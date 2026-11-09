from __future__ import annotations

import json
import logging
import time

from .. import capabilities as caps
from .. import catalog, client, jobs, usage, widgets
from ..host import IO, advanced, send_text
from ..media import audio as audio_media
from ..media import hosting
from ..media import image as image_media
from ..media import video as video_media

log = logging.getLogger("openrouter_suite")
POLL_START_SECONDS = 5.0
POLL_MAX_SECONDS = 15.0
JOB_TIMEOUT_SECONDS = 30 * 60


async def wait_for_job(job_id: str, node_id: str | None) -> dict:
    """Poll a job until it finishes. Raises with the job ID so it can be recovered later."""
    started = time.monotonic()
    delay = POLL_START_SECONDS
    while True:
        job = await client.get_json(f"/videos/{job_id}")
        status = job.get("status")
        if status == "completed":
            return job
        if status in ("failed", "cancelled", "expired"):
            raise client.OpenRouterError(f"Video job {status}: {job.get('error') or 'no details given'} (job {job_id})")
        elapsed = time.monotonic() - started
        if elapsed > JOB_TIMEOUT_SECONDS:
            raise client.OpenRouterError(f"Gave up waiting after {JOB_TIMEOUT_SECONDS // 60} minutes. Recover it later with ORS Video Job using {job_id}.")
        send_text(node_id, f"{status or 'waiting'}, {int(elapsed)}s\njob {job_id}")
        await client.sleep_interruptible(delay)
        delay = min(delay * 1.5, POLL_MAX_SECONDS)


def _frame(tensor, frame_type: str) -> dict:
    return {"type": "image_url", "image_url": {"url": image_media.to_data_url(tensor[0])}, "frame_type": frame_type}


async def _video_reference(video) -> dict:
    return {"type": "video_url", "video_url": {"url": await hosting.upload(video_media.to_mp4_bytes(video), "mp4", "video/mp4")}}


async def _audio_reference(audio: dict) -> dict:
    return {"type": "audio_url", "audio_url": {"url": await hosting.upload(audio_media.encode(audio), "mp3", "audio/mpeg")}}


def _connected(slots: dict | None) -> list:
    return [value for value in (slots or {}).values() if value is not None]


def _check_count(slug: str, label: str, count: int, limit: int) -> None:
    if count > limit:
        raise client.OpenRouterError(f"{slug} accepts up to {limit} {label}, but {count} were connected.")


async def _media_fields(slug: str, model: dict, capability: caps.VideoCaps, node_id: str | None) -> dict:
    """Build the request's media fields from whatever is connected."""
    first, last = model.get("first_frame"), model.get("last_frame")
    images = image_media.collect_slots(model.get("images"))
    videos = _connected(model.get("videos"))
    audios = _connected(model.get("audios"))
    _check_count(slug, "reference images", len(images), capability.ref_images)
    _check_count(slug, "reference videos", len(videos), capability.ref_videos)
    _check_count(slug, "reference audio clips", len(audios), capability.ref_audios)

    has_frames = first is not None or last is not None
    if has_frames and (images or videos or audios):
        raise client.OpenRouterError("Connect frames or references, not both. OpenRouter ignores references when frames are sent.")
    if last is not None and first is None:
        raise client.OpenRouterError("A last frame needs a first frame too.")
    if has_frames:
        frames = [_frame(first, "first_frame")]
        if last is not None:
            frames.append(_frame(last, "last_frame"))
        return {"frame_images": frames}

    # Images can be embedded, but OpenRouter requires public HTTPS links for video and audio.
    references: list[dict] = []
    source = model.get("video")
    if source is not None or videos or audios:
        send_text(node_id, "Uploading reference files...")
    if source is not None:
        references.append(await _video_reference(source))
    references += [{"type": "image_url", "image_url": {"url": image_media.to_data_url(frame)}} for frame in images]
    references += [await _video_reference(v) for v in videos]
    references += [await _audio_reference(a) for a in audios]
    if capability.needs_input and not references:
        raise client.OpenRouterError(f"{slug} needs at least one input connected.")
    return {"input_references": references} if references else {}


def _provider_options(text: str) -> dict:
    """Forwarded as written. OpenRouter keys these by provider, such as byteplus."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        options = json.loads(text)
    except ValueError as e:
        raise client.OpenRouterError(f"provider_options must be valid JSON: {e}") from e
    if not isinstance(options, dict):
        raise client.OpenRouterError('provider_options must be a JSON object keyed by provider, such as {"byteplus": {"watermark": false}}.')
    return {"provider": {"options": options}}


class ORSVideo(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_Video",
            display_name="ORS Video",
            category="OpenRouter/Video",
            description="Generate or edit video with any OpenRouter video model. Inputs change to match the model.",
            inputs=[
                widgets.prompt_input(tooltip="The video to generate, or the change to make."),
                IO.DynamicCombo.Input("model", options=widgets.video_options(), tooltip="OpenRouter video model."),
                widgets.seed_input(),
                IO.String.Input("continue_job_id", force_input=True, optional=True, tooltip="Extend or edit a finished job, on models that support it.", **advanced()),
            ],
            outputs=[IO.Video.Output(), IO.String.Output("job_id", display_name="job_id")],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, prompt: str, model: dict, seed: int, continue_job_id: str | None = None) -> IO.NodeOutput:
        slug = model["model"]
        entry = catalog.entry("video", slug)
        node_id = cls.hidden.unique_id
        media = await _media_fields(slug, model, caps.video_caps(slug, entry), node_id)
        if not media and not prompt.strip():
            raise client.OpenRouterError("Enter a prompt, or connect an input.")

        body: dict = {"model": slug, **media}
        if prompt.strip():
            body["prompt"] = prompt
        for name in ("duration", "resolution", "aspect_ratio", "generate_audio", "upscale_factor", "creativity"):
            if name in model:
                body[name] = model[name]
        if entry.get("seed"):
            body["seed"] = seed
        if continue_job_id:
            body["previous_job_id"] = jobs.validate_job_id(continue_job_id)
        body.update(_provider_options(model.get("provider_options", "")))

        send_text(node_id, "Submitting...")
        submitted = await client.post_json("/videos", body)
        job_id = jobs.validate_job_id(submitted.get("id", ""))
        jobs.record(job_id, slug)
        log.info("[OpenRouter Suite] video job %s submitted (%s)", job_id, slug)
        job = await wait_for_job(job_id, node_id)
        send_text(node_id, "Downloading...")
        video = await video_media.download_job(job_id)
        usage.report(node_id, (job.get("usage") or {}).get("cost"))
        return IO.NodeOutput(video, job_id)


class ORSVideoJob(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_VideoJob",
            display_name="ORS Video Job",
            category="OpenRouter/Video",
            description="Fetch the result of a video job you have already paid for, after a crash or interrupt.",
            inputs=[IO.String.Input("job_id", default="", tooltip="Leave empty to use the most recent job.")],
            outputs=[IO.Video.Output()],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, job_id: str) -> IO.NodeOutput:
        node_id = cls.hidden.unique_id
        target = jobs.validate_job_id(job_id or jobs.latest())
        await wait_for_job(target, node_id)
        video = await video_media.download_job(target)
        send_text(node_id, f"Recovered job {target}")
        return IO.NodeOutput(video)
