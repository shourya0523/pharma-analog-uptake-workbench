from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.dashboard.series import build_dashboard_preview
from app.db.migrations import upgrade_database
from app.db.models import (
    CanonicalProductORM,
    CompetitiveSnapshotORM,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    MoAComponentORM,
    PeakSalesEstimateORM,
    ProductIndicationORM,
    UptakeMetricORM,
)


def test_dashboard_projection_extends_legacy_contract_with_normalized_metrics():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(
            DrugJobORM(
                id="job", run_id="run", drug_name="Example", manufacturer="Acme",
                status="ready_for_review", completeness_pct=90,
            )
        )
        db.add(
            DatapointORM(
                id="dp", job_id="job", period="2024Q1", value_normalized_usd_millions=10,
                currency="USD", source_url="https://example.test/sales", source_quote="Example sales $10",
                period_type="quarterly", revenue_scope="Product family",
            )
        )
        db.add(
            CanonicalProductORM(
                id="product", canonical_name="Example", identity_key="example:stable",
                current_commercial_owner="Acme", initial_approval_date=date(2020, 1, 1),
            )
        )
        db.add(
            ProductIndicationORM(
                id="indication", product_id="product", disease="Disease A", therapeutic_area="Oncology",
                setting="metastatic", approved_lot="2L+", approval_date=date(2020, 1, 1),
                commercial_launch_date=date(2020, 2, 1), launch_anchor_type="commercial_launch_date",
            )
        )
        db.add(MoAComponentORM(id="moa", product_id="product", moa_term="Kinase inhibitor"))
        db.add(
            PeakSalesEstimateORM(
                id="peak", product_id="product", estimate_type="consensus", value=750,
                currency="USD", geography="Worldwide", revenue_scope="Product family",
                as_of_date=date(2026, 8, 1), selected=True, selection_reason="policy",
            )
        )
        db.add(
            CompetitiveSnapshotORM(
                id="competition", indication_id="indication", geography="U.S.",
                as_of_date=date(2020, 2, 1), formula_version="competitive_intensity_v1",
                raw_score=3, category="medium", cohort_size=4, low_coverage=True,
            )
        )
        db.add(
            UptakeMetricORM(
                id="uptake", indication_id="indication", peak_estimate_id="peak",
                metric_type="revenue_proxy_r4q", period="2021Q1", months_since_launch=11,
                numerator_value=100, denominator_value=750, value=0.133,
                input_ids_json=["dp"],
            )
        )
        db.commit()

        payload = build_dashboard_preview(db, run_id="run")

    product = payload["products"][0]
    assert {"products", "series", "filter_options", "analog_count"} <= payload.keys()
    assert product["moa"] == "Kinase inhibitor"
    assert product["approved_lot"] == "2L+"
    assert product["selected_peak"]["type"] == "consensus"
    assert product["competitive_intensity"] == "medium"
    assert payload["kpis"]["aggregate_selected_peak"]["value"] == 750
    assert payload["kpis"]["uptake_ready_products"] == 1
    assert payload["launch_series"][0]["months_since_launch"] == 11
    assert payload["launch_series"][0]["citation"]["input_ids"] == ["dp"]
    assert "approval_period" in payload["filter_options"]


def test_dashboard_moa_does_not_fall_back_to_epc():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        job = DrugJobORM(id="job", run_id="run", drug_name="EPC only", status="ready_for_review")
        db.add(job)
        from app.db.models import DrugProfileFieldORM

        db.add(
            DrugProfileFieldORM(
                id="epc", job_id="job", field="pharmacologic_class",
                value="Endothelin Receptor Antagonist [EPC]",
            )
        )
        db.commit()
        product = build_dashboard_preview(db, run_id="run")["products"][0]
    assert product["moa"] is None
    assert product["pharmacologic_class"] == "Endothelin Receptor Antagonist [EPC]"

