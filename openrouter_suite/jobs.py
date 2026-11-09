"""A small journal of submitted video jobs, so a paid job can be recovered after a crash."""

from __future__ import annotations

import json
import os
import re
import time

from . import client, config

MAX_ENTRIES = 200
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _path() -> str:
    return os.path.join(config.user_dir(), "video_jobs.json")


def _read() -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record(job_id: str, model: str) -> None:
    entries = _read()
    entries.append({"job_id": job_id, "model": model, "created": time.time()})
    config.atomic_write_json(_path(), entries[-MAX_ENTRIES:])


def latest() -> str:
    entries = _read()
    return entries[-1]["job_id"] if entries else ""


def validate_job_id(job_id: str) -> str:
    """Job IDs become part of a URL path, so only accept the documented characters."""
    job_id = (job_id or "").strip()
    if not _JOB_ID.match(job_id):
        raise client.OpenRouterError("That is not a valid OpenRouter video job ID.")
    return job_id
