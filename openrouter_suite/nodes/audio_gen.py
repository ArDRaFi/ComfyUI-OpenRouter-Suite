from __future__ import annotations

import base64

from .. import catalog, client, usage, widgets
from ..host import IO, advanced
from ..media import audio as audio_media

_OPENAI_STREAM_RATE = 24000


class ORSAudioGenerate(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_AudioGenerate",
            display_name="ORS Audio Generate",
            category="OpenRouter/Audio",
            description="Generate music or spoken replies with OpenRouter audio models.",
            inputs=[
                widgets.prompt_input(tooltip="The music or reply to generate."),
                IO.Combo.Input("model", options=widgets.ordered_slugs("audio"), default=widgets.ordered_slugs("audio")[0], tooltip="OpenRouter audio model."),
                IO.String.Input("voice", default="", optional=True, tooltip="Voice for spoken replies, such as alloy. Not used for music.", **advanced()),
                widgets.seed_input(),
            ],
            outputs=[IO.Audio.Output(), IO.String.Output("text", display_name="text")],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, prompt: str, model: str, seed: int, voice: str = "") -> IO.NodeOutput:
        entry = catalog.entry("audio", model)
        if not prompt.strip():
            raise client.OpenRouterError("Enter a prompt.")

        is_openai = model.startswith("openai/")
        audio_config: dict = {"format": "pcm16" if is_openai else "wav"}
        if voice and voice.strip():
            audio_config["voice"] = voice.strip()
        elif is_openai:
            audio_config["voice"] = "alloy"
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["text", "audio"],
            "audio": audio_config,
            "stream": True,
            "usage": {"include": True},
        }
        if "seed" in (entry.get("params") or []):
            body["seed"] = seed

        chunks: list[bytes] = []
        texts: list[str] = []
        cost = None
        async for event in client.stream_chat(body):
            if isinstance(event.get("usage"), dict) and event["usage"].get("cost") is not None:
                cost = event["usage"]["cost"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    texts.append(delta["content"])
                part = delta.get("audio") or {}
                if part.get("data"):
                    chunks.append(base64.b64decode(part["data"]))
                if part.get("transcript"):
                    texts.append(part["transcript"])

        data = b"".join(chunks)
        if not data:
            raise client.OpenRouterError("The model returned no audio.")
        audio = audio_media.decode_bytes(data) if audio_media.sniff_container(data) else audio_media.pcm16_to_audio(data, _OPENAI_STREAM_RATE)
        usage.report(cls.hidden.unique_id, cost)
        return IO.NodeOutput(audio, "".join(texts))
