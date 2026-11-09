"""HTTP access to OpenRouter: auth, retries, readable errors and SSE parsing."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import aiohttp

from . import config

try:
    from comfy.model_management import throw_exception_if_processing_interrupted as check_interrupt
except ImportError:  # running outside ComfyUI (unit tests)

    def check_interrupt() -> None:
        return None


OFFICIAL_BASE_URL = "https://openrouter.ai/api/v1"
USER_AGENT = "ComfyUI-OpenRouter-Suite"
MAX_RETRIES = 4
MAX_BACKOFF_SECONDS = 30.0
CONNECT_TIMEOUT = 30
JSON_READ_TIMEOUT = 600


class OpenRouterError(RuntimeError):
    """An error whose message is safe and useful to show on the node."""


def base_url() -> str:
    """OpenRouter's API root. The override exists for tests and only accepts loopback,
    so the API key can never be sent anywhere other than openrouter.ai."""
    override = os.environ.get("ORS_TEST_BASE_URL", "")
    if override and urlparse(override).hostname in ("127.0.0.1", "localhost"):
        return override.rstrip("/")
    return OFFICIAL_BASE_URL


def _headers() -> dict[str, str]:
    key = config.get_api_key()
    if not key:
        raise OpenRouterError(
            "No OpenRouter API key. Add one in ComfyUI Settings under OpenRouter, "
            f"or set the {config.ENV_KEY} environment variable."
        )
    return {
        "Authorization": f"Bearer {key}",
        "X-Title": "ComfyUI OpenRouter Suite",
        "User-Agent": USER_AGENT,
    }


def _redact(text: str) -> str:
    key = config.get_api_key()
    return text.replace(key, "[redacted]") if key else text


def describe_error(status: int, body: str) -> str:
    """Turn an error response into one short sentence or two."""
    detail = ""
    try:
        parsed = json.loads(body)
        error = parsed.get("error", parsed) if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
            metadata = error.get("metadata")
            if isinstance(metadata, dict):
                provider = metadata.get("provider_name")
                raw = metadata.get("raw")
                if raw:
                    detail = f"{detail} {raw if isinstance(raw, str) else json.dumps(raw)}"
                if provider:
                    detail = f"[{provider}] {detail}"
        elif isinstance(error, str):
            detail = error
    except ValueError:
        detail = body
    detail = _redact(" ".join(detail.split()))[:600]
    prefix = {
        401: "OpenRouter rejected the API key. Check it in ComfyUI Settings under OpenRouter.",
        402: "Not enough OpenRouter credits for this request.",
        403: "OpenRouter or the provider refused this request (moderation or account policy).",
        404: "OpenRouter could not find that model or resource.",
        408: "The request timed out.",
        413: "The request is too large. Use fewer or smaller inputs.",
        429: "Rate limited by OpenRouter or the provider. Try again shortly.",
    }.get(status) or ("Invalid request." if status == 400 else f"OpenRouter error {status}.")
    return f"{prefix} {detail}".strip()


def should_retry(status: int, path: str, billable: bool) -> bool:
    """Retry only when the request is known not to have been billed."""
    if status == 429:
        return True
    if status == 502 and path == "/images":
        return True  # documented as a failed, unbilled image generation
    return status >= 500 and not billable


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(2.0**attempt, MAX_BACKOFF_SECONDS)


async def sleep_interruptible(seconds: float) -> None:
    remaining = seconds
    while remaining > 0:
        check_interrupt()
        step = min(remaining, 0.25)
        await asyncio.sleep(step)
        remaining -= step
    check_interrupt()


def _raise_if_error_body(payload: Any) -> None:
    """OpenRouter can report a failure inside a 200 response."""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        code = payload["error"].get("code")
        status = code if isinstance(code, int) else 502
        raise OpenRouterError(describe_error(status, json.dumps(payload)))


async def _send(
    method: str,
    path: str,
    read: Callable[[aiohttp.ClientResponse], Awaitable[Any]],
    *,
    body: dict | None = None,
    params: dict | None = None,
    billable: bool = False,
    read_timeout: int = JSON_READ_TIMEOUT,
) -> Any:
    url = base_url() + path
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT, sock_read=read_timeout)
    attempt = 0
    while True:
        check_interrupt()
        retry_after = None
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, json=body, params=params, headers=_headers()) as response:
                    if response.status < 400:
                        return await read(response)
                    text = await response.text()
                    message = describe_error(response.status, text)
                    retriable = should_retry(response.status, path, billable)
                    retry_after = response.headers.get("Retry-After")
        except aiohttp.ClientConnectorError as e:
            # The connection never opened, so nothing was sent or billed.
            message = f"Could not reach OpenRouter: {e.os_error or e}"
            retriable = True
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            retriable = not billable
            message = f"Connection to OpenRouter failed: {type(e).__name__}."
            if billable:
                message += " The request may still have been billed, so it was not retried. Check your OpenRouter activity."
        if not retriable or attempt >= MAX_RETRIES:
            raise OpenRouterError(message)
        await sleep_interruptible(_backoff(attempt, retry_after))
        attempt += 1


async def get_json(path: str, params: dict | None = None) -> Any:
    async def read(response: aiohttp.ClientResponse) -> Any:
        return await response.json(content_type=None)

    payload = await _send("GET", path, read, params=params)
    _raise_if_error_body(payload)
    return payload


async def post_json(path: str, body: dict, *, billable: bool = True) -> Any:
    async def read(response: aiohttp.ClientResponse) -> Any:
        return await response.json(content_type=None)

    payload = await _send("POST", path, read, body=body, billable=billable)
    _raise_if_error_body(payload)
    return payload


async def post_for_bytes(path: str, body: dict) -> tuple[bytes, dict[str, str]]:
    async def read(response: aiohttp.ClientResponse) -> tuple[bytes, dict[str, str]]:
        return await response.read(), dict(response.headers)

    return await _send("POST", path, read, body=body, billable=True)


async def download(path: str, destination: str, params: dict | None = None) -> None:
    async def read(response: aiohttp.ClientResponse) -> None:
        with open(destination, "wb") as f:
            async for chunk in response.content.iter_chunked(1 << 20):
                check_interrupt()
                f.write(chunk)

    await _send("GET", path, read, params=params)


class SSEParser:
    """Incremental Server-Sent Events parser that yields each `data:` payload."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer += chunk.replace(b"\r\n", b"\n")
        events: list[str] = []
        while b"\n\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n\n", 1)
            lines = []
            for line in raw.decode("utf-8", errors="replace").split("\n"):
                if line.startswith(":"):  # keep-alive comment
                    continue
                if line.startswith("data:"):
                    lines.append(line[5:].removeprefix(" "))
            if lines:
                events.append("\n".join(lines))
        return events


