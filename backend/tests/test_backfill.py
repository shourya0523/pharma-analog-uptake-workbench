from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.migrations import upgrade_database
from app.db.models import (
    CanonicalProductORM,
    DrugJobORM,
    DrugProfileFieldORM,
    ExtractionRunORM,
    MoAComponentORM,
)
from app.remediation.backfill import backfill_job


def test_backfill_is_idempotent_and_preserves_confirmed_moa():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(
            DrugJobORM(
                id="job", run_id="run", drug_name="Example", generic_name="ingredient",
                manufacturer="Acme", status="ready_for_review",
            )
        )
        db.add_all(
            [
                DrugProfileFieldORM(
                    id="moa", job_id="job", field="moa", value="Confirmed molecular mechanism",
                    citation_json={"source_url": "https://example.test/label", "source_quote": "mechanism"},
                    validation_status="confirmed",
                ),
                DrugProfileFieldORM(
                    id="epc", job_id="job", field="pharmacologic_class", value="Some Class [EPC]",
                    citation_json={"source_url": "https://example.test/label", "source_quote": "openfda.pharm_class_epc"},
                    validation_status="needs_review",
                ),
            ]
        )
        db.commit()

        first = backfill_job(db, "job")
        second = backfill_job(db, "job")

        assert first.product_id == second.product_id
        assert db.query(CanonicalProductORM).count() == 1
        assert db.query(MoAComponentORM).count() == 1
        assert db.query(MoAComponentORM).one().moa_term == "Confirmed molecular mechanism"


def test_backfill_never_uses_epc_as_moa():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(DrugJobORM(id="job", run_id="run", drug_name="EPC only", status="completed"))
        db.add(
            DrugProfileFieldORM(
                id="epc", job_id="job", field="pharmacologic_class", value="Some Class [EPC]",
                citation_json={"source_url": "https://example.test/label"},
            )
        )
        db.commit()
        backfill_job(db, "job")
        assert db.query(MoAComponentORM).count() == 0

