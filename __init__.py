"""ComfyUI-OpenRouter-Suite: OpenRouter image, video, LLM and audio models in ComfyUI."""

from .openrouter_suite.extension import comfy_entrypoint

WEB_DIRECTORY = "./web"

__all__ = ["comfy_entrypoint", "WEB_DIRECTORY"]
