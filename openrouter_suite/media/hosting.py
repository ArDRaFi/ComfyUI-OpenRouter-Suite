"""Temporary public hosting for video and audio references.

OpenRouter only accepts these references as public HTTPS links, and its Files API cannot
hold video. Connected files are therefore uploaded to a third-party temporary host. The
OpenRouter key is never sent to these hosts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import aiohttp

from .. import client

log = logging.getLogger("openrouter_suite")
_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)


def parse_text_url(text: str) -> str:
    return text.strip()


def parse_uguu(text: str) -> str:
    return json.loads(text)["files"][0]["url"]


@dataclass(frozen=True)
class Host:
    name: str
    url: str
    file_field: str
    parse: Callable[[str], str]
    fields: dict = field(default_factory=dict)


HOSTS = (
    Host(
        "litterbox.catbox.moe",
        "https://litterbox.catbox.moe/resources/internals/api.php",
        "fileToUpload",
        parse_text_url,
        {"reqtype": "fileupload", "time": "1h"},
    ),
    Host("uguu.se", "https://uguu.se/upload", "files[]", parse_uguu),
)


def _active_hosts() -> tuple[Host, ...]:
    base = client.base_url()
    if base != client.OFFICIAL_BASE_URL:  # tests only, and base_url() only allows loopback
        return (Host("test", f"{base}/__upload", "fileToUpload", parse_text_url),)
    return HOSTS


async def upload(data: bytes, extension: str, mime: str) -> str:
    """Upload bytes under a random name, and return a public HTTPS link. Tries each host."""
    failures: list[str] = []
    for host in _active_hosts():
        client.check_interrupt()
        form = aiohttp.FormData()
        for name, value in host.fields.items():
            form.add_field(name, value)
        form.add_field(host.file_field, data, filename=f"{uuid.uuid4().hex}.{extension}", content_type=mime)
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT, headers={"User-Agent": client.USER_AGENT}) as session:
                async with session.post(host.url, data=form) as response:
                    text = await response.text()
                    if response.status >= 400:
                        failures.append(f"{host.name}: HTTP {response.status}")
                        continue
            url = host.parse(text)
            if urlparse(url).scheme != "https":
                failures.append(f"{host.name}: unexpected response")
                continue
            log.info("[OpenRouter Suite] uploaded a reference to %s", host.name)
            return url
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, IndexError, TypeError) as e:
            failures.append(f"{host.name}: {type(e).__name__}")
    raise client.OpenRouterError(f"Could not upload the reference file to a temporary host ({'; '.join(failures)}). Try again shortly.")
