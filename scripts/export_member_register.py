"""Write the member register back out of the database, for review.

The table is the store; the file is how a person reads it. Without this the
trade made when the register moved into the database - a git diff for a store
that survives a run - would only have the losing half.

What comes back is a diff against what runs have learned and what reviewers
have settled:

    uv run --project backend python scripts/export_member_register.py
    git diff seed/xbrl_members.csv

Editing that file and re-seeding an empty database is the supported way to
correct a member by hand. On a database that already holds the register, set
``validation_status='confirmed'`` on the row instead: nothing automated
overwrites a confirmed row.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import select

from app.db.models import SessionLocal, XbrlMemberResolutionORM
from app.extraction.members import (
    REGISTER_PATH,
    Resolution,
    save_register,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=REGISTER_PATH,
                        help=f"where to write (default {REGISTER_PATH})")
    args = parser.parse_args()

    with SessionLocal() as session:
        rows = list(session.scalars(select(XbrlMemberResolutionORM)))

    entries = {
        (row.issuer, row.member): Resolution(
            member=row.member,
            product=row.product or None,
            method=row.method,
            confidence=row.confidence,
            note=row.note or "",
            verdict=row.verdict or "",
            candidates_fingerprint=row.candidates_fingerprint or "",
        )
        for row in rows
    }
    save_register(entries, path=args.out)

    named = sum(1 for entry in entries.values() if entry.resolved)
    confirmed = sum(1 for row in rows if row.validation_status == "confirmed")
    print(f"{args.out}: {len(entries)} members, {named} naming a product, "
          f"{confirmed} confirmed by a reviewer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
