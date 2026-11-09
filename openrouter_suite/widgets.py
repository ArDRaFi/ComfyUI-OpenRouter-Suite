"""Builds ComfyUI inputs from model capabilities. Runs on every /object_info and validation."""

from __future__ import annotations

from . import capabilities as caps
from . import catalog
from .host import IO, advanced, fixed_seed_control

_MAX_SEED = 2147483647
_PUBLIC_UPLOAD_NOTE = "Uploaded to a temporary public host, because OpenRouter needs a public link."
_PREFERRED_VALUES = {
    "aspect_ratio": ("1:1", "auto"),
    "resolution": ("1K", "1024", "2K"),
    "quality": ("auto", "high"),
    "background": ("auto",),
    "output_format": ("png",),
}
_PREFERRED_MODELS = {
    "image": ("google/gemini-3-pro-image", "openai/gpt-image-2"),
    "video": ("google/veo-3.1-fast", "kwaivgi/kling-v3.0-std"),
    "tts": ("hexgrad/kokoro-82m", "elevenlabs/eleven-v3"),
    "stt": ("openai/whisper-1",),
    "llm": ("openai/gpt-5.6-luna", "anthropic/claude-sonnet-5", "google/gemini-3.5-flash"),
    "audio": ("google/lyria-3-clip-preview",),
}


def _prefer(values: list, preferred: tuple):
    return next((p for p in preferred if p in values), values[0])


def prompt_input(name: str = "prompt", tooltip: str = "What to generate.") -> IO.String.Input:
    return IO.String.Input(name, multiline=True, default="", tooltip=tooltip)


def seed_input() -> IO.Int.Input:
    return IO.Int.Input(
        "seed",
        default=0,
        min=0,
        max=_MAX_SEED,
        tooltip="Sent to models that support seeds. Changing it re-runs the node on any model.",
        **fixed_seed_control(),
    )


def slots(group: str, io_type, prefix: str, count: int, label: str, minimum: int = 0) -> IO.Autogrow.Input:
    names = [f"{prefix}_{i}" for i in range(1, count + 1)]
    return IO.Autogrow.Input(
        group,
        template=IO.Autogrow.TemplateNames(io_type.Input(prefix), names=names, min=minimum),
        tooltip=f"{label}: up to {count} for this model.",
    )


def ordered_slugs(section: str, keep=lambda entry: True) -> list[str]:
    """All slugs for a section with a sensible default first, and retired models last."""
    entries = catalog.models(section)
    active = [s for s, e in entries.items() if keep(e) and not e.get("retired")]
    retired = [s for s, e in entries.items() if keep(e) and e.get("retired")]
    if active:
        first = _prefer(active, _PREFERRED_MODELS.get(section, ()))
        active.remove(first)
        active.insert(0, first)
    return (active + retired) or ["(no models available)"]


def _param_input(param: caps.Param):
    if param.kind == "enum":
        default = param.values[0]
        if param.name in _PREFERRED_VALUES:
            default = _prefer(param.values, _PREFERRED_VALUES[param.name])
        return IO.Combo.Input(param.name, options=param.values, default=default)
    if param.kind == "bool":
        return IO.Boolean.Input(param.name, default=False)
    extra = {"optional": True, **advanced()} if param.name == "output_compression" else {}
    default = param.maximum if param.name == "output_compression" else param.minimum
    if param.kind == "int":
        return IO.Int.Input(param.name, default=int(default), min=int(param.minimum), max=int(param.maximum), **extra)
    return IO.Float.Input(param.name, default=float(default), min=float(param.minimum), max=float(param.maximum), **extra)


def image_options() -> list[IO.DynamicCombo.Option]:
    entries = catalog.models("image")
    options = []
    for slug in ordered_slugs("image", lambda e: not caps.is_vector_only(e)):
        if slug not in entries:
            options.append(IO.DynamicCombo.Option(slug, []))
            continue
        capability = caps.image_caps(slug, entries[slug])
        inputs = []
        if capability.max_refs:
            inputs.append(slots("images", IO.Image, "image", capability.max_refs, "Reference images", capability.min_refs))
        inputs += [_param_input(p) for p in capability.params]
        options.append(IO.DynamicCombo.Option(slug, inputs))
    return options


