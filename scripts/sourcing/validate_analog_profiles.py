"""Check analog profile files against the rubric in docs/analog_profiles.md.

The criteria and their options are read from the rubric's tables, so a
criterion added there is checked here without a second list to keep in step.
An option cell lists choices separated by " / "; "Calendar year" means a
four-digit year. Every criterion also accepts Unknown.

    python scripts/sourcing/validate_analog_profiles.py PROFILES.csv [EVIDENCE.csv]

Exits non-zero, listing each problem, when a column is missing, a value is
not an option, a product appears twice, or (with an evidence file) a value
has no evidence row agreeing with it or an evidence row has no source.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUBRIC = REPO / "docs" / "analog_profiles.md"
ID_COLUMNS = ("drug_name", "generic_name", "launch_indication")


def criteria() -> dict[str, set[str] | None]:
    """Criterion -> allowed options, or None for a year."""
    out: dict[str, set[str] | None] = {}
    for name, options in re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|", RUBRIC.read_text(), re.M):
        name, options = name.strip(), options.strip()
        if name in ("Criterion", "---") or set(name) <= {"-"}:
            continue
        if options.lower() == "calendar year":
            out[name] = None
        else:
            out[name] = {option.strip() for option in options.split(" / ")} | {"Unknown"}
    return out


def option_allowed(value: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return bool(re.fullmatch(r"(19|20)\d\d", value)) or value == "Unknown"
    return value in allowed or value.replace("–", "-") in {a.replace("–", "-") for a in allowed}


def check(profiles: Path, evidence: Path | None) -> list[str]:
    rubric = criteria()
    problems = []
    with profiles.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    header = list(rows[0].keys()) if rows else []
    for column in [*ID_COLUMNS, *rubric]:
        if column not in header:
            problems.append(f"missing column: {column}")
    seen = set()
    for row in rows:
        name = row.get("drug_name", "")
        if name in seen:
            problems.append(f"{name}: appears twice")
        seen.add(name)
        for criterion, allowed in rubric.items():
            value = (row.get(criterion) or "").strip()
            if not option_allowed(value, allowed):
                problems.append(f"{name} / {criterion}: {value!r} is not an option")
    if evidence:
        with evidence.open(newline="") as handle:
            ev = list(csv.DictReader(handle))
        by_key = {(r["drug_name"], r["criterion"]): r for r in ev}
        for row in rows:
            for criterion in rubric:
                record = by_key.get((row["drug_name"], criterion))
                value = (row.get(criterion) or "").strip()
                if record is None:
                    problems.append(f"{row['drug_name']} / {criterion}: no evidence row")
                elif record["value"].strip() != value:
                    problems.append(f"{row['drug_name']} / {criterion}: evidence says {record['value']!r}, profile {value!r}")
                elif value != "Unknown" and not record.get("source_url", "").startswith("http"):
                    problems.append(f"{row['drug_name']} / {criterion}: no source for {value!r}")
    return problems


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    problems = check(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
