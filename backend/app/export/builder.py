from __future__ import annotations

import csv
import io
import json

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.dashboard.series import build_dashboard_preview
from app.db.models import (
    DatapointORM,
    DrugJobORM,
    ExportORM,
)
from app.domain.models import PUBLISHED_STATUS_VALUES, PeriodType, new_id
from app.storage.filestore import FileStore

# The one column of the datapoint the sheets do not carry: the citation
# record, whose own fields already have columns beside it and which is JSON
# rather than a cell. If it ever holds something no other column does, it
# belongs on the sheet like the rest.
_NOT_A_CELL = "citation_json"


def _identifies_rather_than_describes(name: str) -> bool:
    """Whether a column joins its row to another row instead of describing it.

    The primary key and the two keys pointing at the job and the source. A
    reader of the sheet has the drug name and the source URL, and the ids
    say nothing they can act on.
    """
    return name == "id" or name.endswith("_id")


# What a datapoint is, taken from the mapped table rather than listed here.
# The list that stood in its place dropped three columns, of which
# `period_type` was the one that said whether a row was a quarter at all.
DATAPOINT_COLUMNS = [
    column.name
    for column in DatapointORM.__table__.columns
    if not _identifies_rather_than_describes(column.name) and column.name != _NOT_A_CELL
]

QUARTERLY_HEADERS = ["drug_name", *DATAPOINT_COLUMNS]


def _cell(value: object) -> object:
    """One datapoint field as a spreadsheet cell.

    A JSON column arrives as a list or a dict; everything else is already a
    scalar the writer can take.
    """
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value)
    return value


def datapoint_row(drug_name: str, datapoint: DatapointORM) -> list:
    """One datapoint as a row under ``QUARTERLY_HEADERS``."""
    return [drug_name, *(_cell(getattr(datapoint, name)) for name in DATAPOINT_COLUMNS)]


def is_published_quarter(datapoint: DatapointORM) -> bool:
    """Whether this row belongs in a file named for quarterly revenue.

    A quarter, and a figure the pipeline stands behind. A cumulative figure
    under a quarter's label and a figure still held for review are both real
    and both belong in the unfiltered sheet beside it, not in a curve.
    """
    return (
        datapoint.period_type == PeriodType.QUARTERLY.value
        and datapoint.validation_status in PUBLISHED_STATUS_VALUES
    )


PRODUCT_HEADERS = [
    "job_id",
    "canonical_product_id",
    "product_name",
    "company",
    "therapeutic_area",
    "fda_approval_date",
    "approved_indications",
    "indications_json",
    "moa",
    "pharmacologic_class",
    "roa",
    "approved_lot",
    "competitive_intensity",
    "competitive_raw_score",
    "competitive_formula_version",
    "competitive_cohort_size",
    "competitive_low_coverage",
    "peak_value",
    "peak_type",
    "peak_method",
    "peak_as_of_date",
    "peak_geography",
    "peak_revenue_scope",
    "peak_input_ids",
    "uptake_methodology",
    "source_url",
    "completeness_pct",
    "validation_status",
]


def product_export_rows(db: Session, run_id: str) -> tuple[list[str], list[list]]:
    payload = build_dashboard_preview(db, run_id=run_id)
    rows: list[list] = []
    for product in payload["products"]:
        peak = product.get("selected_peak") or {}
        competition = product.get("competitive_snapshot") or {}
        values = {
            "job_id": product.get("job_id"),
            "canonical_product_id": product.get("canonical_product_id"),
            "product_name": product.get("product_name"),
            "company": product.get("company"),
            "therapeutic_area": product.get("therapeutic_area"),
            "fda_approval_date": product.get("fda_approval_date"),
            "approved_indications": product.get("approved_indications"),
            "indications_json": json.dumps(product.get("indications") or []),
            "moa": product.get("moa"),
            "pharmacologic_class": product.get("pharmacologic_class"),
            "roa": product.get("roa"),
            "approved_lot": product.get("approved_lot"),
            "competitive_intensity": product.get("competitive_intensity"),
            "competitive_raw_score": competition.get("raw_score"),
            "competitive_formula_version": competition.get("formula_version"),
            "competitive_cohort_size": competition.get("cohort_size"),
            "competitive_low_coverage": competition.get("low_coverage"),
            "peak_value": peak.get("value"),
            "peak_type": peak.get("type"),
            "peak_method": peak.get("selection_reason"),
            "peak_as_of_date": peak.get("as_of_date"),
            "peak_geography": peak.get("geography"),
            "peak_revenue_scope": peak.get("revenue_scope"),
            "peak_input_ids": json.dumps(peak.get("input_ids") or []),
            "uptake_methodology": "revenue_proxy_r4q" if product.get("uptake_ready") else None,
            "source_url": product.get("source_link"),
            "completeness_pct": product.get("completeness_score"),
            "validation_status": product.get("validation_status"),
        }
        rows.append([values[header] for header in PRODUCT_HEADERS])
    return PRODUCT_HEADERS, rows