async def stream_chat(body: dict) -> AsyncIterator[dict]:
    """POST a streaming chat request and yield each parsed chunk."""
    url = base_url() + "/chat/completions"
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT, sock_read=JSON_READ_TIMEOUT)
    attempt = 0
    while True:
        check_interrupt()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(url, json=body, headers=_headers()) as response:
                    if response.status >= 400:
                        message = describe_error(response.status, await response.text())
                        if response.status == 429 and attempt < MAX_RETRIES:
                            await sleep_interruptible(_backoff(attempt, response.headers.get("Retry-After")))
                            attempt += 1
                            continue
                        raise OpenRouterError(message)
                    parser = SSEParser()
                    async for chunk in response.content.iter_any():
                        check_interrupt()
                        for data in parser.feed(chunk):
                            if data.strip() == "[DONE]":
                                return
                            try:
                                event = json.loads(data)
                            except ValueError:
                                continue
                            _raise_if_error_body(event)
                            yield event
                    return
            except aiohttp.ClientConnectorError as e:
                if attempt >= MAX_RETRIES:
                    raise OpenRouterError(f"Could not reach OpenRouter: {e.os_error or e}") from e
                await sleep_interruptible(_backoff(attempt, None))
                attempt += 1
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                raise OpenRouterError(
                    f"Connection to OpenRouter failed: {type(e).__name__}. "
                    "The request may still have been billed, so it was not retried."
                ) from e
