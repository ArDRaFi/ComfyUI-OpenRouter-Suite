"""Convert ComfyUI VIDEO inputs to data URLs, and downloaded jobs to VIDEO outputs."""

from __future__ import annotations

import base64
import io
import os
import uuid

from .. import client
from ..host import InputImpl, Types, folder_paths


def to_mp4_bytes(video) -> bytes:
    buffer = io.BytesIO()
    video.save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
    return buffer.getvalue()


def to_data_url(video) -> str:
    return f"data:video/mp4;base64,{base64.b64encode(to_mp4_bytes(video)).decode()}"


async def download_job(job_id: str):
    """Download a finished job's first output and return it as a VIDEO."""
    directory = folder_paths.get_temp_directory()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"openrouter_{uuid.uuid4().hex}.mp4")
    await client.download(f"/videos/{job_id}/content", path, params={"index": 0})
    return InputImpl.VideoFromFile(path)
