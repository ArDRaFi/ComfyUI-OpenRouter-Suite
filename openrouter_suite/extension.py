from __future__ import annotations

from typing_extensions import override

from . import catalog, routes
from .host import IO, ComfyExtension
from .nodes.audio_gen import ORSAudioGenerate
from .nodes.image import ORSImage
from .nodes.llm import ORSLLM
from .nodes.speech import ORSTextToSpeech
from .nodes.transcribe import ORSSpeechToText
from .nodes.video import ORSVideo, ORSVideoJob


class OpenRouterSuite(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [ORSImage, ORSVideo, ORSVideoJob, ORSLLM, ORSTextToSpeech, ORSSpeechToText, ORSAudioGenerate]


async def comfy_entrypoint() -> OpenRouterSuite:
    catalog.load()
    routes.register()
    catalog.start_background(routes.notify_catalog_changed)
    return OpenRouterSuite()
