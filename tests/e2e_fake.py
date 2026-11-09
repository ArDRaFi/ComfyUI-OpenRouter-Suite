"""Runs real ComfyUI workflows against tests/fake_openrouter.py. Costs nothing.

Usage: python tests/e2e_fake.py <comfy_port> <fake_port>
ComfyUI must be running with OPENROUTER_API_KEY=test-key-1234 and ORS_TEST_BASE_URL set.
"""

import json
import sys
import time
import urllib.error
import urllib.request

COMFY = f"http://127.0.0.1:{sys.argv[1]}"
FAKE = f"http://127.0.0.1:{sys.argv[2]}"
failures = []


def http(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{'  ' + str(detail) if not condition else ''}")
    if not condition:
        failures.append(name)


def run(workflow):
    response = http(f"{COMFY}/prompt", {"prompt": workflow})
    if "prompt_id" not in response:
        return {"validation": response}
    prompt_id = response["prompt_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        history = http(f"{COMFY}/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(0.5)
    return {"timeout": True}


def ok(result):
    return result.get("status", {}).get("status_str") == "success"


def error_text(result):
    if "validation" in result:
        return json.dumps(result["validation"])[:800]
    for name, data in result.get("status", {}).get("messages", []):
        if name == "execution_error":
            return data.get("exception_message", "")
    return json.dumps(result)[:500]


def requests_for(path):
    return [r for r in http(f"{FAKE}/__requests") if r["path"].endswith(path)]


info = http(f"{COMFY}/object_info")
names = ["ORS_Image", "ORS_Video", "ORS_VideoJob", "ORS_LLM", "ORS_TextToSpeech", "ORS_SpeechToText", "ORS_AudioGenerate"]
for name in names:
    check(f"registered {name}", name in info)
check("key absent from /object_info", "test-key-1234" not in json.dumps(info))

status = http(f"{COMFY}/openrouter_suite/status")
check("status hides key", status["key"] == {"set": True, "last4": "1234", "source": "environment"}, status["key"])

image_spec = info["ORS_Image"]["input"]["required"]["model"]
options = {o["key"]: o for o in image_spec[1]["options"]}
def slot_count(key):
    autogrow = options[key]["inputs"].get("required", {}).get("images") or options[key]["inputs"].get("optional", {}).get("images")
    return len(autogrow[1]["template"]["names"]) if autogrow else 0
check("GPT Image has 16 slots", slot_count("openai/gpt-image-2") == 16, slot_count("openai/gpt-image-2"))
check("Krea 2 has 1 slot", slot_count("krea/krea-2-medium") == 1, slot_count("krea/krea-2-medium"))
check("FLUX.2 Pro has 8 slots", slot_count("black-forest-labs/flux.2-pro") == 8, slot_count("black-forest-labs/flux.2-pro"))
check("text-only model has no slots", slot_count("recraft/recraft-v4.1-flash") == 0)
check("SVG-only models hidden", "recraft/recraft-v4-vector" not in options)

video_options = {o["key"]: o for o in info["ORS_Video"]["input"]["required"]["model"][1]["options"]}
def inputs_of(key):
    spec = video_options[key]["inputs"]
    return {**spec.get("required", {}), **spec.get("optional", {})}
check("Veo frames and references", {"first_frame", "last_frame", "images"} <= set(inputs_of("google/veo-3.1")), list(inputs_of("google/veo-3.1")))
check("Veo has no video or audio slots", not {"videos", "audios"} & set(inputs_of("google/veo-3.1")))
check("Seedance has all reference types", {"images", "videos", "audios"} <= set(inputs_of("bytedance/seedance-2.0")))
first_only = next(k for k, e in video_options.items() if "first_frame" in e["inputs"].get("optional", {}) and "last_frame" not in e["inputs"].get("optional", {}))
check("first-frame-only model has no last frame", "last_frame" not in inputs_of(first_only))
check("no input is nested two levels", all(spec[0] != "COMFY_DYNAMICCOMBO_V3" for key in video_options for spec in inputs_of(key).values()))
check("FLUX Edit requires a video", "video" in video_options["black-forest-labs/flux-video-edit"]["inputs"]["required"])
check("Avatar has image and audio slots", {"images", "audios"} <= set(inputs_of("heygen/avatar-iv")))

base_image = {"class_type": "EmptyImage", "inputs": {"width": 32, "height": 32, "batch_size": 1, "color": 255}}

# Image: references, per-model options, cost.
result = run({
    "1": base_image,
    "2": {"class_type": "ORS_Image", "inputs": {
        "prompt": "a cat", "model": "openai/gpt-image-2.6-sunburst", "seed": 1,
        "model.images.image_1": ["1", 0], "model.images.image_2": ["1", 0],
        "model.aspect_ratio": "1:1", "model.background": "transparent", "model.n": 2,
        "model.output_compression": 100, "model.output_format": "png", "model.quality": "auto",
    }},
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
    "4": {"class_type": "MaskToImage", "inputs": {"mask": ["2", 1]}},
    "5": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
})
check("image run", ok(result), error_text(result))
body = requests_for("/images")[-1]["body"] if requests_for("/images") else {}
check("image sends both references", len(body.get("input_references", [])) == 2, body)
check("image options passed", body.get("background") == "transparent" and body.get("n") == 2)
check("compression dropped for png", "output_compression" not in body)
check("seed dropped when unsupported", "seed" not in body)
check("auth header sent", requests_for("/images")[-1]["auth"] == "Bearer test-key-1234")

# Too many references for the model.
result = run({
    "1": {"class_type": "EmptyImage", "inputs": {"width": 32, "height": 32, "batch_size": 3, "color": 0}},
    "2": {"class_type": "ORS_Image", "inputs": {"prompt": "x", "model": "krea/krea-2-medium", "seed": 1, "model.images.image_1": ["1", 0],
          "model.aspect_ratio": "1:1", "model.resolution": "1K"}},
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
})
check("batch over limit rejected", "up to 1 reference" in error_text(result), error_text(result))

# Unbilled 502 on images is retried.
http(f"{FAKE}/__fail", {"path": "/images", "status": 502})
before = len(requests_for("/images"))
result = run({
    "2": {"class_type": "ORS_Image", "inputs": {"prompt": "retry me", "model": "recraft/recraft-v4.1-flash", "seed": 5, "model.aspect_ratio": "1:1", "model.n": 1}},
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
})
check("image 502 retried then succeeded", ok(result), error_text(result))
check("image 502 sent twice", len(requests_for("/images")) - before == 2, len(requests_for("/images")) - before)

# Video: first + last frame.
veo = {"model": "google/veo-3.1", "model.duration": 4, "model.resolution": "720p", "model.aspect_ratio": "16:9", "model.generate_audio": True}
result = run({
    "1": base_image,
    "2": {"class_type": "ORS_Video", "inputs": {"prompt": "a dog", "seed": 7, **veo, "model.first_frame": ["1", 0], "model.last_frame": ["1", 0]}},
    "3": {"class_type": "SaveVideo", "inputs": {"video": ["2", 0], "filename_prefix": "video/ors", "format": "auto", "codec": "auto"}},
})
check("video first+last run", ok(result), error_text(result))
body = requests_for("/videos")[-1]["body"] if requests_for("/videos") else {}
check("video sends two frames, no references", [f.get("frame_type") for f in body.get("frame_images", [])] == ["first_frame", "last_frame"] and "input_references" not in body, body)
check("video seed sent", body.get("seed") == 7)

# Video: Seedance with image, video and audio references chained from other nodes.
seedance = {"model": "bytedance/seedance-2.0", "model.duration": 5, "model.resolution": "720p", "model.aspect_ratio": "16:9", "model.generate_audio": True, "model.provider_options": ""}
speech_node = {"class_type": "ORS_TextToSpeech", "inputs": {"text": "hello", "model": "hexgrad/kokoro-82m", "model.voice": "af_alloy"}}
result = run({
    "1": base_image,
    "2": {"class_type": "ORS_Video", "inputs": {"prompt": "first", "seed": 8, **veo}},
    "3": speech_node,
    "4": {"class_type": "ORS_Video", "inputs": {"prompt": "mix", "seed": 9, **seedance,
          "model.images.image_1": ["1", 0], "model.videos.video_1": ["2", 0], "model.audios.audio_1": ["3", 0]}},
    "5": {"class_type": "PreviewAny", "inputs": {"source": ["4", 1]}},
})
check("seedance references run", ok(result), error_text(result))
body = requests_for("/videos")[-1]["body"] if requests_for("/videos") else {}
check("seedance sends image, video and audio", sorted(r["type"] for r in body.get("input_references", [])) == ["audio_url", "image_url", "video_url"], body)
by_type = {r["type"]: r[r["type"]]["url"] for r in body.get("input_references", [])}
check("video and audio references are public https links", by_type.get("video_url", "").startswith("https://") and by_type.get("audio_url", "").startswith("https://"), by_type)
check("image reference stays embedded", by_type.get("image_url", "").startswith("data:image/"), by_type)
uploads = requests_for("/__upload")
check("uploads carry media with random names", {u["body"]["type"] for u in uploads} >= {"video/mp4", "audio/mpeg"} and all(u["body"]["size"] > 0 for u in uploads), uploads)
check("api key never sent to upload host", all(u["auth"] is None for u in uploads), uploads)

# Frames and references together are refused rather than silently dropped.
result = run({
    "1": base_image,
    "2": {"class_type": "ORS_Video", "inputs": {"prompt": "x", "seed": 31, **veo, "model.first_frame": ["1", 0], "model.images.image_1": ["1", 0]}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 1]}},
})
check("frames plus references refused", "not both" in error_text(result), error_text(result))
result = run({
    "1": base_image,
    "2": {"class_type": "ORS_Video", "inputs": {"prompt": "x", "seed": 32, **veo, "model.last_frame": ["1", 0]}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 1]}},
})
check("last frame alone refused", "needs a first frame" in error_text(result), error_text(result))

