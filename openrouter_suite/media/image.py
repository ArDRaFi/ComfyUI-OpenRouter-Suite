"""Convert between ComfyUI IMAGE tensors and the data OpenRouter sends and receives."""

from __future__ import annotations

import base64
import io

import numpy as np
import torch
from PIL import Image

from ..client import OpenRouterError

MAX_REFERENCE_PIXELS = 2048 * 2048
MAX_PNG_BYTES = 4 * 1024 * 1024


def collect_slots(slots: dict | None) -> list[torch.Tensor]:
    """Flatten connected slots into single images, so a batch counts once per image."""
    images: list[torch.Tensor] = []
    for batch in (slots or {}).values():
        if batch is not None:
            images.extend(frame for frame in batch)
    return images


def _to_pil(image: torch.Tensor) -> Image.Image:
    array = (image.clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
    return Image.fromarray(array, "RGBA" if array.shape[-1] == 4 else "RGB")


def _limit_pixels(picture: Image.Image, max_pixels: int) -> Image.Image:
    pixels = picture.width * picture.height
    if pixels <= max_pixels:
        return picture
    scale = (max_pixels / pixels) ** 0.5
    return picture.resize((max(1, int(picture.width * scale)), max(1, int(picture.height * scale))), Image.Resampling.LANCZOS)


def to_data_url(image: torch.Tensor, max_pixels: int = MAX_REFERENCE_PIXELS) -> str:
    """Encode as PNG, falling back to high-quality JPEG when PNG would be very large."""
    picture = _limit_pixels(_to_pil(image), max_pixels)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    mime = "image/png"
    if buffer.tell() > MAX_PNG_BYTES and picture.mode == "RGB":
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=95, subsampling=0)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buffer.getvalue()).decode()}"


def decode_results(items: list[dict]) -> tuple[torch.Tensor, torch.Tensor]:
    """Turn `data[].b64_json` items into an IMAGE batch and its MASK batch."""
    pictures: list[Image.Image] = []
    for item in items:
        encoded = item.get("b64_json")
        if not encoded:
            continue
        raw = base64.b64decode(encoded)
        if raw.lstrip()[:5] in (b"<svg ", b"<?xml"):
            raise OpenRouterError("This model returned an SVG, which cannot be used as an IMAGE output.")
        try:
            pictures.append(Image.open(io.BytesIO(raw)))
            pictures[-1].load()
        except Exception as e:
            raise OpenRouterError(f"OpenRouter returned an image that could not be decoded: {e}") from e
    if not pictures:
        raise OpenRouterError("OpenRouter returned no image.")

    size = pictures[0].size
    rgb_frames, mask_frames = [], []
    for picture in pictures:
        has_alpha = picture.mode in ("RGBA", "LA") or "transparency" in picture.info
        converted = picture.convert("RGBA" if has_alpha else "RGB")
        if converted.size != size:  # one batch needs one size
            converted = converted.resize(size, Image.Resampling.LANCZOS)
        array = np.asarray(converted, dtype=np.float32) / 255.0
        rgb_frames.append(torch.from_numpy(array[..., :3].copy()))
        if has_alpha:
            mask_frames.append(1.0 - torch.from_numpy(array[..., 3].copy()))
        else:
            mask_frames.append(torch.zeros(size[1], size[0], dtype=torch.float32))
    return torch.stack(rgb_frames), torch.stack(mask_frames)
