from __future__ import annotations

import random
from typing import Any

from app.config import get_settings

# Below this a value is queued whatever else is true of it.
LOW_CONFIDENCE = 0.7

# Why a value is in the queue, in words, keyed by the reason `add` attaches
# below. Served beside the queue so the page that shows a reason shows its
# meaning from the same place the reason is decided. A reason added to
# `select_validation_tasks` without a line here fails
# `test_every_queue_reason_has_its_prose`.
REASON_HELP: dict[str, str] = {
    "low_confidence": f"Confidence fell below the {LOW_CONFIDENCE} gate.",
    "conflict": "Two candidates disagreed for this quarter and reconciliation picked one.",
    "ocr_derived": "Recovered from a PDF whose columns came from whitespace, not markup.",
    "early_launch": "One of the first quarters after launch, which are often restated.",
    "recent_period": "One of the two newest quarters, which are always sampled.",
    "needs_review": "The evidence judge did not accept the quote as supporting the value.",
    "random_auto_pass_sample": "A random QA sample of auto-passed rows.",
}


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
    early = {d["id"] for d in ordered[:2]} if ordered else set()
    recent = {d["id"] for d in ordered[-2:]} if ordered else set()

    for dp in datapoints:
        conf = dp.get("confidence_score") or 0
        if conf < LOW_CONFIDENCE:
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
    sample_n = max(0, round(len(auto_pass) * settings.validation_sample_rate))
    for dp in random.sample(auto_pass, min(sample_n, len(auto_pass))):
        add(dp, "random_auto_pass_sample")

    return tasks
