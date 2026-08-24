from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.sources import ManualURLConnector, SECConnector, TranscriptConnectorStub
from app.connectors.openfda import OpenFDAConnector
from app.db.models import (
    DatapointORM,
    DrugJobORM,
    DrugProfileFieldORM,
    ExtractionRunORM,
    QualityCheckORM,
    SourceDocumentORM,
    UnresolvedQuarterORM,
    ValidationTaskORM,
)
from app.domain.models import (
    JobStatus,
    JobStep,
    PeriodType,
    RetrievalStatus,
    SourceType,
    ValidationStatus,
    new_id,
)
from app.llm.client import LLMModules
from app.parsing.documents import DocumentParser
from app.quality.checks import apply_auto_pass_gate, run_quality_checks
from app.storage.filestore import FileStore, get_file_store
from app.validation.sampling import select_validation_tasks


SOURCE_PRIORITY = [
    SourceType.SEC_FILING,
    SourceType.EARNINGS_RELEASE,
    SourceType.INVESTOR_PRESENTATION,
    SourceType.ANNUAL_REPORT,
    SourceType.QUARTERLY_REPORT,
    SourceType.COMPANY_IR,
    SourceType.TRANSCRIPT,
    SourceType.USER_URL,
    SourceType.OTHER,
]


class PipelineOrchestrator:
    def __init__(self, db: Session, file_store: FileStore | None = None, llm: LLMModules | None = None) -> None:
        self.db = db
        self.file_store = file_store or get_file_store()
        self.llm = llm or LLMModules()
        self.sec = SECConnector(self.file_store)
        self.fda = OpenFDAConnector(self.file_store)
        self.manual = ManualURLConnector(self.file_store)
        self.transcripts = TranscriptConnectorStub()
        self.parser = DocumentParser(self.file_store)

    def _set_step(self, job: DrugJobORM, step: JobStep, status: JobStatus | None = None) -> None:
        job.current_step = step.value
        if status:
            job.status = status.value
        job.updated_at = datetime.utcnow()
        self.db.commit()

    async def run_job(self, job_id: str) -> None:
        job = self.db.get(DrugJobORM, job_id)
        if not job:
            return
        try:
            job.status = JobStatus.RUNNING.value
            self.db.commit()

            run = self.db.get(ExtractionRunORM, job.run_id)
            options = (run.options_json if run else {}) or {}
            await self._identity(job)
            sources = await self._retrieve(job, options)
            parsed = await self._parse(job, sources)
            await self._extract_metadata(job, sources, parsed, options)
            datapoint_rows = await self._extract_revenue(job, sources, parsed, options)
            await self._judge(job, datapoint_rows, sources, parsed, options)
            await self._quality_and_validation(job)
            await self._completeness(job)
            self._set_step(job, JobStep.READY_FOR_REVIEW, JobStatus.READY_FOR_REVIEW)
        except Exception as exc:
            job.status = JobStatus.FAILED.value
            job.error = str(exc)
            self.db.commit()
            raise

    async def _identity(self, job: DrugJobORM) -> None:
        self._set_step(job, JobStep.IDENTITY_RESOLVE)
        if not job.cik and (job.ticker or job.manufacturer):
            cik = await self.sec.resolve_cik(job.ticker, job.manufacturer)
            if cik:
                job.cik = cik
                self.db.commit()

    async def _retrieve(self, job: DrugJobORM, options: dict[str, Any]) -> list:
        self._set_step(job, JobStep.SOURCE_RETRIEVE)
        collected = []
        if options.get("sec_filings", True):
            collected.extend(
                await self.sec.retrieve(
                    run_id=job.run_id,
                    job_id=job.id,
                    cik=job.cik,
                    ticker=job.ticker,
                    company_name=job.manufacturer,
                )
            )
        if options.get("openfda", True):
            collected.extend(
                await self.fda.retrieve(
                    run_id=job.run_id,
                    job_id=job.id,
                    brand=job.drug_name,
                    generic=job.generic_name,
                )
            )
        if job.known_source_url and options.get("company_ir", True):
            collected.extend(
                await self.manual.retrieve(run_id=job.run_id, job_id=job.id, url=job.known_source_url)
            )
        if options.get("transcripts", False):
            collected.extend(await self.transcripts.retrieve())

        for src in collected:
            self.db.add(
                SourceDocumentORM(
                    id=src.source_id,
                    job_id=job.id,
                    source_type=src.source_type.value,
                    source_title=src.title,
                    source_url=src.url,
                    source_date=src.source_date.isoformat() if src.source_date else None,
                    filing_type=src.filing_type,
                    accession_number=src.accession_number,
                    retrieval_status=src.retrieval_status.value,
                    parsing_status="pending",
                    storage_key=src.storage_key,
                    notes=src.notes,
                    metadata_json=src.metadata,
                )
            )
        job.sources_found = len(collected)
        self.db.commit()
        return collected

    async def _parse(self, job: DrugJobORM, sources: list) -> dict[str, Any]:
        self._set_step(job, JobStep.PARSE_SOURCES)
        parsed_map: dict[str, Any] = {}
        for src in sources:
            doc = await self.parser.parse(src)
            parsed_map[src.source_id] = doc
            row = self.db.get(SourceDocumentORM, src.source_id)
            if row:
                row.parsing_status = doc.parsing_status.value
                row.page_or_section = doc.page_or_section
                if doc.notes:
                    row.notes = (row.notes or "") + f" | parse: {doc.notes}"
        self.db.commit()
        return parsed_map

    async def _extract_metadata(self, job: DrugJobORM, sources: list, parsed: dict, options: dict) -> None:
        if not options.get("product_metadata", True):
            return
        self._set_step(job, JobStep.EXTRACT_METADATA)

        # Deterministic OpenFDA enrichment
        for src in sources:
            if src.source_type != SourceType.OPENFDA or src.retrieval_status != RetrievalStatus.SUCCESS:
                continue
            results = (src.metadata or {}).get("results") or []
            if not results:
                try:
                    results = json.loads(src.raw_text or "{}").get("results", [])
                except Exception:
                    results = []
            if not results:
                continue
            openfda = results[0].get("openfda", {})
            mapping = {
                "brand_name": (openfda.get("brand_name") or [None])[0],
                "generic_name": (openfda.get("generic_name") or [None])[0],
                "manufacturer": (openfda.get("manufacturer_name") or [None])[0],
                "roa": (openfda.get("route") or [None])[0],
                "dosage_form": (openfda.get("dosage_form") or [None])[0],
                "pharmacologic_class": (openfda.get("pharm_class_epc") or [None])[0],
            }
            for field, value in mapping.items():
                if not value:
                    continue
                citation = {
                    "source_id": src.source_id,
                    "source_type": SourceType.OPENFDA.value,
                    "source_url": src.url,
                    "source_title": src.title,
                    "source_quote": f"openfda.{field}",
                    "retrieval_date": datetime.utcnow().isoformat(),
                    "confidence": 0.85,
                    "validation_status": ValidationStatus.NEEDS_REVIEW.value,
                    "interpreted": False,
                }
                self.db.add(
                    DrugProfileFieldORM(
                        id=new_id(),
                        job_id=job.id,
                        field=field,
                        value=str(value),
                        citation_json=citation,
                        validation_status=ValidationStatus.NEEDS_REVIEW.value,
                    )
                )
                if field == "generic_name" and not job.generic_name:
                    job.generic_name = str(value)
                if field == "manufacturer" and not job.manufacturer:
                    job.manufacturer = str(value)

        # LLM metadata from first successful narrative source
        for src in sources:
            doc = parsed.get(src.source_id)
            if not doc or doc.parsing_status.value != "success":
                continue
            if src.source_type == SourceType.OPENFDA:
                continue
            result = await self.llm.extract_metadata(
                product=job.drug_name,
                text=doc.full_text[:40000],
                source_meta={"url": src.url, "type": src.source_type.value, "title": src.title},
            )
            for field in result.get("fields", []):
                if not field.get("value"):
                    continue
                citation = {
                    "source_id": src.source_id,
                    "source_type": src.source_type.value,
                    "source_url": src.url,
                    "source_title": src.title,
                    "source_quote": field.get("source_quote") or "",
                    "retrieval_date": datetime.utcnow().isoformat(),
                    "confidence": float(field.get("confidence") or 0.5),
                    "validation_status": ValidationStatus.NEEDS_REVIEW.value,
                    "interpreted": bool(field.get("interpreted", True)),
                }
                self.db.add(
                    DrugProfileFieldORM(
                        id=new_id(),
                        job_id=job.id,
                        field=field["field"],
                        value=str(field["value"]),
                        citation_json=citation,
                        validation_status=ValidationStatus.NEEDS_REVIEW.value,
                    )
                )
            break
        self.db.commit()

    async def _extract_revenue(self, job: DrugJobORM, sources: list, parsed: dict, options: dict) -> list[DatapointORM]:
        if not options.get("quarterly_revenue", True):
            return []
        self._set_step(job, JobStep.EXTRACT_REVENUE)
        rows: list[DatapointORM] = []
        for src in sources:
            doc = parsed.get(src.source_id)
            if not doc or doc.parsing_status.value != "success":
                continue
            if src.source_type == SourceType.OPENFDA:
                continue
            result = await self.llm.extract_revenue(
                product=job.drug_name,
                company=job.manufacturer,
                source_meta={
                    "url": src.url,
                    "type": src.source_type.value,
                    "title": src.title,
                    "filing_type": src.filing_type,
                    "accession": src.accession_number,
                },
                text=doc.full_text,
            )
            for cand in result.get("candidates", []):
                quote = (cand.get("source_quote") or "").strip()
                url = src.url
                period_type = (cand.get("period_type") or "unknown").lower()
                # Never create quarterly datapoint from YTD/annual unless explicitly quarterly
                if period_type in {"ytd", "annual", "guidance", "cumulative"} and "Q" in str(cand.get("period", "")):
                    # keep but mark for review via period_type
                    pass
                dp_id = new_id()
                value = cand.get("value_reported")
                unit = cand.get("unit")
                currency = cand.get("currency") or "USD"
                normalized = cand.get("value_normalized_usd_millions")
                if normalized is None and value is not None:
                    if unit and "billion" in str(unit).lower():
                        normalized = float(value) * 1000
                    elif unit and "thousand" in str(unit).lower():
                        normalized = float(value) / 1000
                    else:
                        normalized = float(value)

                citation = {
                    "source_id": src.source_id,
                    "source_type": src.source_type.value,
                    "source_url": url,
                    "source_title": src.title,
                    "source_quote": quote,
                    "retrieval_date": datetime.utcnow().isoformat(),
                    "filing_type": src.filing_type,
                    "accession_number": src.accession_number,
                    "confidence": float(cand.get("confidence") or 0.5),
                    "validation_status": ValidationStatus.PENDING.value,
                    "interpreted": False,
                }
                row = DatapointORM(
                    id=dp_id,
                    job_id=job.id,
                    source_id=src.source_id,
                    period=str(cand.get("period") or "unknown"),
                    fiscal_year=cand.get("fiscal_year"),
                    fiscal_quarter=cand.get("fiscal_quarter"),
                    calendar_year=cand.get("calendar_year"),
                    calendar_quarter=cand.get("calendar_quarter"),
                    value_reported=value,
                    value_normalized_usd_millions=normalized,
                    currency=currency,
                    unit=unit,
                    period_type=period_type,
                    revenue_scope=cand.get("revenue_scope") or "Unknown",
                    geography=cand.get("geography"),
                    formulation=cand.get("formulation"),
                    route_of_administration=cand.get("route_of_administration"),
                    source_url=url,
                    source_quote=quote or "",
                    extraction_method="llm",
                    confidence_score=float(cand.get("confidence") or 0.5),
                    validation_status=ValidationStatus.PENDING.value,
                    citation_json=citation,
                )
                self.db.add(row)
                rows.append(row)
                src_row = self.db.get(SourceDocumentORM, src.source_id)
                if src_row:
                    src_row.relevant_datapoints_found = (src_row.relevant_datapoints_found or 0) + 1
        job.candidates_extracted = len(rows)
        self.db.commit()
        return rows

    async def _judge(self, job: DrugJobORM, rows: list[DatapointORM], sources: list, parsed: dict, options: dict) -> None:
        self._set_step(job, JobStep.EVIDENCE_JUDGE)
        if not options.get("llm_evidence_judge", True):
            return
        src_by_id = {s.source_id: s for s in sources}
        for row in rows:
            src = src_by_id.get(row.source_id or "")
            doc = parsed.get(row.source_id or "")
            context = doc.full_text[:8000] if doc else ""
            judgment = await self.llm.judge(
                candidate={
                    "period": row.period,
                    "value_reported": row.value_reported,
                    "period_type": row.period_type,
                    "revenue_scope": row.revenue_scope,
                },
                quote=row.source_quote,
                context=context,
            )
            row.source_support = judgment.get("support_classification")
            status = judgment.get("validation_status") or "needs_review"
            row.validation_status = status
            row.issue_flags = judgment.get("issues") or []
            if row.citation_json:
                row.citation_json = {**row.citation_json, "validation_status": status}
        self.db.commit()
        self._set_step(job, JobStep.RECONCILE_CONFLICTS)
        # Simple reconcile: prefer higher priority source type for same period+scope
        priority_index = {t.value: i for i, t in enumerate(SOURCE_PRIORITY)}
        by_key: dict[tuple, list[DatapointORM]] = {}
        for row in rows:
            key = (row.period, row.revenue_scope, row.formulation)
            by_key.setdefault(key, []).append(row)
        for group in by_key.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda r: priority_index.get((r.citation_json or {}).get("source_type", ""), 99))
            for loser in group[1:]:
                loser.validation_status = ValidationStatus.NEEDS_REVIEW.value
                loser.issue_flags = list(set((loser.issue_flags or []) + ["conflict_with_higher_priority_source"]))
        self.db.commit()

    async def _quality_and_validation(self, job: DrugJobORM) -> None:
        self._set_step(job, JobStep.QUALITY_CHECKS)
        dps = self.db.query(DatapointORM).filter_by(job_id=job.id).all()
        profile_fields = self.db.query(DrugProfileFieldORM).filter_by(job_id=job.id).all()
        profile = {f.field: f.value for f in profile_fields}
        dp_dicts = [
            {
                "id": d.id,
                "period": d.period,
                "value_reported": d.value_reported,
                "source_url": d.source_url,
                "source_quote": d.source_quote,
                "period_type": d.period_type,
                "revenue_scope": d.revenue_scope,
                "formulation": d.formulation,
                "geography": d.geography,
                "currency": d.currency,
                "unit": d.unit,
                "confidence_score": d.confidence_score,
                "validation_status": d.validation_status,
            }
            for d in dps
        ]
        issues = run_quality_checks(dp_dicts, profile)
        for issue in issues:
            self.db.add(
                QualityCheckORM(
                    id=new_id(),
                    job_id=job.id,
                    issue_type=issue.issue_type,
                    severity=issue.severity,
                    affected_datapoint=issue.affected_datapoint,
                    explanation=issue.explanation,
                    recommended_action=issue.recommended_action,
                )
            )
        for d in dps:
            related = [i for i in issues if i.affected_datapoint == d.id]
            d.validation_status = apply_auto_pass_gate(
                {
                    "id": d.id,
                    "source_url": d.source_url,
                    "source_quote": d.source_quote,
                    "period_type": d.period_type,
                    "revenue_scope": d.revenue_scope,
                    "confidence_score": d.confidence_score,
                    "validation_status": d.validation_status,
                },
                related,
            )
            if d.citation_json:
                d.citation_json = {**d.citation_json, "validation_status": d.validation_status}

        job.auto_pass_count = sum(1 for d in dps if d.validation_status == ValidationStatus.AUTO_PASS.value)
        job.needs_review_count = sum(1 for d in dps if d.validation_status == ValidationStatus.NEEDS_REVIEW.value)
        job.quality_flags = [i.issue_type for i in issues if i.severity == "high"]

        self._set_step(job, JobStep.VALIDATION_TASKS)
        conflict_ids = {d.id for d in dps if "conflict" in " ".join(d.issue_flags or [])}
        tasks = select_validation_tasks(
            [
                {
                    "id": d.id,
                    "period": d.period,
                    "confidence_score": d.confidence_score,
                    "validation_status": d.validation_status,
                }
                for d in dps
            ],
            conflict_ids=conflict_ids,
        )
        for t in tasks:
            self.db.add(
                ValidationTaskORM(
                    id=new_id(),
                    job_id=job.id,
                    datapoint_id=t["datapoint_id"],
                    reason=t["reason"],
                    confidence_score=t["confidence_score"],
                    status="open",
                )
            )
        self.db.commit()

    async def _completeness(self, job: DrugJobORM) -> None:
        self._set_step(job, JobStep.COMPLETENESS)
        dps = self.db.query(DatapointORM).filter_by(job_id=job.id).all()
        quarterly = [d for d in dps if d.period_type == PeriodType.QUARTERLY.value or ("Q" in d.period and d.period_type not in {"ytd", "annual"})]
        # Build expected quarters from min-max if any exist; missing -> unresolved
        periods = sorted({d.period for d in quarterly if d.period and d.period != "unknown"})
        existing = set(periods)
        # If gaps between known periods, mark unresolved (simple lexical for YYYYQn)
        def period_key(p: str) -> tuple[int, int]:
            try:
                y = int(p[:4])
                q = int(p[-1])
                return y, q
            except Exception:
                return 9999, 9

        if periods:
            start, end = period_key(periods[0]), period_key(periods[-1])
            y, q = start
            while (y, q) <= end:
                label = f"{y}Q{q}"
                if label not in existing:
                    self.db.add(
                        UnresolvedQuarterORM(
                            id=new_id(),
                            job_id=job.id,
                            period=label,
                            reason_unresolved="No reliable product-level quarterly value extracted",
                            sources_checked=[s.source_url for s in job.sources],
                            recommended_next_step="Check SEC 10-Q MD&A / earnings release for product net sales",
                            confidence_that_unavailable=0.3,
                        )
                    )
                q += 1
                if q > 4:
                    q = 1
                    y += 1

        unresolved = self.db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        job.unresolved_count = len(unresolved)
        result = await self.llm.completeness(
            profile={"drug_name": job.drug_name},
            datapoints=[{"period": d.period, "status": d.validation_status} for d in dps],
            unresolved=[{"period": u.period} for u in unresolved],
        )
        job.completeness_pct = float(result.get("completeness_pct") or 0)
        self.db.commit()