# Too many references hits the override limit: 4 images on Veo (limit 3).
result = run({
    "1": {"class_type": "EmptyImage", "inputs": {"width": 32, "height": 32, "batch_size": 4, "color": 0}},
    "2": {"class_type": "ORS_Video", "inputs": {"prompt": "x", "seed": 3, **veo, "model.images.image_1": ["1", 0]}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 1]}},
})
check("override limit enforced", "up to 3 reference images" in error_text(result), error_text(result))

# A single-mode edit model takes its video directly on the node.
result = run({
    "1": {"class_type": "ORS_Video", "inputs": {"prompt": "source", "seed": 21, **veo}},
    "2": {"class_type": "ORS_Video", "inputs": {"prompt": "make it snowy", "seed": 22, "model": "black-forest-labs/flux-video-edit", "model.video": ["1", 0]}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 1]}},
})
check("single-mode edit run", ok(result), error_text(result))
body = requests_for("/videos")[-1]["body"] if requests_for("/videos") else {}
check("edit sends source video", [r["type"] for r in body.get("input_references", [])] == ["video_url"] and body["input_references"][0]["video_url"]["url"].startswith("https://"), body)
check("Grok Lite offers no reference images", "images" not in inputs_of("x-ai/grok-imagine-video-1.5-lite"))
before_uploads = len(requests_for("/__upload"))
result = run({
    "1": {"class_type": "ORS_Video", "inputs": {"prompt": "source", "seed": 51, **veo}},
    "2": base_image,
    "3": {"class_type": "ORS_Video", "inputs": {"prompt": "x", "seed": 52, "model": "bytedance/seedance-2.0", "model.duration": 5, "model.resolution": "720p", "model.aspect_ratio": "16:9", "model.generate_audio": True, "model.first_frame": ["2", 0], "model.videos.video_1": ["1", 0]}},
    "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 1]}},
})
check("nothing uploaded when the request is invalid", "not both" in error_text(result) and len(requests_for("/__upload")) == before_uploads, error_text(result))

