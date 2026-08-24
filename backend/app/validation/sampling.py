from __future__ import annotations

import random
from typing import Any

from app.config import get_settings


def select_validation_tasks(
    datapoints: list[dict[str, Any]],
    *,
    conflict_ids: set[str] | None = None,
    ocr_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    conflict_ids = conflict_ids or set()
    ocr_ids = ocr_ids or set()
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(dp: dict[str, Any], reason: str) -> None:
        dp_id = dp["id"]
        if dp_id in seen:
            return
        seen.add(dp_id)
        tasks.append({"datapoint_id": dp_id, "reason": reason, "confidence_score": dp.get("confidence_score", 0)})

    # Sort by period for early/recent heuristics
    ordered = sorted(datapoints, key=lambda d: d.get("period") or "")
    early = set(d["id"] for d in ordered[:2]) if ordered else set()
    recent = set(d["id"] for d in ordered[-2:]) if ordered else set()

    for dp in datapoints:
        conf = dp.get("confidence_score") or 0
        if conf < 0.7:
            add(dp, "low_confidence")
        if dp["id"] in conflict_ids:
            add(dp, "conflict")
        if dp["id"] in ocr_ids:
            add(dp, "ocr_derived")
        if dp["id"] in early:
            add(dp, "early_launch")
        if dp["id"] in recent:
            add(dp, "recent_period")
        if dp.get("validation_status") == "needs_review":
            add(dp, "needs_review")

    auto_pass = [d for d in datapoints if d.get("validation_status") == "auto_pass" and d["id"] not in seen]
    sample_n = max(0, int(round(len(auto_pass) * settings.validation_sample_rate)))
    for dp in random.sample(auto_pass, min(sample_n, len(auto_pass))):
        add(dp, "random_auto_pass_sample")

    return tasks
