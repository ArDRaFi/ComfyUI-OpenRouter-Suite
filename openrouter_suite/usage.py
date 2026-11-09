"""Tells the user what each run cost."""

from __future__ import annotations

import logging

from .host import send_event

log = logging.getLogger("openrouter_suite")
_session_total = 0.0


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_cost(cost: float | None, session_total: float) -> str:
    if cost is None:
        spent = "cost n/a"
    else:
        spent = f"${cost:.6f}" if 0 < cost < 0.001 else f"${cost:.4f}"  # keep tiny costs visible
    return f"{spent} (session ${session_total:.2f})"


def report(node_id: str | None, cost) -> None:
    """Shown in the node title by web/cost.js. ComfyUI clears progress text when a job ends."""
    global _session_total
    value = _number(cost)
    if value is not None:
        _session_total += value
    text = format_cost(value, _session_total)
    log.info("[OpenRouter Suite] %s", text)
    send_event("ors.cost", {"node": node_id, "text": text})