class ExportBuilder:
    def __init__(self, db: Session, file_store: FileStore) -> None:
        self.db = db
        self.file_store = file_store

    async def export_product_workbook(self, job_id: str) -> ExportORM:
        job = self.db.get(DrugJobORM, job_id)
        if not job:
            raise ValueError("job not found")
        wb = Workbook()

        # Sheet 1: every datapoint the job holds, each saying what period it
        # covers and what it was reported as.
        ws = wb.active
        ws.title = "Quarterly Revenue"
        ws.append(QUARTERLY_HEADERS)
        for d in job.datapoints:
            ws.append(datapoint_row(job.drug_name, d))

        ws2 = wb.create_sheet("Source Audit Log")
        ws2.append(
            [
                "source_id",
                "drug_name",
                "source_type",
                "source_title",
                "source_url",
                "source_date",
                "filing_type",
                "accession_number",
                "page_or_section",
                "retrieval_status",
                "parsing_status",
                "relevant_datapoints_found",
                "notes",
            ]
        )
        for s in job.sources:
            ws2.append(
                [
                    s.id,
                    job.drug_name,
                    s.source_type,
                    s.source_title,
                    s.source_url,
                    s.source_date,
                    s.filing_type,
                    s.accession_number,
                    s.page_or_section,
                    s.retrieval_status,
                    s.parsing_status,
                    s.relevant_datapoints_found,
                    s.notes,
                ]
            )

        ws3 = wb.create_sheet("Unresolved Quarter Tracker")
        ws3.append(
            [
                "drug_name",
                "period",
                "reason_unresolved",
                "sources_checked",
                "recommended_next_step",
                "confidence_that_unavailable",
                "reviewer_notes",
            ]
        )
        for u in job.unresolved_quarters:
            ws3.append(
                [
                    job.drug_name,
                    u.period,
                    u.reason_unresolved,
                    ",".join(u.sources_checked or []),
                    u.recommended_next_step,
                    u.confidence_that_unavailable,
                    u.reviewer_notes,
                ]
            )

        ws4 = wb.create_sheet("Drug Profile")
        ws4.append(["field", "value", "source_url", "confidence", "validation_status"])
        for f in job.profile_fields:
            cit = f.citation_json or {}
            ws4.append([f.field, f.value, cit.get("source_url"), cit.get("confidence"), f.validation_status])

        ws5 = wb.create_sheet("Quality Checks")
        ws5.append(["issue_type", "severity", "affected_datapoint", "explanation", "recommended_action", "status"])
        for q in job.quality_checks:
            ws5.append([q.issue_type, q.severity, q.affected_datapoint, q.explanation, q.recommended_action, q.status])

        buf = io.BytesIO()
        wb.save(buf)
        key = f"exports/{job.run_id}/{job.id}/product_workbook.xlsx"
        await self.file_store.put(key, buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        exp = ExportORM(id=new_id(), run_id=job.run_id, job_id=job.id, format="product_workbook", storage_key=key)
        self.db.add(exp)
        self.db.commit()
        return exp

    async def export_powerbi_csvs(self, run_id: str) -> list[ExportORM]:
        jobs = self.db.query(DrugJobORM).filter_by(run_id=run_id).all()
        exports: list[ExportORM] = []

        def write_csv(name: str, headers: list[str], rows: list[list]) -> ExportORM:
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(headers)
            w.writerows(rows)
            key = f"exports/{run_id}/powerbi/{name}"
            # sync put via asyncio loop caller
            return key, out.getvalue().encode()

        # products
        product_headers, prod_rows = product_export_rows(self.db, run_id)
        key, data = write_csv(
            "products.csv",
            product_headers,
            prod_rows,
        )
        await self.file_store.put(key, data, "text/csv")
        exp = ExportORM(id=new_id(), run_id=run_id, format="products_csv", storage_key=key)
        self.db.add(exp)
        exports.append(exp)

        # Two files, because one of them is charted and the other is looked
        # things up in. quarterly_revenue.csv is what its name says and
        # nothing else, so a period axis built from it is a quarterly axis;
        # all_datapoints.csv is every row either file has ever held, so
        # filtering the first loses nothing.
        every_row = [(j.drug_name, d) for j in jobs for d in j.datapoints]
        for name, fmt, rows in (
            (
                "quarterly_revenue.csv",
                "quarterly_revenue_csv",
                [pair for pair in every_row if is_published_quarter(pair[1])],
            ),
            ("all_datapoints.csv", "all_datapoints_csv", every_row),
        ):
            key, data = write_csv(
                name,
                QUARTERLY_HEADERS,
                [datapoint_row(drug_name, d) for drug_name, d in rows],
            )
            await self.file_store.put(key, data, "text/csv")
            exp = ExportORM(id=new_id(), run_id=run_id, format=fmt, storage_key=key)
            self.db.add(exp)
            exports.append(exp)

        self.db.commit()
        return exports


class TemplateMapper:
    """Stub for future official Excel workbook mapping."""

    def infer(self, workbook_bytes: bytes) -> dict:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(workbook_bytes), read_only=True)
        sheets = {}
        for name in wb.sheetnames:
            ws = wb[name]
            rows = list(ws.iter_rows(max_row=1, values_only=True))
            headers = list(rows[0]) if rows else []
            sheets[name] = {"headers": headers}
        return {"sheets": sheets, "compatible": True, "unmapped_required": []}
