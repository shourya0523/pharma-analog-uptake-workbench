"""What the openFDA step writes, and what it says about where it came from.

Two records answer for one product and they are not the same shape: the
drugsFDA application carries `products[]` and `submissions[]`, the SPL label
carries prose sections and no `products` array. The fixtures below are that
pair, with invented names.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    CanonicalProductORM,
    DrugJobORM,
    DrugProfileFieldORM,
    ExtractionRunORM,
    ProductIndicationORM,
    sqlite_connect_args,
)
from app.domain.models import RetrievalStatus, RetrievedSource, SourceType, new_id
from app.parsing.fda_label import read_path
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.filestore import LocalFileStore

# One brand, two applications: the original and a later line extension by a
# different route. The extension is listed first, as openFDA may return it.
LINE_EXTENSION = {
    "application_number": "NDA000006",
    "sponsor_name": "ACME THERAPEUTICS",
    "openfda": {"brand_name": ["CALDERON"], "generic_name": ["CALDERINOL"], "route": ["ORAL"]},
    "products": [{"brand_name": "CALDERON", "route": "INTRAVENOUS", "dosage_form": "POWDER"}],
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20210729"},
    ],
}
ORIGINAL = {
    "application_number": "NDA000007",
    "sponsor_name": "ACME THERAPEUTICS",
    "openfda": {"brand_name": ["CALDERON"], "generic_name": ["CALDERINOL"], "route": ["ORAL"]},
    "products": [{"brand_name": "CALDERON", "route": "ORAL", "dosage_form": "TABLET"}],
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20151221"},
    ],
}
LABEL_RECORD = {
    "openfda": {"brand_name": ["CALDERON"], "generic_name": ["CALDERINOL"]},
    "indications_and_usage": [
        (
            "1 INDICATIONS AND USAGE CALDERON is indicated for the treatment of "
            "Calderon's disease (CD) (WHO Group 1) to improve exercise ability."
        )
    ],
    "mechanism_of_action": ["12.1 Mechanism of Action Calderinol is a vasodilator."],
}


class NoModel:
    async def extract_metadata(self, **_):
        return {"fields": []}

    async def judge_profile_field(self, **_):
        return {}


def _orchestrator(tmp_path, results, label_results=(LABEL_RECORD,)):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workbench.db'}", connect_args=sqlite_connect_args("sqlite://")
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    run = ExtractionRunORM(id=new_id(), status="running", options_json={})
    db.add(run)
    job = DrugJobORM(
        id=new_id(),
        run_id=run.id,
        drug_name="Calderon",
        generic_name="calderinol",
        manufacturer="Acme Therapeutics",
        status="running",
        quality_flags=[],
    )
    db.add(job)
    db.commit()
    orch = PipelineOrchestrator(db, file_store=LocalFileStore(str(tmp_path)))
    orch.llm = NoModel()
    sources = [
        RetrievedSource(
            source_id="drugsfda",
            source_type=SourceType.OPENFDA,
            url="https://api.fda.gov/drug/drugsfda.json?search=x",
            retrieval_status=RetrievalStatus.SUCCESS,
            metadata={"results": list(results)},
        ),
        RetrievedSource(
            source_id="label",
            source_type=SourceType.OPENFDA,
            url="https://api.fda.gov/drug/label.json?search=x",
            retrieval_status=RetrievalStatus.SUCCESS,
            metadata={"results": list(label_results)},
        ),
    ]
    return db, orch, job, sources


def _fields(db, job):
    return {row.field: row for row in db.query(DrugProfileFieldORM).filter_by(job_id=job.id)}


@pytest.mark.asyncio
async def test_every_openfda_citation_names_a_key_the_record_states(tmp_path):
    db, orch, job, sources = _orchestrator(tmp_path, [ORIGINAL])
    await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

    records = {"drugsfda": ORIGINAL, "label": LABEL_RECORD}
    rows = _fields(db, job)
    assert rows, "the step wrote nothing, so nothing was tested"
    for name, row in rows.items():
        citation = row.citation_json
        if citation.get("source_type") != SourceType.OPENFDA.value:
            continue
        path = citation["source_field"]
        record = records[citation["source_id"]]
        if name == "fda_approval_date":
            assert "submissions" in path and record.get("submissions")
            continue
        assert read_path(record, path), f"{name} cites {path}, which the record does not state"


@pytest.mark.asyncio
async def test_the_route_read_is_the_applications_own_and_the_other_is_kept(tmp_path):
    db, orch, job, sources = _orchestrator(tmp_path, [LINE_EXTENSION])
    await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

    roa = _fields(db, job)["roa"]
    assert roa.value == "INTRAVENOUS"
    assert roa.citation_json["source_field"] == "products[].route"
    rival = roa.citation_json["conflicting_source"]
    assert rival["value"] == "ORAL" and rival["source_field"] == "openfda.route"
    assert "openfda_conflicting_reading:roa" in job.quality_flags


@pytest.mark.asyncio
async def test_the_approval_is_the_earliest_across_every_matching_application(tmp_path):
    for n, order in enumerate(([LINE_EXTENSION, ORIGINAL], [ORIGINAL, LINE_EXTENSION])):
        directory = tmp_path / str(n)
        directory.mkdir()
        db, orch, job, sources = _orchestrator(directory, order)
        await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

        rows = _fields(db, job)
        approval = rows["fda_approval_date"]
        assert approval.value == "2015-12-21"
        # The route comes from that same application, whichever came back first.
        assert rows["roa"].value == "ORAL"
        assert approval.citation_json["openfda_application_number"] == "NDA000007"
        assert "submissions" in approval.citation_json["source_field"]
        assert sorted(approval.citation_json["openfda_matched_applications"]) == [
            "NDA000006",
            "NDA000007",
        ]


@pytest.mark.asyncio
async def test_the_canonical_row_and_every_indication_carry_the_launch_anchor(tmp_path):
    """The date is on one record and the indications on the other."""
    db, orch, job, sources = _orchestrator(tmp_path, [ORIGINAL])
    await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

    product = db.query(CanonicalProductORM).filter_by(id=job.product_id).one()
    assert product.initial_approval_date == date(2015, 12, 21)

    rows = db.query(ProductIndicationORM).filter_by(product_id=product.id).all()
    assert rows, "no indication was parsed, so the anchor was not tested"
    assert all(row.approval_date == date(2015, 12, 21) for row in rows)
    assert all(row.launch_anchor_type == "indication_approval_date" for row in rows)


@pytest.mark.asyncio
async def test_an_application_with_no_approval_leaves_the_anchor_unset(tmp_path):
    undated = {**ORIGINAL, "submissions": []}
    db, orch, job, sources = _orchestrator(tmp_path, [undated])
    await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

    product = db.query(CanonicalProductORM).filter_by(id=job.product_id).one()
    assert product.initial_approval_date is None
    rows = db.query(ProductIndicationORM).filter_by(product_id=product.id).all()
    assert rows and all(row.approval_date is None for row in rows)


@pytest.mark.asyncio
async def test_one_job_files_one_canonical_product(tmp_path):
    """The label record states no formulation, so it cannot key the product."""
    db, orch, job, sources = _orchestrator(tmp_path, [ORIGINAL])
    await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

    products = db.query(CanonicalProductORM).all()
    assert [p.id for p in products] == [job.product_id]


@pytest.mark.asyncio
async def test_the_area_groups_where_the_indication_does_not(tmp_path):
    db, orch, job, sources = _orchestrator(tmp_path, [ORIGINAL])
    await orch._extract_metadata(job, sources, {}, {"product_metadata": True})

    rows = _fields(db, job)
    assert rows["indication"].value.startswith("Calderon's disease (CD)")
    assert rows["therapeutic_area"].value == "calderon's disease"
    assert rows["therapeutic_area"].citation_json["source_field"] == "indications_and_usage"