# Recovery.
result = run({
    "1": {"class_type": "ORS_VideoJob", "inputs": {"job_id": ""}},
    "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "filename_prefix": "video/ors_job", "format": "auto", "codec": "auto"}},
})
check("job recovery from journal", ok(result), error_text(result))
result = run({
    "1": {"class_type": "ORS_VideoJob", "inputs": {"job_id": "../../etc/passwd"}},
    "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "filename_prefix": "video/x", "format": "auto", "codec": "auto"}},
})
check("job id path traversal rejected", "valid OpenRouter video job ID" in error_text(result), error_text(result))

# Interrupting during polling stops the wait, and the paid job can still be recovered.
response = http(f"{COMFY}/prompt", {"prompt": {
    "1": {"class_type": "ORS_Video", "inputs": {"prompt": "interrupt me", "seed": 41, **veo}},
    "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "filename_prefix": "video/ors_int", "format": "auto", "codec": "auto"}},
}})
time.sleep(2.5)
started_interrupt = time.time()
urllib.request.urlopen(urllib.request.Request(f"{COMFY}/interrupt", data=b"{}", headers={"Content-Type": "application/json"}))
history = {}
while time.time() - started_interrupt < 30 and response["prompt_id"] not in history:
    time.sleep(0.25)
    history = http(f"{COMFY}/history/{response['prompt_id']}")
messages = [name for name, _ in history.get(response["prompt_id"], {}).get("status", {}).get("messages", [])]
check("interrupt stops polling promptly", "execution_interrupted" in messages and time.time() - started_interrupt < 10, messages)
result = run({
    "1": {"class_type": "ORS_VideoJob", "inputs": {"job_id": ""}},
    "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "filename_prefix": "video/ors_rec", "format": "auto", "codec": "auto"}},
})
check("interrupted job recovered", ok(result), error_text(result))

