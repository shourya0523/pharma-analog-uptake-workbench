"""Ask the pipeline what a filer's XBRL member names.

The same chain a run walks when it reads an instance, exposed so it can be
asked from outside: the register first where its decision still binds, then
the string rules, then the model for what the rules cannot place. Nothing
asked here is written down - a question is not a decision, and the register
must not learn an answer key's members by being scored against them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.models import SessionLocal
from app.extraction import member_store
from app.extraction.members import Resolution, match, stored
from app.llm.client import LLMModules

router = APIRouter(tags=["members"])


class MemberQuestion(BaseModel):
    issuer: str
    member: str
    siblings: list[str] = []
    candidates: list[str]


def _answer(resolution: Resolution, decided_by: str) -> dict[str, Any]:
    return {
        "member": resolution.member,
        "product": resolution.product if resolution.resolved else None,
        "method": resolution.method,
        "confidence": resolution.confidence,
        "note": resolution.note,
        "decided_by": decided_by,
    }


@router.post("/members/resolve")
async def resolve_member(body: MemberQuestion) -> dict[str, Any]:
    db = SessionLocal()
    try:
        register = member_store.load_register(db)
    finally:
        db.close()
    entry = stored(register, body.issuer, body.member)
    if entry is not None and entry.binds(body.candidates):
        return _answer(entry, "register")
    outcome = match(body.member, body.candidates)
    if outcome.resolved:
        return _answer(outcome, "rules")
    reply = await LLMModules().resolve_xbrl_member(
        issuer=body.issuer,
        member=body.member,
        siblings=body.siblings,
        candidates=body.candidates,
    )
    if not reply:
        # No model configured, or nothing to choose from: the rules' refusal
        # stands, and says why.
        return _answer(outcome, "rules")
    product = reply.get("product") or None
    return _answer(
        Resolution(
            member=body.member,
            product=product,
            method="llm",
            confidence=float(reply.get("confidence") or 0.0),
            note=str(reply.get("reason") or "")[:200],
        ),
        "model",
    )
