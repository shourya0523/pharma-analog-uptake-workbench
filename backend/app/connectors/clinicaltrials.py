from __future__ import annotations

from typing import Any


def _key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def deduplicate_phase3_programs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse trial records to sponsor/asset programs while retaining trial IDs."""

    programs: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        sponsor = str(record.get("sponsor") or "").strip()
        asset = str(record.get("asset") or "").strip()
        if not sponsor or not asset:
            continue
        key = (_key(sponsor), _key(asset))
        program = programs.setdefault(
            key,
            {
                "sponsor": sponsor,
                "asset": asset,
                "trial_ids": [],
                "source": "ClinicalTrials.gov",
                "coverage": "phase3_records_only",
            },
        )
        nct_id = str(record.get("nct_id") or "").strip()
        if nct_id and nct_id not in program["trial_ids"]:
            program["trial_ids"].append(nct_id)
    for program in programs.values():
        program["trial_ids"].sort()
    return [programs[key] for key in sorted(programs)]

