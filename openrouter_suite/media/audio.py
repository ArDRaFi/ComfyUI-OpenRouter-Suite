"""Convert between ComfyUI AUDIO dicts and encoded audio bytes, using PyAV."""

from __future__ import annotations

import io

import av
import numpy as np
import torch

from ..client import OpenRouterError

_MP3_RATES = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000}
DEFAULT_PCM_RATE = 24000


def decode_bytes(data: bytes) -> dict:
    """Decode any common audio container into {"waveform": [B, C, T], "sample_rate": int}."""
    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise OpenRouterError("The response contained no audio.")
            stream = container.streams.audio[0]
            rate = int(stream.codec_context.sample_rate)
            channels = stream.codec_context.channels or 1
            layout = "stereo" if channels >= 2 else "mono"
            resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
            parts: list[np.ndarray] = []
            for frame in container.decode(stream):
                parts += [f.to_ndarray() for f in resampler.resample(frame)]
            parts += [f.to_ndarray() for f in resampler.resample(None)]
    except av.error.FFmpegError as e:
        raise OpenRouterError(f"The returned audio could not be decoded: {e}") from e
    if not parts:
        raise OpenRouterError("The response contained no audio.")
    waveform = torch.from_numpy(np.concatenate(parts, axis=1).astype(np.float32))
    return {"waveform": waveform.unsqueeze(0), "sample_rate": rate}


def pcm16_to_audio(data: bytes, sample_rate: int = DEFAULT_PCM_RATE) -> dict:
    samples = np.frombuffer(data[: len(data) - len(data) % 2], dtype="<i2").astype(np.float32) / 32768.0
    return {"waveform": torch.from_numpy(samples.copy()).reshape(1, 1, -1), "sample_rate": sample_rate}


def sniff_container(data: bytes) -> bool:
    """True when the bytes look like a real audio file rather than headerless PCM."""
    head = data[:12]
    return (
        head[:4] in (b"RIFF", b"fLaC", b"OggS")
        or head[:3] == b"ID3"
        or head[4:8] == b"ftyp"
        or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    )


def duration_seconds(audio: dict) -> float:
    return audio["waveform"].shape[-1] / float(audio["sample_rate"])


def encode(audio: dict, fmt: str = "mp3", rate: int | None = None, mono: bool = False, bitrate: int = 192000) -> bytes:
    """Encode the first item of an AUDIO batch."""
    waveform = audio["waveform"][0]
    source_rate = int(audio["sample_rate"])
    if waveform.shape[0] > 2:
        waveform = waveform[:2]
    source_layout = "stereo" if waveform.shape[0] == 2 else "mono"
    target_rate = rate or source_rate
    if fmt == "mp3" and target_rate not in _MP3_RATES:
        target_rate = 44100
    target_layout = "mono" if mono else source_layout

    frame = av.AudioFrame.from_ndarray(waveform.detach().cpu().float().numpy(), format="fltp", layout=source_layout)
    frame.sample_rate = source_rate
    resampler = av.AudioResampler(format="fltp" if fmt == "mp3" else "s16", layout=target_layout, rate=target_rate)
    buffer = io.BytesIO()
    with av.open(buffer, "w", format=fmt) as container:
        stream = container.add_stream("libmp3lame" if fmt == "mp3" else "pcm_s16le", rate=target_rate, layout=target_layout)
        if fmt == "mp3":
            stream.bit_rate = bitrate
        for resampled in resampler.resample(frame) + resampler.resample(None):
            for packet in stream.encode(resampled):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def split_points(samples: np.ndarray, sample_rate: int, chunk_seconds: float, search_seconds: float = 5.0) -> list[int]:
    """Choose cut positions near each chunk boundary, at the quietest moment nearby."""
    total = len(samples)
    chunk = int(chunk_seconds * sample_rate)
    search = int(search_seconds * sample_rate)
    window = max(1, int(0.05 * sample_rate))
    points: list[int] = []
    position = chunk
    while position < total - search:
        low, high = max(0, position - search), min(total, position + search)
        energy = np.convolve(np.abs(samples[low:high]), np.ones(window) / window, mode="valid")
        points.append(low + int(np.argmin(energy)) + window // 2)
        position = points[-1] + chunk
    return points


def split(audio: dict, chunk_seconds: float) -> list[tuple[dict, float]]:
    """Split long audio at quiet points. Returns (chunk, start time in seconds) pairs."""
    waveform = audio["waveform"][0]
    rate = int(audio["sample_rate"])
    points = split_points(waveform.mean(dim=0).cpu().numpy(), rate, chunk_seconds)
    edges = [0, *points, waveform.shape[-1]]
    return [
        ({"waveform": waveform[..., a:b].unsqueeze(0), "sample_rate": rate}, a / rate)
        for a, b in zip(edges, edges[1:], strict=False)
    ]


def _timestamp(seconds: float) -> str:
    milliseconds = int(round(max(seconds, 0.0) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def to_srt(segments: list[dict]) -> str:
    blocks = [
        f"{index}\n{_timestamp(s['start'])} --> {_timestamp(s['end'])}\n{str(s.get('text', '')).strip()}\n"
        for index, s in enumerate(segments, 1)
    ]
    return "\n".join(blocks)
