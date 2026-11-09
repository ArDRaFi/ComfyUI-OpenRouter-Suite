from __future__ import annotations

import base64

from .. import catalog, client, usage, widgets
from ..host import IO
from ..media import audio as audio_media

# Providers stop processing after 60 seconds, so long recordings are sent in pieces.
CHUNK_SECONDS = 300.0
STT_SAMPLE_RATE = 16000


class ORSSpeechToText(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_SpeechToText",
            display_name="ORS Speech to Text",
            category="OpenRouter/Audio",
            description="Transcribe audio with any OpenRouter speech recognition model.",
            inputs=[
                IO.Audio.Input("audio"),
                IO.Combo.Input("model", options=widgets.ordered_slugs("stt"), default=widgets.ordered_slugs("stt")[0], tooltip="OpenRouter transcription model."),
                IO.String.Input("language", default="", optional=True, tooltip="ISO code such as en or ja. Empty detects it automatically."),
                IO.Boolean.Input("timestamps", default=False, optional=True, tooltip="Produce SRT subtitles. Not every model supports this."),
            ],
            outputs=[IO.String.Output("text", display_name="text"), IO.String.Output("srt", display_name="srt")],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, audio: dict, model: str, language: str = "", timestamps: bool = False) -> IO.NodeOutput:
        catalog.entry("stt", model)
        chunks = audio_media.split(audio, CHUNK_SECONDS) if audio_media.duration_seconds(audio) > CHUNK_SECONDS else [(audio, 0.0)]

        texts: list[str] = []
        segments: list[dict] = []
        total_cost = 0.0
        has_cost = False
        for chunk, start in chunks:
            encoded = audio_media.encode(chunk, rate=STT_SAMPLE_RATE, mono=True, bitrate=64000)
            body: dict = {"model": model, "input_audio": {"data": base64.b64encode(encoded).decode(), "format": "mp3"}}
            if language and language.strip():
                body["language"] = language.strip()
            if timestamps:
                body["response_format"] = "verbose_json"
                body["timestamp_granularities"] = ["segment"]
            response = await client.post_json("/audio/transcriptions", body)
            text = (response.get("text") or "").strip()
            if text:
                texts.append(text)
            for segment in response.get("segments") or []:
                segments.append({**segment, "start": segment["start"] + start, "end": segment["end"] + start})
            cost = (response.get("usage") or {}).get("cost")
            if cost is not None:
                total_cost += float(cost)
                has_cost = True

        usage.report(cls.hidden.unique_id, total_cost if has_cost else None)
        return IO.NodeOutput(" ".join(texts), audio_media.to_srt(segments))
