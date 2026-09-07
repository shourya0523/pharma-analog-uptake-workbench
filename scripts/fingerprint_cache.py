"""Inspect or prune the fingerprint cache.

    uv run python ../scripts/fingerprint_cache.py --stats
    uv run python ../scripts/fingerprint_cache.py --purge-version 1
    uv run python ../scripts/fingerprint_cache.py --purge-model openai/gpt-4o-mini
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.fingerprint.llm import CACHE_DIR  # noqa: E402


def entries() -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(CACHE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        meta = data.get("meta", {"version": "1", "model": "?"}) if isinstance(data, dict) else {}
        out.append((path, meta))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--purge-version")
    parser.add_argument("--purge-model")
    args = parser.parse_args()
    items = entries()
    if args.stats or not (args.purge_version or args.purge_model):
        by_version = Counter(str(m.get("version")) for _, m in items)
        by_model = Counter(str(m.get("model")) for _, m in items)
        failed = len(list(CACHE_DIR.glob("*.failed.txt")))
        print(f"{len(items)} cached descriptions in {CACHE_DIR}; {failed} failed responses")
        print(f"  by prompt version: {dict(by_version)}")
        print(f"  by model: {dict(by_model)}")
    removed = 0
    for path, meta in items:
        if (args.purge_version and str(meta.get("version")) == args.purge_version) or \
                (args.purge_model and str(meta.get("model")) == args.purge_model):
            path.unlink()
            removed += 1
    if removed:
        print(f"removed {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