def _video_inputs(capability: caps.VideoCaps) -> list:
    """Kept to one level on purpose: the frontend loses links on doubly nested sockets
    when a saved workflow is reopened."""
    inputs = []
    if capability.source_video:
        inputs.append(IO.Video.Input("video", tooltip=f"The video to edit or upscale. {_PUBLIC_UPLOAD_NOTE}"))
    if capability.first_frame:
        inputs.append(IO.Image.Input("first_frame", optional=True, tooltip="Start the video on this image."))
    if capability.last_frame:
        inputs.append(IO.Image.Input("last_frame", optional=True, tooltip="End the video on this image. Needs a first frame."))
    if capability.ref_images:
        inputs.append(slots("images", IO.Image, "image", capability.ref_images, "Reference images"))
    if capability.ref_videos:
        inputs.append(slots("videos", IO.Video, "video", capability.ref_videos, f"{_PUBLIC_UPLOAD_NOTE} Reference videos"))
    if capability.ref_audios:
        inputs.append(slots("audios", IO.Audio, "audio", capability.ref_audios, f"{_PUBLIC_UPLOAD_NOTE} Reference audio"))
    return inputs


def video_options() -> list[IO.DynamicCombo.Option]:
    entries = catalog.models("video")
    options = []
    for slug in ordered_slugs("video"):
        if slug not in entries:
            options.append(IO.DynamicCombo.Option(slug, []))
            continue
        entry = entries[slug]
        inputs = _video_inputs(caps.video_caps(slug, entry))
        if entry.get("durations"):
            durations = entry["durations"]
            inputs.append(IO.Combo.Input("duration", options=durations, default=_prefer(durations, (5, 6, 8)), tooltip="Length in seconds."))
        if entry.get("resolutions"):
            inputs.append(IO.Combo.Input("resolution", options=entry["resolutions"], default=_prefer(entry["resolutions"], ("720p", "1080p"))))
        if entry.get("aspect_ratios"):
            inputs.append(IO.Combo.Input("aspect_ratio", options=entry["aspect_ratios"], default=_prefer(entry["aspect_ratios"], ("16:9",))))
        if entry.get("generate_audio"):
            inputs.append(IO.Boolean.Input("generate_audio", default=True, tooltip="Generate sound with the video."))
        upscale = entry.get("upscale_factor")
        if isinstance(upscale, dict):
            low, high = float(upscale.get("min", 1)), float(upscale.get("max", 4))
            inputs.append(IO.Float.Input("upscale_factor", default=low, min=low, max=high, step=0.1))
        creativity = entry.get("creativity")
        if isinstance(creativity, list) and creativity:
            inputs.append(IO.Combo.Input("creativity", options=creativity, default=creativity[0]))
        if entry.get("passthrough"):
            allowed = ", ".join(entry["passthrough"])
            tooltip = f'Optional JSON keyed by provider, such as {{"byteplus": {{"watermark": false}}}}. Allowed settings: {allowed}'
            inputs.append(IO.String.Input("provider_options", default="", optional=True, tooltip=tooltip, **advanced()))
        options.append(IO.DynamicCombo.Option(slug, inputs))
    return options


def tts_options() -> list[IO.DynamicCombo.Option]:
    entries = catalog.models("tts")
    options = []
    for slug in ordered_slugs("tts"):
        voices = (entries.get(slug) or {}).get("voices") or []
        if voices:
            voice = IO.Combo.Input("voice", options=voices, default=voices[0], tooltip="Voices available for this model.")
        else:
            voice = IO.String.Input("voice", default="", tooltip="Voice ID. Leave empty if the model has a default voice.")
        options.append(IO.DynamicCombo.Option(slug, [voice]))
    return options
