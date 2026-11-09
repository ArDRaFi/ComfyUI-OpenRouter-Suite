from __future__ import annotations

import asyncio
import base64
import re

from .. import catalog, client, usage, widgets
from ..host import IO, advanced
from ..media import audio as audio_media

_RATE = re.compile(r"rate=(\d+)")


async def _generation_cost(generation_id: str | None) -> float | None:
    """Speech returns raw audio, so its cost is looked up afterwards. Best effort only."""
    if not generation_id:
        return None
    for delay in (1, 2, 3):
        await asyncio.sleep(delay)
        try:
            data = (await client.get_json("/generation", params={"id": generation_id})).get("data") or {}
        except client.OpenRouterError:
            continue
        if data.get("total_cost") is not None:
            return data["total_cost"]
    return None


def _decode(data: bytes, headers: dict) -> dict:
    content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    if "pcm" in content_type or not audio_media.sniff_container(data):
        match = _RATE.search(content_type)
        return audio_media.pcm16_to_audio(data, int(match.group(1)) if match else audio_media.DEFAULT_PCM_RATE)
    return audio_media.decode_bytes(data)


class ORSTextToSpeech(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_TextToSpeech",
            display_name="ORS Text to Speech",
            category="OpenRouter/Audio",
            description="Turn text into speech with any OpenRouter voice model.",
            inputs=[
                widgets.prompt_input("text", "The words to speak."),
                IO.DynamicCombo.Input("model", options=widgets.tts_options(), tooltip="OpenRouter speech model."),
                IO.String.Input("instructions", multiline=True, default="", optional=True, tooltip="Tone, pacing or emotion. Only some models use this.", **advanced()),
                IO.Float.Input("speed", default=1.0, min=0.25, max=4.0, step=0.05, optional=True, tooltip="Only some models support this.", **advanced()),
                IO.Audio.Input("reference_audio", optional=True, tooltip="A voice sample to clone, on models that support it."),
            ],
            outputs=[IO.Audio.Output()],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, text: str, model: dict, instructions: str = "", speed: float = 1.0, reference_audio: dict | None = None) -> IO.NodeOutput:
        slug = model["model"]
        catalog.entry("tts", slug)
        if not text.strip():
            raise client.OpenRouterError("Enter some text.")

        body: dict = {"model": slug, "input": text, "response_format": "mp3"}
        if model.get("voice"):
            body["voice"] = model["voice"]
        if instructions and instructions.strip():
            body["instructions"] = instructions
        if speed != 1.0:
            body["speed"] = speed
        if reference_audio is not None:
            encoded = base64.b64encode(audio_media.encode(reference_audio)).decode()
            body["input_references"] = [{"type": "input_audio", "input_audio": {"data": f"data:audio/mpeg;base64,{encoded}", "format": "mp3"}}]

        try:
            data, headers = await client.post_for_bytes("/audio/speech", body)
        except client.OpenRouterError as e:
            # Some models only produce PCM. A rejected request is not billed.
            if not str(e).startswith("Invalid request"):
                raise
            body["response_format"] = "pcm"
            data, headers = await client.post_for_bytes("/audio/speech", body)

        audio = _decode(data, headers)
        generation_id = next((v for k, v in headers.items() if k.lower() == "x-generation-id"), None)
        usage.report(cls.hidden.unique_id, await _generation_cost(generation_id))
        return IO.NodeOutput(audio)