# The key route never returns the key.
saved = http(f"{COMFY}/openrouter_suite/key", {"key": "sk-or-route-secret"})
check("key route hides key", "sk-or-route-secret" not in json.dumps(saved), saved)
http(f"{COMFY}/openrouter_suite/key", {"key": ""})

# LLM: vision, dropped unsupported parameters, streamed text.
vision_model = "openai/gpt-5.5"
llm_inputs = {"prompt": "describe", "system_prompt": "be brief", "model": vision_model, "temperature": 0.5, "max_tokens": 100, "seed": 2}
result = run({
    "1": base_image,
    "2": {"class_type": "ORS_LLM", "inputs": {**llm_inputs, "images.image_1": ["1", 0]}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}},
})
check("llm run", ok(result), error_text(result))
body = requests_for("/chat/completions")[-1]["body"] if requests_for("/chat/completions") else {}
user = body.get("messages", [{}])[-1].get("content", [])
check("llm sends image part", any(p.get("type") == "image_url" for p in user if isinstance(p, dict)), body)
text = result.get("outputs", {}).get("3", {}).get("text", [""])[0] if ok(result) else ""
check("llm streamed text reassembled", text == "Hello world", text)

result = run({
    "1": {"class_type": "ORS_LLM", "inputs": {"prompt": "hi", "model": vision_model, "temperature": 0.5, "max_tokens": 0, "seed": 3}},
    "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
})
check("llm with no media", ok(result), error_text(result))
check("max_tokens 0 omitted", "max_tokens" not in requests_for("/chat/completions")[-1]["body"])

# TTS: PCM-only fallback, then STT on the result.
result = run({
    "1": {"class_type": "ORS_TextToSpeech", "inputs": {"text": "hello", "model": "google/gemini-3.8-flash-tts", "model.voice": "Zephyr"}},
    "2": {"class_type": "ORS_SpeechToText", "inputs": {"audio": ["1", 0], "model": "openai/whisper-1", "language": "en", "timestamps": True}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 1]}},
    "4": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "audio/ors"}},
})
check("tts + stt run", ok(result), error_text(result))
speech_calls = requests_for("/audio/speech")
check("tts fell back to pcm", [c["body"]["response_format"] for c in speech_calls[-2:]] == ["mp3", "pcm"], speech_calls[-2:])
stt = requests_for("/audio/transcriptions")[-1]["body"] if requests_for("/audio/transcriptions") else {}
check("stt sends audio", stt.get("input_audio", {}).get("format") == "mp3" and stt.get("language") == "en", stt)
srt = result.get("outputs", {}).get("3", {}).get("text", [""])[0] if ok(result) else ""
check("srt produced", "00:00:00,000 --> 00:00:01,000" in srt, srt)

# Audio generate.
result = run({
    "1": {"class_type": "ORS_AudioGenerate", "inputs": {"prompt": "a tune", "model": "openai/gpt-audio", "voice": "", "seed": 1}},
    "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "audio/ors_gen"}},
})
check("audio generate run", ok(result), error_text(result))

# Re-queue does not re-bill.
workflow = {
    "2": {"class_type": "ORS_Image", "inputs": {"prompt": "cached", "model": "recraft/recraft-v4.1-flash", "seed": 11, "model.aspect_ratio": "1:1", "model.n": 1}},
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
}
run(workflow)
count = len(requests_for("/images"))
run(workflow)
check("unchanged re-queue makes no second call", len(requests_for("/images")) == count)

# Live update without a restart.
http(f"{FAKE}/__inject", {})
refreshed = http(f"{COMFY}/openrouter_suite/refresh", {})
check("refresh reports new model", "test/brand-new-image" in refreshed.get("added", []), refreshed)
live = http(f"{COMFY}/object_info/ORS_Image")["ORS_Image"]["input"]["required"]["model"][1]["options"]
check("new model appears with correct slots", any(o["key"] == "test/brand-new-image" and len(o["inputs"]["optional" if "images" in o["inputs"].get("optional", {}) else "required"]["images"][1]["template"]["names"]) == 7 for o in live))
result = run({
    "2": {"class_type": "ORS_Image", "inputs": {"prompt": "new", "model": "test/brand-new-image", "seed": 1, "model.aspect_ratio": "9:16"}},
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
})
check("new model runs immediately", ok(result), error_text(result))

print(f"\n{len(failures)} failure(s)" if failures else "\nAll checks passed")
sys.exit(1 if failures else 0)
