"""The member resolver can be asked through the API, and answers the way a run does.

seed/holdout_members has no scorer since the eval consolidation deleted the
script that read it; `scripts/eval.py --members` now scores it through this
route, so the route must walk the same chain a run walks - register, rules,
model - and must never write what it was asked into the register.
"""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import main
from app.api import members as members_api
from app.db.migrations import upgrade_database
from app.db.models import XbrlMemberResolutionORM

ACME = ["Calderon", "Calderon XR", "Nebulized Calderon", "NuVessa"]


def _client(monkeypatch, *, rows: list[XbrlMemberResolutionORM] = ()):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    upgrade_database(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(members_api, "SessionLocal", factory)
    with Session(engine) as db:
        db.add_all(rows)
        db.commit()
    return TestClient(main.app), factory


class _NoModel:
    async def resolve_xbrl_member(self, **_kwargs):
        return {}


class _Model:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    async def resolve_xbrl_member(self, **kwargs):
        self.asked.append(kwargs)
        return self.answer


def test_the_rules_answer_before_any_model_is_asked(monkeypatch):
    client, _ = _client(monkeypatch)
    model = _Model({"product": "NuVessa", "confidence": 0.99, "reason": "no"})
    monkeypatch.setattr(members_api, "LLMModules", lambda: model)
    got = client.post("/members/resolve", json={
        "issuer": "Acme", "member": "acme:NebulizedCalderonMember",
        "siblings": ["acme:CalderonMember"], "candidates": ACME,
    }).json()
    assert got["product"] == "Nebulized Calderon"
    assert got["decided_by"] == "rules"
    assert not model.asked


def test_a_binding_register_entry_answers_first(monkeypatch):
    row = XbrlMemberResolutionORM(
        id=str(uuid.uuid4()), issuer="Acme", member="acme:RespiratoryFranchiseMember",
        product="Calderon", verdict="product", method="reviewer", confidence=1.0,
    )
    client, _ = _client(monkeypatch, rows=[row])
    monkeypatch.setattr(members_api, "LLMModules", _NoModel)
    got = client.post("/members/resolve", json={
        "issuer": "Acme", "member": "acme:RespiratoryFranchiseMember", "candidates": ACME,
    }).json()
    assert got["product"] == "Calderon"
    assert got["decided_by"] == "register"


def test_what_the_rules_decline_goes_to_the_model_and_is_not_recorded(monkeypatch):
    """A joined name is the model's question; its answer is returned, not kept."""
    model = _Model({"product": "Calderon", "confidence": 0.9,
                    "reason": "a brand and its extended-release form"})
    client, factory = _client(monkeypatch)
    monkeypatch.setattr(members_api, "LLMModules", lambda: model)
    got = client.post("/members/resolve", json={
        "issuer": "Acme", "member": "acme:CalderonAndCalderonXRMember",
        "siblings": ["acme:NuVessaMember"], "candidates": ACME,
    }).json()
    assert got["product"] == "Calderon"
    assert got["decided_by"] == "model"
    assert model.asked[0]["siblings"] == ["acme:NuVessaMember"]
    with factory() as db:
        learned = db.scalars(select(XbrlMemberResolutionORM).where(
            XbrlMemberResolutionORM.issuer == "Acme")).all()
    assert not learned, "a question through the API must not become a register entry"


def test_a_hedging_model_is_a_refusal(monkeypatch):
    model = _Model({"product": "Calderon", "confidence": 0.4, "reason": "perhaps"})
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(members_api, "LLMModules", lambda: model)
    got = client.post("/members/resolve", json={
        "issuer": "Acme", "member": "acme:CalderonAndCalderonXRMember", "candidates": ACME,
    }).json()
    assert got["product"] is None
    assert got["decided_by"] == "model"


def test_without_a_model_the_rules_refusal_stands(monkeypatch):
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(members_api, "LLMModules", _NoModel)
    got = client.post("/members/resolve", json={
        "issuer": "Acme", "member": "acme:CalderonAndCalderonXRMember", "candidates": ACME,
    }).json()
    assert got["product"] is None
    assert got["decided_by"] == "rules"
    assert got["method"] == "joined"
