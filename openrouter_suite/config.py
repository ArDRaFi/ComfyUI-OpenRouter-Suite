"""API key storage. The key lives on the server and is never placed in a node widget,
because widget values are saved into workflows and PNG metadata."""

from __future__ import annotations

import json
import os
import tempfile

ENV_KEY = "OPENROUTER_API_KEY"


def user_dir() -> str:
    """Directory for this pack's per-user files."""
    base = os.environ.get("ORS_USER_DIR")
    if not base:
        import folder_paths

        base = os.path.join(folder_paths.get_user_directory(), "openrouter_suite")
    os.makedirs(base, exist_ok=True)
    return base


def atomic_write_json(path: str, data) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _config_path() -> str:
    return os.path.join(user_dir(), "config.json")


def _read_config() -> dict:
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_api_key() -> str:
    return (os.environ.get(ENV_KEY) or _read_config().get("api_key") or "").strip()


def set_api_key(key: str) -> None:
    config = _read_config()
    key = (key or "").strip()
    if key:
        config["api_key"] = key
    else:
        config.pop("api_key", None)
    atomic_write_json(_config_path(), config)


def key_status() -> dict:
    """What the browser may know about the key. Never includes the key itself."""
    key = get_api_key()
    if not key:
        return {"set": False, "last4": "", "source": ""}
    source = "environment" if os.environ.get(ENV_KEY) else "settings"
    return {"set": True, "last4": key[-4:], "source": source}
