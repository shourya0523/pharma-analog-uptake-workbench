"""Regenerate the degraded-package freeze: ``uv run python -m tests.freeze_degraded``."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEGRADED = BACKEND / "app" / "extraction" / "degraded"
TARGET = Path(__file__).resolve().parent / "fixtures" / "degraded_freeze.json"

if __name__ == "__main__":
    freeze = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(DEGRADED.glob("*.py"))}
    TARGET.write_text(json.dumps(freeze, indent=2) + "\n")
    print(f"froze {len(freeze)} files into {TARGET}")
