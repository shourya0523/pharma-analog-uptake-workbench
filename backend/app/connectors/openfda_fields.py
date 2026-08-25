from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_openfda_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    try:
        datetime.fromisoformat(s)
        return s[:10]
    except ValueError:
        return None


def earliest_approval_date(results: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return (iso_date, source_field) from OpenFDA drugsFDA submissions.

    Prefers earliest ORIG submission with AP status; falls back to earliest AP date.
    """
    dates: list[tuple[str, str]] = []
    for result in results:
        for sub in result.get("submissions") or []:
            status = (sub.get("submission_status") or "").upper()
            if status and status != "AP":
                continue
            parsed = parse_openfda_date(sub.get("submission_status_date"))
            if not parsed:
                continue
            stype = (sub.get("submission_type") or "").upper()
            field = f"submissions[type={stype or 'UNK'}].submission_status_date"
            dates.append((parsed, field if stype == "ORIG" else f"fallback:{field}"))

    orig = [(d, f) for d, f in dates if not f.startswith("fallback:")]
    pool = orig or [(d, f.removeprefix("fallback:")) for d, f in dates]
    if not pool:
        return None, None
    pool.sort(key=lambda x: x[0])
    return pool[0]
