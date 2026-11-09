from __future__ import annotations

import base64
import json
import os
import time

from .. import catalog, client, usage, widgets
from ..host import IO, advanced, folder_paths, send_text
from ..media import audio as audio_media
from ..media import image as image_media
from ..media import video as video_media

MAX_IMAGES = 20
MAX_VIDEOS = 4
MAX_AUDIOS = 4
_NO_PDF = "none"
_TEXT_REFRESH_SECONDS = 0.25


def _pdf_options() -> list[str]:
    try:
        names = [n for n in os.listdir(folder_paths.get_input_directory()) if n.lower().endswith(".pdf")]
    except OSError:
        names = []
    return [_NO_PDF, *sorted(names)]


def _pdf_part(name: str) -> dict:
    # Only a bare file name inside ComfyUI's input folder is accepted.
    path = os.path.join(folder_paths.get_input_directory(), os.path.basename(name))
    try:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
    except OSError as e:
        raise client.OpenRouterError(f"Could not read {name}: {e}") from e
    return {"type": "file", "file": {"filename": os.path.basename(name), "file_data": f"data:application/pdf;base64,{encoded}"}}


def response_format(text: str) -> dict | None:
    """Empty means plain text, 'json' means any JSON object, otherwise a JSON Schema."""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() == "json":
        return {"type": "json_object"}
    try:
        schema = json.loads(text)
    except ValueError as e:
        raise client.OpenRouterError(f"json_schema must be 'json' or a valid JSON Schema: {e}") from e
    wrapped = schema if "schema" in schema else {"name": "output", "strict": True, "schema": schema}
    return {"type": "json_schema", "json_schema": wrapped}


def _require(slug: str, inputs: list, kind: str, label: str, count: int) -> None:
    if count and kind not in inputs:
        raise client.OpenRouterError(f"{slug} does not accept {label} input.")


class ORSLLM(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_LLM",
            display_name="ORS LLM",
            category="OpenRouter/Text",
            description="Chat with any OpenRouter LLM, with optional image, video, audio or PDF input.",
            inputs=[
                widgets.prompt_input(tooltip="Your message to the model."),
                IO.String.Input("system_prompt", multiline=True, default="", optional=True, tooltip="Instructions for how the model should behave."),
                IO.Combo.Input("model", options=widgets.ordered_slugs("llm"), default=widgets.ordered_slugs("llm")[0], tooltip="OpenRouter model."),
                widgets.slots("images", IO.Image, "image", MAX_IMAGES, "Images"),
                widgets.slots("videos", IO.Video, "video", MAX_VIDEOS, "Videos"),
                widgets.slots("audios", IO.Audio, "audio", MAX_AUDIOS, "Audio clips"),
                IO.Float.Input("temperature", default=0.7, min=0.0, max=2.0, step=0.05),
                IO.Int.Input("max_tokens", default=4096, min=0, max=1000000, tooltip="0 leaves it to the provider's default."),
                widgets.seed_input(),
                IO.Combo.Input("reasoning_effort", options=["default", "none", "low", "medium", "high"], default="default", optional=True, tooltip="'default' leaves it to the model. Reasoning counts toward max_tokens.", **advanced()),
                IO.Float.Input("top_p", default=1.0, min=0.0, max=1.0, step=0.01, optional=True, **advanced()),
                IO.String.Input("json_schema", multiline=True, default="", optional=True, tooltip="Empty for text, 'json' for any JSON object, or a JSON Schema.", **advanced()),
                IO.Boolean.Input("web_search", default=False, optional=True, **advanced()),
                IO.Combo.Input("pdf", options=_pdf_options(), default=_NO_PDF, optional=True, tooltip="A PDF from ComfyUI's input folder.", **advanced()),
            ],
            outputs=[IO.String.Output()],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(
        cls,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        seed: int,
        images: dict | None = None,
        videos: dict | None = None,
        audios: dict | None = None,
        system_prompt: str = "",
        reasoning_effort: str = "default",
        top_p: float = 1.0,
        json_schema: str = "",
        web_search: bool = False,
        pdf: str = _NO_PDF,
    ) -> IO.NodeOutput:
        entry = catalog.entry("llm", model)
        inputs, params = entry.get("inputs") or [], entry.get("params") or []
        if not prompt.strip():
            raise client.OpenRouterError("Enter a prompt.")

        image_frames = image_media.collect_slots(images)
        video_clips = [v for v in (videos or {}).values() if v is not None]
        audio_clips = [a for a in (audios or {}).values() if a is not None]
        _require(model, inputs, "image", "image", len(image_frames))
        _require(model, inputs, "video", "video", len(video_clips))
        _require(model, inputs, "audio", "audio", len(audio_clips))
        use_pdf = bool(pdf) and pdf != _NO_PDF
        _require(model, inputs, "file", "PDF", int(use_pdf))

        content: list[dict] = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": image_media.to_data_url(f)}} for f in image_frames]
        content += [{"type": "video_url", "video_url": {"url": video_media.to_data_url(v)}} for v in video_clips]
        content += [
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio_media.encode(a)).decode(), "format": "mp3"}}
            for a in audio_clips
        ]
        if use_pdf:
            content.append(_pdf_part(pdf))

        messages: list[dict] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content if len(content) > 1 else prompt})

        body: dict = {"model": model, "messages": messages, "stream": True, "usage": {"include": True}}
        # Send only what this model supports. Others would be ignored or rejected.
        if "temperature" in params:
            body["temperature"] = temperature
        if "top_p" in params and top_p < 1.0:
            body["top_p"] = top_p
        if "seed" in params:
            body["seed"] = seed
        if max_tokens > 0:
            body["max_tokens"] = max_tokens
        if reasoning_effort != "default" and "reasoning" in params:
            body["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        output_format = response_format(json_schema)
        if output_format:
            if "response_format" not in params:
                raise client.OpenRouterError(f"{model} does not support JSON output.")
            body["response_format"] = output_format
        if web_search:
            if "web_search_options" not in params:
                raise client.OpenRouterError(f"{model} does not support web search.")
            body["web_search_options"] = {}

        node_id = cls.hidden.unique_id
        parts: list[str] = []
        cost = None
        finish_reason = None
        last_update = 0.0
        async for event in client.stream_chat(body):
            if isinstance(event.get("usage"), dict) and event["usage"].get("cost") is not None:
                cost = event["usage"]["cost"]
            for choice in event.get("choices") or []:
                finish_reason = choice.get("finish_reason") or finish_reason
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    parts.append(piece)
            now = time.monotonic()
            if parts and now - last_update >= _TEXT_REFRESH_SECONDS:
                send_text(node_id, "".join(parts)[-2000:])
                last_update = now

        text = "".join(parts)
        if not text:
            hint = " Reasoning may have used up max_tokens. Try raising it." if finish_reason == "length" else ""
            raise client.OpenRouterError(f"The model returned no text.{hint}")
        usage.report(node_id, cost)
        return IO.NodeOutput(text)
