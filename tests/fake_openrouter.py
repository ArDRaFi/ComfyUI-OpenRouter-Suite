"""A local stand-in for OpenRouter. Returns real, decodable media and records every request."""

import base64
import fractions
import io
import json
import os
import sys

import av
import numpy as np
from aiohttp import web
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "data", "catalog_snapshot.json"), encoding="utf-8") as f:
    SNAPSHOT = json.load(f)

requests_seen = []
injected_image_models = []
job_polls = {}
fail_next = {}


def png_bytes(alpha=False):
    picture = Image.new("RGBA" if alpha else "RGB", (64, 48), (200, 30, 30, 128) if alpha else (30, 200, 30))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def mp4_bytes():
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=8)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.time_base = fractions.Fraction(1, 8)
        for i in range(8):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 20, dtype=np.uint8), format="rgb24")
            frame.pts = i
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def mp3_bytes():
    rate = 24000
    samples = np.sin(np.linspace(0, 440 * 2 * np.pi, rate, dtype=np.float32)).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
    frame.sample_rate = rate
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=rate, layout="mono")
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


MP4, MP3 = mp4_bytes(), mp3_bytes()


def summarize(value):
    """Shorten data URLs so the recorded request stays readable."""
    if isinstance(value, str) and len(value) > 120:
        return value[:40] + f"...[{len(value)}]"
    if isinstance(value, dict):
        return {k: summarize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [summarize(v) for v in value]
    return value


async def record(request):
    body = None
    if request.can_read_body:
        body = await request.json()
    requests_seen.append({"path": request.path, "auth": request.headers.get("Authorization"), "body": summarize(body)})
    return body or {}


def maybe_fail(path):
    status = fail_next.pop(path, None)
    if status:
        return web.json_response({"error": {"code": status, "message": "simulated failure"}}, status=status)
    return None


async def image_models(request):
    rows = [
        {"id": slug, "supported_parameters": e["params"], "architecture": {"input_modalities": e["inputs"]}}
        for slug, e in SNAPSHOT["image"].items()
    ]
    return web.json_response({"data": rows + injected_image_models})


async def empty_models(request):
    return web.json_response({"data": []})


async def images(request):
    failure = maybe_fail("/images")
    body = await record(request)
    if failure:
        return failure
    alpha = body.get("background") == "transparent"
    data = [{"b64_json": base64.b64encode(png_bytes(alpha)).decode(), "media_type": "image/png"} for _ in range(int(body.get("n", 1)))]
    return web.json_response({"data": data, "usage": {"cost": 0.04}})


async def videos(request):
    await record(request)
    job = f"gen-vid-{len(requests_seen)}-test"
    return web.json_response({"id": job, "polling_url": f"/api/v1/videos/{job}", "status": "pending"}, status=202)


async def video_status(request):
    await record(request)
    job = request.match_info["job"]
    job_polls[job] = job_polls.get(job, 0) + 1
    if job_polls[job] < 2:
        return web.json_response({"id": job, "status": "in_progress", "polling_url": ""})
    return web.json_response({"id": job, "status": "completed", "polling_url": "", "usage": {"cost": 1.5, "is_byok": False}})


async def video_content(request):
    await record(request)
    return web.Response(body=MP4, content_type="video/mp4")


async def chat(request):
    body = await record(request)
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    await response.write(b": OPENROUTER PROCESSING\n\n")
    if "audio" in (body.get("modalities") or []):
        pcm = np.zeros(2400, dtype="<i2").tobytes()
        deltas = [{"audio": {"data": base64.b64encode(pcm).decode(), "transcript": "la "}}] * 2
    else:
        deltas = [{"content": "Hello "}, {"content": "world"}]
    for delta in deltas:
        event = json.dumps({"choices": [{"delta": delta}]}).encode()
        # Split each event across two writes to exercise the incremental parser.
        await response.write(b"data: " + event[:10])
        await response.write(event[10:] + b"\n\n")
    tail = {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"cost": 0.002}}
    await response.write(b"data: " + json.dumps(tail).encode() + b"\n\ndata: [DONE]\n\n")
    return response


async def speech(request):
    body = await record(request)
    headers = {"X-Generation-Id": "gen-tts-1"}
    if body.get("model", "").startswith("google/"):
        if body.get("response_format") == "mp3":
            return web.json_response({"error": {"code": 400, "message": "pcm only"}}, status=400)
        return web.Response(body=np.zeros(4800, dtype="<i2").tobytes(), headers={**headers, "Content-Type": "audio/pcm"})
    return web.Response(body=MP3, headers={**headers, "Content-Type": "audio/mpeg"})


async def generation(request):
    await record(request)
    return web.json_response({"data": {"total_cost": 0.01}})


async def transcriptions(request):
    await record(request)
    return web.json_response({
        "text": "hello there",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello there"}],
        "usage": {"cost": 0.003},
    })


async def upload(request):
    form = await request.post()
    part = form["fileToUpload"]
    requests_seen.append({"path": request.path, "auth": request.headers.get("Authorization"), "body": {"filename": part.filename, "type": part.content_type, "size": len(part.file.read())}})
    return web.Response(text=f"https://files.fake.test/{part.filename}")


async def debug_requests(request):
    return web.json_response(requests_seen)


async def debug_inject(request):
    payload = await request.json()
    injected_image_models.append({
        "id": payload.get("id", "test/brand-new-image"),
        "supported_parameters": {"aspect_ratio": {"type": "enum", "values": ["1:1", "9:16"]}, "input_references": {"type": "range", "min": 0, "max": 7}},
        "architecture": {"input_modalities": ["text", "image"]},
    })
    return web.json_response({"ok": True})


async def debug_fail(request):
    payload = await request.json()
    fail_next[payload["path"]] = payload["status"]
    return web.json_response({"ok": True})


def create_app():
    app = web.Application(client_max_size=1024**3)
    v1 = "/api/v1"
    app.add_routes([
        web.get(v1 + "/images/models", image_models),
        web.get(v1 + "/videos/models", empty_models),
        web.get(v1 + "/models", empty_models),
        web.post(v1 + "/images", images),
        web.post(v1 + "/videos", videos),
        web.get(v1 + "/videos/{job}", video_status),
        web.get(v1 + "/videos/{job}/content", video_content),
        web.post(v1 + "/chat/completions", chat),
        web.post(v1 + "/audio/speech", speech),
        web.get(v1 + "/generation", generation),
        web.post(v1 + "/audio/transcriptions", transcriptions),
        web.post(v1 + "/__upload", upload),
        web.get("/__requests", debug_requests),
        web.post("/__inject", debug_inject),
        web.post("/__fail", debug_fail),
    ])
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=int(sys.argv[1]), print=None)
