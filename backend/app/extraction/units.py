"""Unit magnitudes: arithmetic about numbers, not a reading of any document."""

from __future__ import annotations

UNIT_SCALE_TO_MILLIONS: dict[str, float] = {
    "billions": 1000.0,
    "millions": 1.0,
    "thousands": 0.001,
    "units": 0.000001,
}
