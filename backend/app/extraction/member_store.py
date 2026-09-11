"""The member register, kept where a run can add to it.

``members.py`` holds the rules; this holds the decisions. They used to live only
in ``seed/xbrl_members.csv``, which works for a script somebody runs on a laptop
and not for the product: the file sits in the source tree, a deployment runs
several workers over containers that are discarded when the run ends, and the
drugs a run is about arrive at upload time rather than at build time. A mapping
learned while reading a filing had nowhere to go.

So the file becomes the seed and the table becomes the store. The file is still
the copy a human reads and edits - ``export_member_register.py`` writes it back
out - and a row a person has confirmed is never overwritten by anything
automated, which is the rule the metadata backfill already follows.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import DrugJobORM, XbrlMemberResolutionORM
from app.extraction.members import (
    NOT_A_PRODUCT,
    VERDICT_NO_CANDIDATE_MATCH,
    Resolution,
    fingerprint,
)
from app.extraction.members import load_register as load_register_csv

CONFIRMED = "confirmed"


def _resolution(row: XbrlMemberResolutionORM) -> Resolution:
    return Resolution(
        member=row.member,
        product=row.product or None,
        method=row.method,
        confidence=row.confidence,
        note=row.note or "",
        verdict=row.verdict or "",
        candidates_fingerprint=row.candidates_fingerprint or "",
    )


def load_register(session: Session) -> dict[tuple[str, str], Resolution]:
    """Every decision on record, keyed the way ``members.resolve`` wants it."""
    seed_from_csv(session)
    return {
        (row.issuer, row.member): _resolution(row)
        for row in session.scalars(select(XbrlMemberResolutionORM))
    }


def seed_from_csv(session: Session, path: Path | None = None) -> int:
    """Load the reviewed file into an empty table. Idempotent, and a no-op after.

    Only ever fills an empty table: once a deployment is running, the table is
    ahead of the file, and re-reading the file over the top of it would undo
    both what runs learned and what reviewers settled.
    """
    if session.scalar(select(func.count()).select_from(XbrlMemberResolutionORM)):
        return 0
    seeded = [
        XbrlMemberResolutionORM(
            id=str(uuid.uuid4()),
            issuer=issuer,
            member=entry.member,
            product=None if entry.product == NOT_A_PRODUCT else entry.product,
            verdict=entry.claim,
            method=entry.method,
            confidence=entry.confidence,
            candidates_fingerprint=entry.candidates_fingerprint or None,
            note=entry.note,
        )
        for (issuer, _member), entry in sorted(load_register_csv(path).items())
    ]
    if not seeded:
        return 0
    try:
        # A savepoint rather than the transaction: this runs inside a job's
        # session, which is holding that job's sources and datapoints. Losing
        # a race to seed must not take them with it.
        with session.begin_nested():
            session.add_all(seeded)
    except IntegrityError:
        # Another worker seeded it between the count and the insert. Theirs is
        # the same file, so there is nothing to reconcile.
        return 0
    return len(seeded)


def record(
    session: Session,
    issuer: str,
    resolution: Resolution,
    *,
    products: list[str] | None = None,
) -> bool:
    """Write one decision down. Returns whether anything changed.

    A negative is stored with the fingerprint of the list it was judged against,
    because that is the only thing that makes it safe to store at all. A row a
    person confirmed is left exactly as it is.
    """
    existing = session.scalar(
        select(XbrlMemberResolutionORM).where(
            XbrlMemberResolutionORM.issuer == issuer,
            XbrlMemberResolutionORM.member == resolution.member,
        )
    )
    if existing is not None and existing.validation_status == CONFIRMED:
        return False
    verdict = resolution.claim
    mark = (
        resolution.candidates_fingerprint
        or (fingerprint(products) if products is not None and verdict == VERDICT_NO_CANDIDATE_MATCH else "")
    )
    fields = {
        "product": None if resolution.product == NOT_A_PRODUCT else resolution.product,
        "verdict": verdict,
        "method": resolution.method,
        "confidence": resolution.confidence,
        "candidates_fingerprint": mark or None,
        "note": resolution.note,
    }
    if existing is not None:
        if all(getattr(existing, key) == value for key, value in fields.items()):
            return False
        for key, value in fields.items():
            setattr(existing, key, value)
        return True
    session.add(
        XbrlMemberResolutionORM(
            id=str(uuid.uuid4()), issuer=issuer, member=resolution.member, **fields
        )
    )
    return True


def record_many(
    session: Session,
    entries: dict[tuple[str, str], Resolution],
    *,
    products: list[str] | None = None,
) -> int:
    try:
        with session.begin_nested():
            written = sum(
                record(session, issuer, entry, products=products)
                for (issuer, _member), entry in sorted(entries.items())
            )
    except IntegrityError:
        # Another worker wrote the same member first. It read the same filing
        # through the same rules, so there is nothing here worth a retry - and
        # a job's own extraction is not worth losing to a bookkeeping race.
        return 0
    return written


def run_products(session: Session, run_id: str) -> list[str]:
    """The drugs this run was asked about.

    These belong in the candidate list for the same reason the tracked products
    do: ``match`` picks the longest product name ending a member, so a sibling
    missing from the list is a sibling whose revenue can be handed to its
    neighbour. A run's own uploads are the products most likely to be siblings
    of each other.
    """
    return sorted(
        {
            name
            for name in session.scalars(
                select(DrugJobORM.drug_name).where(DrugJobORM.run_id == run_id)
            )
            if name
        }
    )
