from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import main
from app.api import products as products_api
from app.db.migrations import upgrade_database
from app.db.models import (
    CanonicalProductORM,
    DatapointORM,
    DrugJobORM,
    DrugProfileFieldORM,
    ExtractionRunORM,
    MoAComponentORM,
    ProductIndicationORM,
    UnresolvedQuarterORM,
    ValidationTaskORM,
)


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    upgrade_database(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(products_api, "SessionLocal", session_factory)

    older = datetime(2026, 1, 5, 9, 0, 0)
    newer = datetime(2026, 9, 11, 6, 0, 0)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run-old", status="completed"))
        db.add(ExtractionRunORM(id="run-new", status="completed"))
        db.add(
            CanonicalProductORM(
                id="prod-1",
                canonical_name="Opsumit",
                identity_key="macitentan|tablet",
                active_moieties_json=["macitentan"],
                current_commercial_owner="Actelion / J&J",
                cadence="quarterly",
            )
        )
        db.add(
            CanonicalProductORM(
                id="prod-2",
                canonical_name="Yutrepia",
                identity_key="treprostinil|powder",
                active_moieties_json=["treprostinil"],
                current_commercial_owner="Liquidia",
            )
        )
        db.add(
            ProductIndicationORM(
                id="ind-1",
                product_id="prod-1",
                disease="Pulmonary arterial hypertension",
                therapeutic_area="Pulmonary vascular disease",
                approved_lot="all_lines_or_unspecified",
            )
        )
        db.add(
            MoAComponentORM(
                id="moa-1",
                product_id="prod-1",
                moa_term="Endothelin receptor antagonist",
                fda_epc_terms_json=["Endothelin Receptor Antagonist [EPC]"],
            )
        )
        # Two jobs for prod-1: the newer one is the product's current state.
        db.add(
            DrugJobORM(
                id="job-old", run_id="run-old", product_id="prod-1", drug_name="Opsumit",
                status="completed", completeness_pct=70.0, created_at=older,
            )
        )
        db.add(
            DrugJobORM(
                id="job-new", run_id="run-new", product_id="prod-1", drug_name="Opsumit",
                status="ready_for_review", completeness_pct=96.0, created_at=newer,
            )
        )
        db.add(
            DrugJobORM(
                id="job-yut", run_id="run-new", product_id="prod-2", drug_name="Yutrepia",
                status="ready_for_review", completeness_pct=0.0,
                created_at=newer + timedelta(minutes=1),
            )
        )
        db.add(
            DrugProfileFieldORM(
                id="pf-1", job_id="job-new", field="approved_lot",
                value="all_lines_or_unspecified", validation_status="auto_pass",
            )
        )
        db.add(
            DatapointORM(
                id="dp-1", job_id="job-new", period="2026Q2",
                value_normalized_usd_millions=186.4, revenue_scope="U.S.",
                source_url="https://sec.gov/opsumit-10q", source_quote="Opsumit 186.4",
                extraction_method="xbrl_tagged", confidence_score=0.97,
                validation_status="needs_review",
            )
        )
        db.add(
            ValidationTaskORM(
                id="vt-1", job_id="job-new", datapoint_id="dp-1",
                reason="recent_period", confidence_score=0.97, status="open",
            )
        )
        db.add(
            UnresolvedQuarterORM(
                id="uq-1", job_id="job-new", period="2025Q1",
                reason_unresolved="No reliable product-level quarterly value extracted",
                sources_checked=["https://sec.gov/opsumit-10k"],
                recommended_next_step="Check SEC 10-Q MD&A",
                confidence_that_unavailable=0.3,
            )
        )
        db.add(
            UnresolvedQuarterORM(
                id="uq-2", job_id="job-yut", period="product_revenue",
                reason_unresolved="Product-specific revenue not disclosed",
                sources_checked=["https://ir.liquidia.com"],
                recommended_next_step="Provide IR URL, or confirm non-disclosure",
                confidence_that_unavailable=0.7,
            )
        )
        db.commit()

    return TestClient(main.app)


def test_library_lists_products_with_queue_counts_from_the_newest_job(client):
    body = client.get("/products").json()

    assert body["total"] == 2
    opsumit = next(row for row in body["products"] if row["name"] == "Opsumit")
    # 96.0 is the newer job; the older one at 70.0 must not win.
    assert opsumit["completeness_pct"] == 96.0
    assert opsumit["cadence"] == "quarterly"
    assert opsumit["flagged"] == 1
    assert opsumit["missing"] == 1
    assert opsumit["quarters"] == 1
    assert opsumit["moa"] == "Endothelin receptor antagonist"
    assert opsumit["indication"] == "Pulmonary arterial hypertension"

    yutrepia = next(row for row in body["products"] if row["name"] == "Yutrepia")
    assert yutrepia["cadence"] == "one_off"
    assert yutrepia["missing"] == 1


def test_library_filters_by_cadence_and_search(client):
    quarterly = client.get("/products", params={"cadence": "quarterly"}).json()
    assert [row["name"] for row in quarterly["products"]] == ["Opsumit"]

    searched = client.get("/products", params={"q": "treprostinil"}).json()
    assert [row["name"] for row in searched["products"]] == ["Yutrepia"]


def test_product_detail_carries_profile_quarters_and_every_run(client):
    body = client.get("/products/prod-1").json()

    assert body["name"] == "Opsumit"
    assert body["approved_lot"] == "all_lines_or_unspecified"
    assert body["epc"] == ["Endothelin Receptor Antagonist [EPC]"]
    assert [field["field"] for field in body["profile"]] == ["approved_lot"]

    quarter = body["quarters"][0]
    assert quarter["period"] == "2026Q2"
    assert quarter["in_queue"] is True
    assert quarter["queue_reason"] == "recent_period"
    assert quarter["source_url"] == "https://sec.gov/opsumit-10q"

    assert [row["period"] for row in body["missing_quarters"]] == ["2025Q1"]
    # Both runs appear, newest first, so the product keeps its history.
    assert [entry["job_id"] for entry in body["timeline"]] == ["job-new", "job-old"]


def test_unknown_product_is_404(client):
    assert client.get("/products/nope").status_code == 404


def test_cadence_is_settable_singly_and_in_bulk(client):
    assert client.patch("/products/prod-2", json={"cadence": "quarterly"}).status_code == 200
    assert client.get("/products/prod-2").json()["cadence"] == "quarterly"

    assert client.patch("/products/prod-2", json={"cadence": "hourly"}).status_code == 400

    bulk = client.post(
        "/products/cadence",
        json={"product_ids": ["prod-1", "prod-2"], "cadence": "one_off"},
    )
    assert bulk.json()["updated"] == 2
    assert client.get("/products/prod-1").json()["cadence"] == "one_off"


def test_review_queue_carries_both_streams_with_their_real_reasons(client):
    body = client.get("/review/queue").json()

    assert body["flagged"] == 1
    assert body["missing"] == 2

    flagged = next(item for item in body["items"] if item["type"] == "flagged")
    assert flagged["reason"] == "recent_period"
    assert flagged["product"] == "Opsumit"
    assert flagged["source_quote"] == "Opsumit 186.4"

    whole_product = next(item for item in body["items"] if item["period"] == "product_revenue")
    assert whole_product["reason"] == "not_disclosed"
    assert whole_product["product"] == "Yutrepia"
    assert whole_product["sources_checked"] == ["https://ir.liquidia.com"]

    gap = next(item for item in body["items"] if item["period"] == "2025Q1")
    assert gap["reason"] == "interior_gap"


def test_review_queue_filters_to_one_product(client):
    body = client.get("/review/queue", params={"product_id": "prod-2"}).json()
    assert body["total"] == 1
    assert body["items"][0]["product"] == "Yutrepia"

    flagged_only = client.get("/review/queue", params={"item_type": "flagged"}).json()
    assert flagged_only["missing"] == 0
    assert flagged_only["total"] == 1


def test_entering_a_value_without_a_citation_is_refused(client):
    response = client.post(
        "/unresolved-quarters/uq-1/actions",
        json={"action": "enter_value", "value_normalized_usd_millions": 170.0},
    )
    assert response.status_code == 400
    assert "citation" in response.json()["detail"]
    # Still open, because nothing was written.
    assert client.get("/review/queue").json()["missing"] == 2


def test_entering_a_cited_value_publishes_it_and_closes_the_gap(client):
    response = client.post(
        "/unresolved-quarters/uq-1/actions",
        json={
            "action": "enter_value",
            "value_normalized_usd_millions": 170.0,
            "revenue_scope": "U.S.",
            "source_url": "https://sec.gov/opsumit-10k",
            "source_quote": "Opsumit 170.0",
            "reviewer_notes": "Read off the FY schedule",
        },
    )
    assert response.status_code == 200
    assert response.json()["resolution"] == "value_entered"

    detail = client.get("/products/prod-1").json()
    entered = next(q for q in detail["quarters"] if q["period"] == "2025Q1")
    assert entered["value_normalized_usd_millions"] == 170.0
    assert entered["validation_status"] == "confirmed"
    assert entered["source_url"] == "https://sec.gov/opsumit-10k"
    assert detail["missing_quarters"] == []

    assert client.get("/review/queue").json()["missing"] == 1


def test_confirming_non_disclosure_closes_the_item_without_inventing_a_value(client):
    response = client.post(
        "/unresolved-quarters/uq-2/actions",
        json={"action": "not_disclosed", "reviewer_notes": "Issuer does not break it out"},
    )
    assert response.json()["resolution"] == "not_disclosed"
    assert response.json()["datapoint_id"] is None

    body = client.get("/review/queue").json()
    assert body["missing"] == 1
    assert all(item["period"] != "product_revenue" for item in body["items"])


def test_profile_field_edit_requires_a_citation_and_confirms_the_field(client):
    refused = client.patch(
        "/profile-fields/pf-1", json={"value": "1L", "source_url": "   "}
    )
    assert refused.status_code == 400

    accepted = client.patch(
        "/profile-fields/pf-1",
        json={
            "value": "1L",
            "source_url": "https://dailymed.nlm.nih.gov/spl",
            "reviewer_notes": "Label states it plainly",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["validation_status"] == "confirmed"

    field = client.get("/products/prod-1").json()["profile"][0]
    assert field["value"] == "1L"
    assert field["citation"]["source_url"] == "https://dailymed.nlm.nih.gov/spl"
