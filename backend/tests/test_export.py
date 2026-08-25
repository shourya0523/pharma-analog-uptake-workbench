from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.migrations import upgrade_database
from app.db.models import (
    CanonicalProductORM,
    DrugJobORM,
    ExtractionRunORM,
    MoAComponentORM,
    PeakSalesEstimateORM,
)
from app.export.builder import ExportBuilder, product_export_rows
from app.storage.filestore import LocalFileStore


def test_product_export_matches_normalized_dashboard_dimensions_and_lineage():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(DrugJobORM(id="job", run_id="run", drug_name="Example", status="completed"))
        db.add(CanonicalProductORM(id="product", canonical_name="Example", identity_key="example:key"))
        db.add(MoAComponentORM(id="moa", product_id="product", moa_term="Molecular mechanism"))
        db.add(
            PeakSalesEstimateORM(
                id="peak", product_id="product", estimate_type="consensus", value=500,
                currency="USD", geography="Worldwide", revenue_scope="Product family",
                as_of_date=date(2026, 8, 1), selected=True,
                selection_reason="current_harmonized_consensus_median_v1",
                input_ids_json=["source-1"],
            )
        )
        db.commit()
        headers, rows = product_export_rows(db, "run")

    row = dict(zip(headers, rows[0], strict=True))
    assert row["moa"] == "Molecular mechanism"
    assert row["pharmacologic_class"] is None
    assert row["peak_type"] == "consensus"
    assert row["peak_method"] == "current_harmonized_consensus_median_v1"
    assert row["peak_input_ids"] == '["source-1"]'
    assert "competitive_formula_version" in headers


async def test_powerbi_product_csv_uses_audited_normalized_headers(tmp_path):
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    store = LocalFileStore(str(tmp_path))
    with Session(engine) as db:
        db.add(ExtractionRunORM(id="run", status="completed"))
        db.add(DrugJobORM(id="job", run_id="run", drug_name="Example", status="completed"))
        db.commit()

        exports = await ExportBuilder(db, store).export_powerbi_csvs("run")
        products_export = next(item for item in exports if item.format == "products_csv")
        data = (await store.get(products_export.storage_key)).decode()

    headers = data.splitlines()[0].split(",")
    assert headers == product_export_rows_header()


def product_export_rows_header():
    from app.export.builder import PRODUCT_HEADERS

    return PRODUCT_HEADERS

