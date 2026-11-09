"""Works out what each model can really accept, from catalog data plus model_overrides.json."""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field

OVERRIDES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "model_overrides.json")

# Used when the API does not publish a limit and no override exists, so new models still work.
DEFAULT_REF_IMAGES = 4
DEFAULT_REF_VIDEOS = 1
DEFAULT_REF_AUDIOS = 1

# Image parameters handled by dedicated widgets, or not meant for users.
_IMAGE_NON_WIDGET = {"input_references", "seed", "size", "stream", "markup", "frame_count", "frame_duration_ms"}
_UNSUPPORTED_FORMATS = {"gif", "svg"}


@dataclass
class Param:
    name: str
    kind: str  # enum | int | float | bool
    values: list = field(default_factory=list)
    minimum: float = 0
    maximum: float = 0


@dataclass
class ImageCaps:
    params: list[Param]
    max_refs: int
    min_refs: int
    seed: bool


@dataclass
class VideoCaps:
    first_frame: bool
    last_frame: bool
    source_video: bool
    ref_images: int
    ref_videos: int
    ref_audios: int
    needs_input: bool


_overrides_cache: dict | None = None


def _load_overrides() -> dict:
    global _overrides_cache
    if _overrides_cache is None:
        try:
            with open(OVERRIDES_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        _overrides_cache = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
    return _overrides_cache


def overrides_for(slug: str, table: dict | None = None) -> dict:
    """Merge every matching override. Later entries in the file win."""
    merged: dict = {}
    for pattern, values in (_load_overrides() if table is None else table).items():
        if fnmatch.fnmatchcase(slug, pattern):
            merged.update(values)
    return merged


def _param(name: str, descriptor: dict) -> Param | None:
    kind = descriptor.get("type")
    if kind == "enum":
        values = [v for v in descriptor.get("values") or [] if v not in _UNSUPPORTED_FORMATS]
        return Param(name, "enum", values) if values else None
    if kind == "boolean":
        return Param(name, "bool")
    if kind == "range":
        low, high = descriptor.get("min", 0), descriptor.get("max", 0)
        whole = isinstance(low, int) and isinstance(high, int)
        return Param(name, "int" if whole else "float", minimum=low, maximum=high)
    return None


def is_vector_only(entry: dict) -> bool:
    """SVG-only models cannot produce an IMAGE output, so they are left out of v1."""
    output_format = entry.get("params", {}).get("output_format") or {}
    values = output_format.get("values") or []
    return bool(values) and all(v in _UNSUPPORTED_FORMATS for v in values)


def image_caps(slug: str, entry: dict, table: dict | None = None) -> ImageCaps:
    params = entry.get("params") or {}
    widgets = []
    for name, descriptor in params.items():
        if name not in _IMAGE_NON_WIDGET and isinstance(descriptor, dict):
            param = _param(name, descriptor)
            if param is not None:
                widgets.append(param)
    references = params.get("input_references")
    override = overrides_for(slug, table)
    min_refs = 0
    if isinstance(references, dict):
        max_refs = int(references.get("max") or 0)
        min_refs = int(references.get("min") or 0)
    elif not params and "image" in (entry.get("inputs") or []):
        max_refs = DEFAULT_REF_IMAGES  # the API lists image input but publishes no parameters
    else:
        max_refs = 0
    max_refs = int(override.get("ref_images", max_refs))
    return ImageCaps(widgets, max_refs, min(min_refs, max_refs), "seed" in params)


def video_caps(slug: str, entry: dict, table: dict | None = None) -> VideoCaps:
    inputs = set(entry.get("inputs") or [])
    frames = entry.get("frames") or []
    override = overrides_for(slug, table)
    needs_input = not frames and entry.get("durations") is None
    is_edit = needs_input and "video" in inputs
    return VideoCaps(
        first_frame="first_frame" in frames,
        last_frame="first_frame" in frames and "last_frame" in frames,
        source_video=is_edit,
        ref_images=int(override.get("ref_images", DEFAULT_REF_IMAGES if "image" in inputs else 0)),
        ref_videos=0 if is_edit else int(override.get("ref_videos", DEFAULT_REF_VIDEOS if "video" in inputs else 0)),
        ref_audios=int(override.get("ref_audios", DEFAULT_REF_AUDIOS if "audio" in inputs else 0)),
        needs_input=needs_input,
    )
