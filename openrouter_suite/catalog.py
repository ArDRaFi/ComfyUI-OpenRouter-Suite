"""The live model catalog: fetch, normalize, merge, cache and refresh in the background.

`define_schema` reads from this module on every /object_info and validation, so swapping
the in-memory catalog makes new models available without a restart. Nothing here is ever
called with a network dependency from `define_schema` itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.request
from collections.abc import Callable

from . import client, config

log = logging.getLogger("openrouter_suite")

SECTIONS = ("image", "video", "llm", "tts", "stt", "audio")
SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "catalog_snapshot.json")
REFRESH_INTERVAL = 3600
STALE_AFTER = 900
_UNION_KEYS = ("durations", "resolutions", "aspect_ratios", "voices")

_lock = threading.Lock()
_refresh_lock = threading.Lock()
_data: dict = {}
_version = ""
_on_change: Callable[[list[str], str], None] | None = None
_stop = threading.Event()


def _fetch_json(path: str):
    request = urllib.request.Request(client.base_url() + path, headers={"User-Agent": client.USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _architecture_inputs(model: dict) -> list[str]:
    return list((model.get("architecture") or {}).get("input_modalities") or [])


def normalize_image(models: list[dict]) -> dict:
    return {
        m["id"]: {"params": m.get("supported_parameters") or {}, "inputs": _architecture_inputs(m)}
        for m in models
    }


def normalize_video(models: list[dict], listing: list[dict] | None) -> dict:
    modalities = {m["id"]: _architecture_inputs(m) for m in listing or []}
    out = {}
    for m in models:
        out[m["id"]] = {
            "durations": m.get("supported_durations"),
            "resolutions": m.get("supported_resolutions"),
            "aspect_ratios": m.get("supported_aspect_ratios"),
            "frames": m.get("supported_frame_images") or [],
            "inputs": modalities.get(m["id"]) or ["text", "image"],
            "generate_audio": bool(m.get("generate_audio")),
            "seed": bool(m.get("seed")),
            "upscale_factor": m.get("upscale_factor"),
            "creativity": m.get("creativity"),
            "passthrough": m.get("allowed_passthrough_parameters") or [],
        }
    return out


def normalize_llm(models: list[dict]) -> dict:
    out = {}
    for m in models:
        outputs = (m.get("architecture") or {}).get("output_modalities") or []
        if "text" in outputs:
            out[m["id"]] = {"inputs": _architecture_inputs(m), "params": m.get("supported_parameters") or []}
    return out


def normalize_tts(models: list[dict]) -> dict:
    return {m["id"]: {"voices": m.get("supported_voices") or []} for m in models}


def normalize_stt(models: list[dict]) -> dict:
    return {m["id"]: {} for m in models}


def normalize_audio(models: list[dict]) -> dict:
    return {m["id"]: {"inputs": _architecture_inputs(m), "params": m.get("supported_parameters") or []} for m in models}


def fetch_all() -> dict:
    """Fetch each section independently, so one failing endpoint cannot blank the others."""

    def models(path: str) -> list[dict]:
        return _fetch_json(path).get("data") or []

    def video() -> dict:
        try:
            listing = models("/models?output_modalities=video")
        except Exception:
            listing = None
        return normalize_video(models("/videos/models"), listing)

    jobs: dict[str, Callable[[], dict]] = {
        "image": lambda: normalize_image(models("/images/models")),
        "video": video,
        "llm": lambda: normalize_llm(models("/models")),
        "tts": lambda: normalize_tts(models("/models?output_modalities=speech")),
        "stt": lambda: normalize_stt(models("/models?output_modalities=transcription")),
        "audio": lambda: normalize_audio(models("/models?output_modalities=audio")),
    }
    out: dict = {"fetched_at": time.time()}
    for section, job in jobs.items():
        try:
            out[section] = job()
        except Exception as e:
            log.warning("[OpenRouter Suite] could not refresh %s models: %s", section, e)
    return out


def _union_list(new: list, old: list) -> list:
    merged = list(new) + [v for v in old if v not in new]
    return sorted(merged) if merged and all(isinstance(v, int) for v in merged) else merged


def _union_entry(old: dict | None, new: dict) -> dict:
    """Keep option values that have disappeared, so saved workflows still validate."""
    entry = dict(new)
    if not old:
        return entry
    for key in _UNION_KEYS:
        if isinstance(new.get(key), list) and isinstance(old.get(key), list):
            entry[key] = _union_list(new[key], old[key])
    if isinstance(new.get("params"), dict) and isinstance(old.get("params"), dict):
        params = dict(new["params"])
        for name, descriptor in old["params"].items():
            if name not in params:
                continue
            current = params[name]
            if isinstance(current, dict) and isinstance(descriptor, dict) and isinstance(current.get("values"), list) and isinstance(descriptor.get("values"), list):
                params[name] = {**current, "values": _union_list(current["values"], descriptor["values"])}
        entry["params"] = params
    return entry


def merge(old: dict, new: dict) -> tuple[dict, list[str]]:
    """Additive merge. Returns the catalog and the slugs that are newly available."""
    out: dict = {"fetched_at": new.get("fetched_at") or old.get("fetched_at") or 0}
    added: list[str] = []
    for section in SECTIONS:
        previous = old.get(section) or {}
        current = new.get(section)
        if not current:  # fetch failed or returned nothing: keep what we have
            out[section] = previous
            continue
        result = {slug: _union_entry(previous.get(slug), entry) for slug, entry in current.items()}
        if previous:
            added += [s for s in current if s not in previous or previous[s].get("retired")]
        for slug, entry in previous.items():
            if slug not in current:
                result[slug] = {**entry, "retired": True}
        out[section] = dict(sorted(result.items()))
    return out, added


def version_of(data: dict) -> str:
    content = {section: data.get(section) or {} for section in SECTIONS}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_path() -> str:
    return os.path.join(config.user_dir(), "catalog.json")


def _install(data: dict) -> None:
    global _data, _version
    version = version_of(data)
    with _lock:
        _data = data
        _version = version


def load() -> None:
    """Load the newest of the disk cache and the bundled snapshot. Never uses the network."""
    snapshot = _read_json(SNAPSHOT_PATH)
    cache = _read_json(_cache_path())
    older, newer = sorted((snapshot, cache), key=lambda d: d.get("fetched_at") or 0)
    merged, _ = merge(older, newer)
    if not newer:
        merged = older
    _install(merged)


def models(section: str) -> dict:
    return _data.get(section) or {}


def entry(section: str, slug: str) -> dict:
    """Look up a model at run time, failing clearly if it has been retired."""
    info = models(section).get(slug)
    if info is None:
        raise client.OpenRouterError(f"Unknown OpenRouter model: {slug}. Refresh the page to update the model list.")
    if info.get("retired"):
        raise client.OpenRouterError(f"{slug} is no longer available on OpenRouter. Pick another model.")
    return info


def status() -> dict:
    return {
        "version": _version,
        "fetched_at": _data.get("fetched_at") or 0,
        "counts": {section: len(models(section)) for section in SECTIONS},
    }


def refresh(if_stale: bool = False) -> dict:
    """Fetch the catalog and swap it in if it changed. Safe to call from any thread."""
    if if_stale and time.time() - (_data.get("fetched_at") or 0) < STALE_AFTER:
        return {"changed": False, "added": [], **status()}
    if not _refresh_lock.acquire(blocking=False):
        return {"changed": False, "added": [], **status()}
    try:
        fetched = fetch_all()
        merged, added = merge(_data, fetched)
        changed = version_of(merged) != _version
        _install(merged)
        try:
            config.atomic_write_json(_cache_path(), merged)
        except OSError as e:
            log.warning("[OpenRouter Suite] could not save model cache: %s", e)
        if changed and _on_change is not None:
            _on_change(added, _version)
        return {"changed": changed, "added": added, **status()}
    finally:
        _refresh_lock.release()


def _background_loop() -> None:
    delay = 5.0
    while not _stop.wait(delay):
        delay = REFRESH_INTERVAL
        try:
            refresh()
        except Exception as e:
            log.warning("[OpenRouter Suite] model refresh failed: %s", e)


def start_background(on_change: Callable[[list[str], str], None]) -> None:
    global _on_change
    _on_change = on_change
    threading.Thread(target=_background_loop, name="openrouter-suite-catalog", daemon=True).start()
