"""Regenerate data/catalog_snapshot.json, the model list used before the first live refresh."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from openrouter_suite import catalog  # noqa: E402


def main() -> None:
    fetched = catalog.fetch_all()
    missing = [section for section in catalog.SECTIONS if not fetched.get(section)]
    if missing:
        raise SystemExit(f"Could not fetch: {', '.join(missing)}")
    data, _ = catalog.merge({}, fetched)
    with open(catalog.SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, sort_keys=True)
    print({section: len(data[section]) for section in catalog.SECTIONS})


if __name__ == "__main__":
    main()
