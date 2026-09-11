"""What an XBRL element means, where the filing's own arithmetic does not say.

A filing's calculation linkbase settles almost every element on the product
axis: revenue carries weight +1 in the total above it and a cost carries -1.
What it leaves unplaced is an element the filer tags by product but never puts
into the statements' arithmetic - a royalty line, a disaggregation footnote.

Those are decided once, by a model, and written down here. An element's
meaning does not depend on which filer used it, so the register is keyed by
the element alone, and a decision made against one filing answers the same
element in every other. That is what makes this a cache and not a mechanism:
delete the file and every element is asked again, at one call each, and the
answer is the same.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REGISTER_PATH = Path(__file__).resolve().parents[3] / "seed" / "xbrl_elements.csv"
REGISTER_FIELDS = ("element", "is_revenue", "method", "confidence", "note")

# A model that hedges has said it is guessing, and a guess about what a
# figure *is* mislabels every figure carried under that element.
CONFIDENCE_FLOOR = 0.8


@dataclass(frozen=True)
class Verdict:
    element: str
    is_revenue: bool | None
    method: str
    confidence: float
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.is_revenue is not None and (
            self.method != "llm" or self.confidence >= CONFIDENCE_FLOOR
        )


def load_register(path: Path | None = None) -> dict[str, Verdict]:
    path = path or REGISTER_PATH
    if not path.exists():
        return {}
    out: dict[str, Verdict] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("is_revenue") or "").strip().lower()
            is_revenue = None if raw in {"", "none", "null"} else raw in {"1", "true", "yes"}
            out[row["element"]] = Verdict(
                element=row["element"], is_revenue=is_revenue,
                method=row.get("method") or "human",
                confidence=float(row.get("confidence") or 1.0),
                note=row.get("note") or "",
            )
    return out


def save_register(entries: dict[str, Verdict], path: Path | None = None) -> None:
    """Sorted, so a change reads as a diff."""
    path = path or REGISTER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_FIELDS)
        writer.writeheader()
        for element in sorted(entries):
            v = entries[element]
            writer.writerow({
                "element": element,
                "is_revenue": "" if v.is_revenue is None else str(v.is_revenue).lower(),
                "method": v.method, "confidence": f"{v.confidence:g}", "note": v.note,
            })


def verdicts(register: dict[str, Verdict]) -> dict[str, bool]:
    """The register as the reader takes it: only decisions worth acting on."""
    return {e: v.is_revenue for e, v in register.items() if v.usable and v.is_revenue is not None}
