from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import main
from app.db.migrations import upgrade_database
from app.db.models import DrugJobORM, ExtractionRunORM


def test_dashboard_job_and_observability_routes_share_compatible_contract(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    upgrade_database(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", session_factory)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(
            DrugJobORM(
                id="job", run_id="run", drug_name="Example",
                status="ready_for_review", completeness_pct=100,
            )
        )
        db.commit()

    client = TestClient(main.app)
    dashboard = client.get("/dashboard/preview", params={"run_id": "run"})
    job = client.get("/jobs/job")
    observability = client.get("/observability")

    assert dashboard.status_code == job.status_code == observability.status_code == 200
    assert dashboard.json()["products"][0]["job_id"] == job.json()["id"]
    assert {"products", "series", "launch_series", "kpis"} <= dashboard.json().keys()
    assert "canonical_products" in observability.json()["available_tables"]
    assert "drug_profile_fields" in observability.json()["available_tables"]

