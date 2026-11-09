"""Single import point for everything taken from ComfyUI, to isolate its unstable API."""

from __future__ import annotations

import inspect

import folder_paths
from comfy_api.latest import IO, ComfyExtension, InputImpl, Types
from server import PromptServer

__all__ = ["IO", "ComfyExtension", "InputImpl", "Types", "PromptServer", "folder_paths", "advanced", "fixed_seed_control", "send_event", "send_text"]

_HAS_ADVANCED = "advanced" in inspect.signature(IO.String.Input.__init__).parameters
_CONTROL = getattr(IO, "ControlAfterGenerate", None)


def advanced() -> dict:
    """Keyword arguments that tuck a widget under Advanced, on versions that support it."""
    return {"advanced": True} if _HAS_ADVANCED else {}


def fixed_seed_control() -> dict:
    """Start seeds on 'fixed' so that re-queuing an unchanged paid node does not re-bill.
    Older versions can only default to 'randomize', so they get no control widget."""
    return {"control_after_generate": _CONTROL.fixed} if _CONTROL is not None else {}


def send_event(event: str, data: dict) -> None:
    PromptServer.instance.send_sync(event, data)


def send_text(node_id: str | None, text: str) -> None:
    if node_id is None:
        return
    try:
        PromptServer.instance.send_progress_text(text, node_id)
    except Exception:
        pass
