"""Server routes used by the browser extensions. None of them ever return the API key."""

from __future__ import annotations

import asyncio

from aiohttp import web

from . import catalog, config
from .host import PromptServer

_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    routes = PromptServer.instance.routes

    @routes.get("/openrouter_suite/status")
    async def status(request: web.Request) -> web.Response:
        return web.json_response({"key": config.key_status(), "catalog": catalog.status()})

    @routes.post("/openrouter_suite/key")
    async def set_key(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        key = payload.get("key") if isinstance(payload, dict) else None
        if not isinstance(key, str):
            return web.json_response({"error": "Missing key"}, status=400)
        config.set_api_key(key)
        return web.json_response({"key": config.key_status()})

    @routes.post("/openrouter_suite/refresh")
    async def refresh(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        if_stale = bool(payload.get("if_stale")) if isinstance(payload, dict) else False
        result = await asyncio.get_running_loop().run_in_executor(None, catalog.refresh, if_stale)
        return web.json_response(result)


def notify_catalog_changed(added: list[str], version: str) -> None:
    """Called from the refresh thread. send_sync is safe to use from other threads."""
    PromptServer.instance.send_sync("ors.catalog", {"added": added, "version": version})
