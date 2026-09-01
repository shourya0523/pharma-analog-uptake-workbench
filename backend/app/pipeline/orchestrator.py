from __future__ import annotations

# ruff: noqa: BLE001, DTZ003
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.connectors.llm_search import LLMSearchConnector
from app.connectors.openfda import OpenFDAConnector
from app.connectors.openfda_fields import (
    earliest_approval_date,
    openfda_brand_names,
    select_openfda_result,
)
from app.connectors.sources import (
    ManualURLConnector,
    SECConnector,
    TranscriptConnectorStub,
    parse_filing_date,
)
from app.db.models import (
    AnalogFamilyORM,
    CanonicalProductORM,
    DatapointORM,
    DrugJobORM,
    DrugProfileFieldORM,
    EvidenceAssertionORM,
    ExtractionRunORM,
    MoAComponentORM,
    ProductFormulationORM,
    ProductIndicationORM,
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
from app.identity.resolver import resolve_product_identity
from app.llm.aliases import merge_aliases
from app.llm.client import LLMModules
from app.parsing.documents import DocumentParser
from app.parsing.evidence import (
    build_revenue_llm_text,
    prioritize_sources_for_revenue,
    select_product_evidence_text,
)
from app.parsing.fda_label import format_moa_profile_value, parse_label_record
from app.parsing.indications import parse_indications
from app.parsing.periods import detect_period_context, normalize_period
from app.extraction.candidates import extract_revenue_candidates
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.checks import (
    apply_auto_pass_gate,
    moa_epc_contamination_issue,
    quote_contains_value,
    run_quality_checks,
)
from app.quality.comparative import derive_comparative_candidates
from app.quality.completeness import resolve_completeness_pct
from app.quality.enrichment import (
    apply_field_enrichment,
    deterministic_formulation_fill,
    merge_enrichment_dicts,
)
from app.quality.fast_judge import try_deterministic_judgment
from app.quality.profile import (
    SIBLING_SENSITIVE_FIELDS,
    apply_profile_judgment,
    blends_sibling_brand,
    is_missing_value,
    select_profile_fields_for_judgment,
    values_conflict,
)
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
    SourceType.LLM_SEARCH,
    SourceType.USER_URL,
    SourceType.OTHER,
]


def persist_profile_field(
    db: Session,
    *,
    job_id: str,
    field: str,
    value: str,
    citation: dict[str, Any],
    method: str,
) -> DrugProfileFieldORM:
    """Persist an assertion without replacing reviewer-confirmed metadata."""

    confirmed = (
        db.query(DrugProfileFieldORM)
        .filter_by(job_id=job_id, field=field, validation_status=ValidationStatus.CONFIRMED.value)
        .first()
    )
    if confirmed:
        return confirmed
    row = DrugProfileFieldORM(
        id=new_id(),
        job_id=job_id,
        field=field,
        value=value,
        citation_json={**citation, "extraction_method": method},
        validation_status=ValidationStatus.NEEDS_REVIEW.value,
    )
    db.add(row)
    return row


class PipelineOrchestrator:
    def __init__(self, db: Session, file_store: FileStore | None = None, llm: LLMModules | None = None) -> None:
        self.db = db
        self.file_store = file_store or get_file_store()
        self.llm = llm or LLMModules()
        self.sec = SECConnector(self.file_store)
        self.fda = OpenFDAConnector(self.file_store)
        self.manual = ManualURLConnector(self.file_store)
        self.transcripts = TranscriptConnectorStub()
        self.search = LLMSearchConnector(self.file_store, self.llm)
        self.parser = DocumentParser(self.file_store)
        self._job_aliases: list[str] = []

    def _set_step(self, job: DrugJobORM, step: JobStep, status: JobStatus | None = None) -> None:
        job.current_step = step.value
        if status:
            job.status = status.value
        job.updated_at = datetime.utcnow()
        self.db.commit()
        logger.info(
            "job_step job_id=%s drug=%s step=%s status=%s",
            job.id,
            job.drug_name,
            job.current_step,
            job.status,
        )

    async def run_job(self, job_id: str) -> None:
        job = self.db.get(DrugJobORM, job_id)
        if not job:
            logger.warning("pipeline_skip missing_job job_id=%s", job_id)
            return
        try:
            job.status = JobStatus.RUNNING.value
            self.db.commit()
            logger.info("pipeline_start job_id=%s drug=%s run_id=%s", job.id, job.drug_name, job.run_id)

            run = self.db.get(ExtractionRunORM, job.run_id)
            options = (run.options_json if run else {}) or {}
            self._job_aliases = []
            await self._identity(job)
            sources = await self._retrieve(job, options)
            parsed = await self._parse(job, sources)
            await self._extract_metadata(job, sources, parsed, options)
            if options.get("product_metadata", True):
                await self._judge_profile(job)
            datapoint_rows = await self._extract_revenue(
                job, sources, parsed, options, skip_unresolved=get_settings().enable_llm_search
            )
            if not datapoint_rows and get_settings().enable_llm_search:
                extra_sources, extra_parsed = await self._search_revenue_fallback(job, options)
                if extra_sources:
                    sources = list(sources) + extra_sources
                    parsed = {**parsed, **extra_parsed}
                    datapoint_rows = await self._extract_revenue(
                        job, sources, parsed, options, only_source_ids={s.source_id for s in extra_sources}
                    )
            await self._judge(job, datapoint_rows, sources, parsed, options)
            await self._quality_and_validation(job)
            await self._completeness(job)
            self._set_step(job, JobStep.READY_FOR_REVIEW, JobStatus.READY_FOR_REVIEW)
            logger.info(
                "pipeline_done job_id=%s drug=%s sources=%s candidates=%s auto_pass=%s needs_review=%s unresolved=%s completeness=%s",
                job.id,
                job.drug_name,
                job.sources_found,
                job.candidates_extracted,
                job.auto_pass_count,
                job.needs_review_count,
                job.unresolved_count,
                job.completeness_pct,
            )
        except Exception as exc:
            step = job.current_step if job else None
            job.status = JobStatus.FAILED.value
            job.error = f"{type(exc).__name__}: {exc}"
            self.db.commit()
            logger.exception(
                "pipeline_failed job_id=%s drug=%s step=%s error=%s",
                job_id,
                getattr(job, "drug_name", None),
                step,
                job.error,
            )
            raise

    async def _expand_aliases(self, job: DrugJobORM) -> list[str]:
        settings = get_settings()
        base = merge_aliases(job.drug_name, job.generic_name)
        if not settings.enable_llm_search:
            self._job_aliases = base
            return base
        result = await self.llm.expand_aliases(
            product=job.drug_name,
            generic=job.generic_name,
            manufacturer=job.manufacturer,
            ticker=job.ticker,
        )
        merged = merge_aliases(
            job.drug_name,
            job.generic_name,
            llm_aliases=result.get("aliases"),
            formulations=result.get("formulations"),
            parent_companies=result.get("parent_companies"),
        )
        self._job_aliases = merged
        payload = {
            "aliases": result.get("aliases") or [],
            "formulations": result.get("formulations") or [],
            "parent_companies": result.get("parent_companies") or [],
            "search_terms": result.get("search_terms") or [],
            "merged": merged,
        }
        self.db.add(
            DrugProfileFieldORM(
                id=new_id(),
                job_id=job.id,
                field="llm_aliases",
                value=json.dumps(payload),
                citation_json={
                    "source_type": SourceType.LLM_SEARCH.value,
                    "source_quote": "llm_alias_expansion",
                    "retrieval_date": datetime.utcnow().isoformat(),
                    "confidence": 0.7,
                    "validation_status": ValidationStatus.NEEDS_REVIEW.value,
                    "interpreted": True,
                },
                validation_status=ValidationStatus.NEEDS_REVIEW.value,
            )
        )
        self.db.commit()
        return merged

    def _persist_sources(self, job: DrugJobORM, collected: list) -> None:
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

    async def _identity(self, job: DrugJobORM) -> None:
        self._set_step(job, JobStep.IDENTITY_RESOLVE)
        await self._expand_aliases(job)
        if not job.cik and (job.ticker or job.manufacturer):
            cik = await self.sec.resolve_cik(job.ticker, job.manufacturer)
            if cik:
                job.cik = cik
                self.db.commit()
                logger.info("cik_resolved job_id=%s drug=%s cik=%s via=sec", job.id, job.drug_name, cik)
        if not job.cik and get_settings().enable_llm_search:
            cik = await self.search.resolve_cik_from_search(
                product=job.drug_name,
                manufacturer=job.manufacturer,
                ticker=job.ticker,
                aliases=self._job_aliases,
            )
            if cik:
                job.cik = cik
                job.quality_flags = list(set((job.quality_flags or []) + ["cik_from_llm_search"]))
                self.db.commit()
                logger.info("cik_resolved job_id=%s drug=%s cik=%s via=llm_search", job.id, job.drug_name, cik)

    async def _retrieve(self, job: DrugJobORM, options: dict[str, Any]) -> list:
        self._set_step(job, JobStep.SOURCE_RETRIEVE)
        collected = []
        want_primary = bool(options.get("sec_filings", True))
        want_earnings = bool(options.get("earnings_releases", True))
        if want_primary or want_earnings:
            collected.extend(
                await self.sec.retrieve(
                    run_id=job.run_id,
                    job_id=job.id,
                    cik=job.cik,
                    ticker=job.ticker,
                    company_name=job.manufacturer,
                    include_primary=want_primary,
                    include_earnings=want_earnings,
                    earnings_since=parse_filing_date(options.get("earnings_since")),
                    earnings_until=parse_filing_date(options.get("earnings_until")),
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

        sec_ok = any(
            s.source_type in {SourceType.SEC_FILING, SourceType.EARNINGS_RELEASE}
            and s.retrieval_status == RetrievalStatus.SUCCESS
            for s in collected
        )
        if get_settings().enable_llm_search and not sec_ok:
            search_sources = await self.search.fallback_retrieve(
                run_id=job.run_id,
                job_id=job.id,
                goal="filing",
                product=job.drug_name,
                aliases=self._job_aliases,
                manufacturer=job.manufacturer,
                ticker=job.ticker,
                context="SEC/IR filings with product net sales when CIK retrieval failed or no SEC filings.",
            )
            collected.extend(search_sources)

        self._persist_sources(job, collected)
        job.sources_found = len(collected)
        self.db.commit()
        logger.info(
            "sources_retrieved job_id=%s drug=%s count=%s types=%s",
            job.id,
            job.drug_name,
            len(collected),
            sorted({getattr(s.source_type, "value", str(s.source_type)) for s in collected}),
        )
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

        written: dict[str, str] = {}
        conflicts: dict[str, dict[str, Any]] = {}

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
            selected, matched_brand = select_openfda_result(
                results,
                product=job.drug_name,
                generic=job.generic_name,
                aliases=self._job_aliases,
            )
            if selected is None:
                # Every result belongs to another product sharing the molecule
                job.quality_flags = list(
                    set((job.quality_flags or []) + ["openfda_no_brand_match"])
                )
                self.db.commit()
                logger.info(
                    "openfda_no_brand_match job_id=%s drug=%s brands=%s",
                    job.id,
                    job.drug_name,
                    [b for r in results for b in openfda_brand_names(r)][:8],
                )
                continue
            logger.info(
                "openfda_matched job_id=%s drug=%s brand=%s application=%s",
                job.id,
                job.drug_name,
                matched_brand,
                selected.get("application_number"),
            )
            openfda = selected.get("openfda", {})
            first_label = parse_label_record(selected)
            epc_terms = first_label.epc_terms
            moa_terms = first_label.moa_terms
            moa_value = format_moa_profile_value(moa_terms, first_label.moa_summary)
            if moa_epc_contamination_issue(moa_value, epc_terms):
                moa_value = None
            parsed_indications = (
                parse_indications(first_label.indications_text)
                if first_label.indications_text
                else []
            )
            indication_value = (
                "; ".join(dict.fromkeys(ind.disease for ind in parsed_indications if ind.disease))
                or None
            )
            mapping = {
                "brand_name": (openfda.get("brand_name") or [None])[0],
                "generic_name": (openfda.get("generic_name") or [None])[0],
                "manufacturer": (openfda.get("manufacturer_name") or [None])[0],
                "roa": "; ".join(first_label.routes) or None,
                "dosage_form": "; ".join(first_label.dosage_forms) or None,
                "pharmacologic_class": "; ".join(epc_terms) or None,
                "moa": moa_value,
                "active_ingredients": "; ".join(first_label.active_ingredients) or None,
                "indication": indication_value,
                "therapeutic_area": indication_value,
            }
            # Scope the approval date to this application; the earliest date across
            # all results belongs to whichever product was approved first.
            approval, approval_field = earliest_approval_date([selected])
            if approval:
                mapping["fda_approval_date"] = approval
            for field, value in mapping.items():
                if is_missing_value(value) or field in written:
                    continue
                written[field] = str(value)
                quote = (
                    approval_field
                    if field == "fda_approval_date" and approval_field
                    else f"openfda.{field}"
                )
                citation = {
                    "source_id": src.source_id,
                    "source_type": SourceType.OPENFDA.value,
                    "source_url": src.url,
                    "source_title": src.title,
                    "source_quote": quote,
                    "retrieval_date": datetime.utcnow().isoformat(),
                    "confidence": 0.9 if field == "fda_approval_date" else 0.85,
                    "validation_status": ValidationStatus.NEEDS_REVIEW.value,
                    "interpreted": False,
                    "openfda_application_number": selected.get("application_number"),
                    "openfda_matched_brand": matched_brand,
                }
                persist_profile_field(
                    self.db,
                    job_id=job.id,
                    field=field,
                    value=str(value),
                    citation=citation,
                    method="structured_fda",
                )
                if field == "generic_name" and not job.generic_name:
                    job.generic_name = str(value)
                if field == "manufacturer" and not job.manufacturer:
                    job.manufacturer = str(value)

            if first_label.brand_names:
                identity = resolve_product_identity(
                    brand_name=first_label.brand_names[0],
                    active_ingredients=first_label.active_ingredients,
                    dosage_form=first_label.dosage_forms[0] if first_label.dosage_forms else None,
                    route_terms=first_label.routes,
                )
                product = (
                    self.db.query(CanonicalProductORM)
                    .filter_by(identity_key=identity.identity_key)
                    .first()
                )
                if not product:
                    product = CanonicalProductORM(
                        id=new_id(),
                        canonical_name=identity.canonical_name,
                        identity_key=identity.identity_key,
                        active_moieties_json=identity.active_ingredients,
                        current_commercial_owner=job.manufacturer,
                        regulatory_sponsor=(openfda.get("manufacturer_name") or [None])[0],
                        application_number=(
                            first_label.application_numbers[0]
                            if first_label.application_numbers
                            else None
                        ),
                    )
                    self.db.add(product)
                    self.db.flush()
                family = (
                    self.db.query(AnalogFamilyORM)
                    .filter_by(active_moiety_key=identity.analog_family_key)
                    .first()
                )
                if not family:
                    family = AnalogFamilyORM(
                        id=new_id(),
                        name=identity.analog_family_key.title(),
                        active_moiety_key=identity.analog_family_key,
                    )
                    self.db.add(family)
                    self.db.flush()
                if not self.db.query(ProductFormulationORM).filter_by(product_id=product.id).first():
                    self.db.add(
                        ProductFormulationORM(
                            id=new_id(),
                            product_id=product.id,
                            analog_family_id=family.id,
                            dosage_form=identity.dosage_form or "unresolved",
                            route_source_term="; ".join(first_label.routes) or None,
                            route_category="; ".join(first_label.routes).lower() or None,
                        )
                    )
                for moa_term in moa_terms:
                    existing_moa = (
                        self.db.query(MoAComponentORM)
                        .filter_by(product_id=product.id, moa_term=moa_term)
                        .first()
                    )
                    if not existing_moa:
                        self.db.add(
                            MoAComponentORM(
                                id=new_id(),
                                product_id=product.id,
                                active_ingredient=None,
                                moa_term=moa_term,
                                descriptive_text=first_label.moa_summary,
                                fda_epc_terms_json=epc_terms,
                            )
                        )
                for indication in parsed_indications:
                    existing_indication = (
                        self.db.query(ProductIndicationORM)
                        .filter_by(product_id=product.id, disease=indication.disease)
                        .first()
                    )
                    if existing_indication:
                        continue
                    self.db.add(
                        ProductIndicationORM(
                            id=new_id(),
                            product_id=product.id,
                            disease=indication.disease,
                            setting=indication.setting,
                            population=indication.population,
                            biomarker=indication.biomarker,
                            approval_date=(
                                datetime.fromisoformat(approval).date()
                                if approval
                                else None
                            ),
                            launch_anchor_type=(
                                "indication_approval_date" if approval else None
                            ),
                            approved_lot=indication.approved_lot.value.value,
                            approved_lot_quote=indication.source_quote,
                        )
                    )
                for field, value in mapping.items():
                    if value:
                        self.db.add(
                            EvidenceAssertionORM(
                                id=new_id(),
                                entity_type="product",
                                entity_id=product.id,
                                field_name=field,
                                value_json={"value": value},
                                source_id=src.source_id,
                                source_url=src.url,
                                source_section=f"openfda.{field}",
                                source_quote=f"openfda.{field}",
                                confidence=0.9,
                                validation_status=ValidationStatus.NEEDS_REVIEW.value,
                                extraction_method="structured_fda",
                                selected=True,
                            )
                        )

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
                name = field.get("field")
                if not name or is_missing_value(field.get("value")):
                    continue
                if name == "moa":
                    epc_values = [
                        row.value
                        for row in self.db.query(DrugProfileFieldORM)
                        .filter_by(job_id=job.id, field="pharmacologic_class")
                        .all()
                        if row.value
                    ]
                    if moa_epc_contamination_issue(str(field["value"]), epc_values):
                        continue
                if name in SIBLING_SENSITIVE_FIELDS and blends_sibling_brand(
                    field.get("value"),
                    product=job.drug_name,
                    aliases=self._job_aliases,
                    source_quote=field.get("source_quote"),
                ):
                    job.quality_flags = list(
                        set((job.quality_flags or []) + [f"sibling_blend_skipped:{name}"])
                    )
                    continue
                if name in written:
                    # Record the disagreement for the judge instead of silently
                    # preferring one source; identical answers are just deduped.
                    if values_conflict(written[name], field["value"]):
                        conflicts[name] = {
                            "value": str(field["value"]),
                            "source_type": src.source_type.value,
                            "source_url": src.url,
                            "source_quote": field.get("source_quote") or "",
                        }
                    continue
                written[name] = str(field["value"])
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
                persist_profile_field(
                    self.db,
                    job_id=job.id,
                    field=name,
                    value=str(field["value"]),
                    citation=citation,
                    method="bounded_llm",
                )
            break

        if conflicts:
            for name, rival in conflicts.items():
                row = (
                    self.db.query(DrugProfileFieldORM)
                    .filter_by(job_id=job.id, field=name)
                    .first()
                )
                if row and row.citation_json:
                    row.citation_json = {**row.citation_json, "conflicting_source": rival}
        self.db.commit()
        logger.info(
            "metadata_extracted job_id=%s drug=%s fields=%s conflicts=%s",
            job.id,
            job.drug_name,
            sorted(written),
            sorted(conflicts),
        )

    async def _judge_profile(self, job: DrugJobORM) -> None:
        """Challenge every profile field with independent search and correct them.

        Source registries carry errors, so a cited value is not assumed correct.
        Conflicts and high-cost regulatory fields are judged first; remaining
        content fields follow. Internal payloads like llm_aliases are skipped.
        """
        settings = get_settings()
        if not settings.enable_profile_judge:
            return
        if not settings.openrouter_api_key or not settings.enable_llm_search:
            return
        rows = self.db.query(DrugProfileFieldORM).filter_by(job_id=job.id).all()
        if not rows:
            return
        self._set_step(job, JobStep.JUDGE_METADATA)

        to_judge = select_profile_fields_for_judgment(
            rows, max_fields=settings.profile_judge_max_fields
        )

        corrected = 0
        for row in to_judge:
            citation = row.citation_json or {}
            judgment = await self.llm.judge_profile_field(
                product=job.drug_name,
                generic=job.generic_name,
                aliases=self._job_aliases,
                field=row.field,
                value=row.value or "",
                source={
                    "source_type": citation.get("source_type"),
                    "source_url": citation.get("source_url"),
                    "source_quote": citation.get("source_quote"),
                    "openfda_application_number": citation.get("openfda_application_number"),
                    "conflicting_source": citation.get("conflicting_source"),
                },
            )
            outcome = apply_profile_judgment(
                row.field,
                row.value or "",
                judgment,
                min_confidence=settings.profile_judge_min_confidence,
            )
            row.citation_json = {
                **citation,
                "judge_verdict": outcome.verdict,
                "judge_flags": outcome.flags,
                **({"correction": outcome.correction} if outcome.correction else {}),
            }
            if outcome.corrected:
                row.value = outcome.value
                corrected += 1
                # A search-sourced correction always goes to a human
                row.validation_status = ValidationStatus.NEEDS_REVIEW.value
                job.quality_flags = list(
                    set((job.quality_flags or []) + [f"profile_corrected:{row.field}"])
                )
            logger.info(
                "profile_field_judged job_id=%s field=%s verdict=%s corrected=%s flags=%s",
                job.id,
                row.field,
                outcome.verdict,
                outcome.corrected,
                outcome.flags,
            )
        self.db.commit()
        logger.info(
            "profile_judged job_id=%s drug=%s judged=%s corrected=%s",
            job.id,
            job.drug_name,
            len(to_judge),
            corrected,
        )

    async def _search_revenue_fallback(
        self, job: DrugJobORM, options: dict[str, Any]
    ) -> tuple[list, dict[str, Any]]:
        search_sources = await self.search.fallback_retrieve(
            run_id=job.run_id,
            job_id=job.id,
            goal="revenue",
            product=job.drug_name,
            aliases=self._job_aliases,
            manufacturer=job.manufacturer,
            ticker=job.ticker,
            context="Product-level quarterly or annual net sales from earnings release or IR.",
        )
        if not search_sources:
            return [], {}
        self._persist_sources(job, search_sources)
        job.sources_found = (job.sources_found or 0) + len(search_sources)
        job.quality_flags = list(set((job.quality_flags or []) + ["llm_search_revenue_fallback"]))
        self.db.commit()
        parsed = await self._parse(job, search_sources)
        return search_sources, parsed

    async def _extract_revenue(
        self,
        job: DrugJobORM,
        sources: list,
        parsed: dict,
        options: dict,
        *,
        only_source_ids: set[str] | None = None,
        skip_unresolved: bool = False,
    ) -> list[DatapointORM]:
        if not options.get("quarterly_revenue", True):
            return []
        self._set_step(job, JobStep.EXTRACT_REVENUE)
        rows: list[DatapointORM] = []
        dropped_total = 0
        any_product_money = False
        # Reading tables costs nothing, so every parsed source is read; only the
        # LLM pass is capped.
        selected_sources = prioritize_sources_for_revenue(
            sources, parsed, max_sources=max(len(list(sources)), 1)
        )
        llm_source_ids = {
            s.source_id
            for s in prioritize_sources_for_revenue(
                sources, parsed, max_sources=get_settings().llm_max_extract_sources
            )
        }
        if only_source_ids:
            selected_sources = [s for s in selected_sources if s.source_id in only_source_ids]
        extra = self._job_aliases or None

        for src in selected_sources:
            doc = parsed.get(src.source_id)
            if not doc or doc.parsing_status.value != "success":
                continue
            if src.source_type == SourceType.OPENFDA:
                continue

            llm_text, evidence_meta = build_revenue_llm_text(
                doc,
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
            )
            period_context = detect_period_context(doc.full_text)
            if evidence_meta.get("had_product_money_hits"):
                any_product_money = True

            # Skip the LLM when a filing has no product+$ evidence (avoid XBRL /
            # company-total noise) or when it is beyond the extraction budget.
            no_product_evidence = evidence_meta.get("strategy") in {
                "no_product_mention",
                "empty",
            } or not evidence_meta.get("had_product_money_hits")
            use_llm = src.source_id in llm_source_ids and not no_product_evidence
            if not use_llm:
                src_row = self.db.get(SourceDocumentORM, src.source_id)
                if src_row:
                    reason = "no_product_evidence" if no_product_evidence else "over_source_budget"
                    note = (
                        f"skip_revenue_llm reason={reason} "
                        f"strategy={evidence_meta.get('strategy')} "
                        f"product_money={evidence_meta.get('had_product_money_hits')}"
                    )
                    src_row.notes = f"{(src_row.notes or '').rstrip()} | {note}".strip(" |")

            result: dict[str, Any] = {"candidates": [], "spans": []}
            if use_llm:
                result = await self.llm.extract_revenue(
                    product=job.drug_name,
                    company=job.manufacturer,
                    source_meta={
                        "url": src.url,
                        "type": src.source_type.value,
                        "title": src.title,
                        "filing_type": src.filing_type,
                        "accession": src.accession_number,
                        "evidence": evidence_meta,
                        "reporting_period": period_context.describe() if period_context else None,
                        "period_columns": (
                            [str(period_context.year), str(period_context.comparative_year)]
                            if period_context
                            else None
                        ),
                    },
                    text=llm_text,
                )
            span_corpus = "\n\n".join(
                (s.get("span_text") or "") for s in (result.get("spans") or [])
            ) or llm_text
            llm_dropped = result.get("dropped") or []
            kept, dropped = filter_revenue_candidates(
                result.get("candidates") or [],
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
                source_text=span_corpus,
            )
            dropped = list(llm_dropped) + list(dropped)
            dropped_total += len(dropped)

            # Read the revenue table directly; the model omits rows unpredictably.
            # These quotes come from the parsed table, not the model, so the
            # verbatim gate that guards model output does not apply.
            #
            # The table is fingerprinted first, so its numbers are scaled by the
            # unit it declares and its year-to-date columns stay labelled as
            # such. Assuming USD millions and a fixed quarter layout is what
            # produced 1000x-wrong values and full-year totals filed as
            # quarters in the dataset this pipeline is scored against.
            fingerprinted, table_findings, table_skips = extract_revenue_candidates(
                doc.tables,
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
                context=doc.full_text[:4000],
            )
            for finding in table_findings:
                logger.warning(
                    "table_check job_id=%s source_id=%s %s",
                    job.id,
                    src.source_id,
                    finding,
                )
            if table_skips:
                logger.info(
                    "table_skipped job_id=%s source_id=%s reasons=%s",
                    job.id,
                    src.source_id,
                    table_skips,
                )
            table_rows, table_dropped = filter_revenue_candidates(
                fingerprinted,
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
            )
            dropped_total += len(table_dropped)
            if table_rows:
                seen_rows = {
                    (str(c.get("period")), round(float(c["value_reported"]), 3))
                    for c in kept
                    if c.get("value_reported") is not None
                }
                added = [
                    row
                    for row in table_rows
                    if (str(row["period"]), round(float(row["value_reported"]), 3)) not in seen_rows
                ]
                kept = list(kept) + added
                logger.info(
                    "table_rows_extracted job_id=%s source_id=%s parsed=%s added=%s",
                    job.id,
                    src.source_id,
                    len(table_rows),
                    len(added),
                )

            comparatives = derive_comparative_candidates(kept, context=period_context)
            if comparatives:
                kept = list(kept) + comparatives
                logger.info(
                    "comparative_columns_derived job_id=%s source_id=%s count=%s",
                    job.id,
                    src.source_id,
                    len(comparatives),
                )
            src_row = self.db.get(SourceDocumentORM, src.source_id)
            if src_row and dropped:
                reason_counts: dict[str, int] = {}
                for d in dropped:
                    reason_counts[d.get("_drop_reason", "unknown")] = reason_counts.get(d.get("_drop_reason", "unknown"), 0) + 1
                note = f"filtered_candidates={reason_counts}"
                src_row.notes = f"{(src_row.notes or '').rstrip()} | {note}".strip(" |")
            if src_row and result.get("note"):
                src_row.notes = f"{(src_row.notes or '').rstrip()} | {result.get('note')}".strip(" |")

            for cand in kept:
                quote = (cand.get("source_quote") or "").strip()
                url = src.url
                period_type = (cand.get("period_type") or "unknown").lower()
                raw_period = str(cand.get("period") or "unknown")
                period = normalize_period(
                    raw_period, period_type=period_type, context=period_context
                )
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
                    "period_reported": raw_period,
                }
                if src.source_type == SourceType.LLM_SEARCH:
                    citation["search_query"] = (src.metadata or {}).get("search_query")
                    citation["search_snippet"] = (src.metadata or {}).get("search_snippet")
                issue_flags: list[str] = []
                if cand.get("_reclassified"):
                    issue_flags.append("reclassified_company_total")
                if cand.get("_derived_comparative"):
                    issue_flags.append("derived_comparative_column")
                if cand.get("_from_table"):
                    issue_flags.append("extracted_from_table")
                if period is None:
                    issue_flags.append("period_unparsed")
                elif period != raw_period:
                    issue_flags.append("period_normalized")
                row = DatapointORM(
                    id=dp_id,
                    job_id=job.id,
                    source_id=src.source_id,
                    period=period or "unknown",
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
                    extraction_method="table" if cand.get("_from_table") else "llm",
                    confidence_score=float(cand.get("confidence") or 0.5),
                    validation_status=ValidationStatus.PENDING.value,
                    citation_json=citation,
                    issue_flags=issue_flags or None,
                )
                self.db.add(row)
                rows.append(row)
                if src_row:
                    src_row.relevant_datapoints_found = (src_row.relevant_datapoints_found or 0) + 1

        if not rows and not skip_unresolved:
            reason = (
                "Product-specific revenue not disclosed (or not found) in retrieved SEC/IR sources"
                if not any_product_money
                else "Candidates extracted but all failed product/quote integrity filters"
            )
            self.db.add(
                UnresolvedQuarterORM(
                    id=new_id(),
                    job_id=job.id,
                    period="product_revenue",
                    reason_unresolved=reason,
                    sources_checked=[s.url for s in selected_sources],
                    recommended_next_step="Provide IR/earnings URL with product-level sales, or confirm non-disclosure",
                    confidence_that_unavailable=0.7 if not any_product_money else 0.4,
                )
            )
            job.quality_flags = list(
                set((job.quality_flags or []) + ["no_product_revenue_candidates", f"dropped_candidates:{dropped_total}"])
            )

        job.candidates_extracted = len(rows)
        self.db.commit()
        logger.info(
            "extract_revenue_done job_id=%s drug=%s kept=%s dropped=%s product_money=%s sources=%s",
            job.id,
            job.drug_name,
            len(rows),
            dropped_total,
            any_product_money,
            len(selected_sources),
        )
        return rows

    async def _judge(self, job: DrugJobORM, rows: list[DatapointORM], sources: list, parsed: dict, options: dict) -> None:
        self._set_step(job, JobStep.EVIDENCE_JUDGE)
        if not options.get("llm_evidence_judge", True):
            return
        settings = get_settings()
        aliases = self._job_aliases or merge_aliases(job.drug_name, job.generic_name)
        for row in rows:
            candidate = {
                "period": row.period,
                "value_reported": row.value_reported,
                "period_type": row.period_type,
                "revenue_scope": row.revenue_scope,
                "formulation": row.formulation,
            }
            context = row.source_quote or ""
            judgment = None
            if settings.llm_skip_judge_when_deterministic:
                judgment = try_deterministic_judgment(
                    product=job.drug_name,
                    generic=job.generic_name,
                    candidate=candidate,
                    quote=row.source_quote or "",
                    extra_aliases=aliases,
                )
            if judgment is None:
                doc = parsed.get(row.source_id or "")
                if doc and doc.full_text:
                    context, _meta = select_product_evidence_text(
                        doc.full_text,
                        product=job.drug_name,
                        generic=job.generic_name,
                        extra_aliases=aliases,
                        max_chars=6000,
                        window=500,
                        max_windows=6,
                    )
                    if row.source_quote and row.source_quote not in context:
                        context = f"{row.source_quote}\n\n{context}"[:6000]
                judgment = await self.llm.judge(
                    product=job.drug_name,
                    candidate=candidate,
                    quote=row.source_quote,
                    context=context,
                    generic=job.generic_name,
                    extra_aliases=aliases,
                )

            support = judgment.get("support_classification")
            status = judgment.get("validation_status") or "needs_review"
            enrichment: dict[str, Any] = {}
            if (
                settings.enable_llm_search
                and support in {"partial", "unsupported", "inconclusive", "misclassified"}
            ):
                search_judgment = await self.llm.judge_with_search(
                    product=job.drug_name,
                    aliases=aliases,
                    candidate=candidate,
                    quote=row.source_quote or "",
                    context=context,
                )
                if search_judgment:
                    judgment = {**judgment, **{k: v for k, v in search_judgment.items() if v is not None}}
                    support = judgment.get("support_classification")
                    status = judgment.get("validation_status") or status
                    enrichment = merge_enrichment_dicts(search_judgment.get("enrichment") or {})
                    row.issue_flags = list(set((row.issue_flags or []) + ["llm_search_validated"]))
                    if search_judgment.get("search_corroborated"):
                        row.issue_flags = list(set((row.issue_flags or []) + ["search_corroborated"]))
                    if (row.citation_json or {}).get("source_type") == SourceType.LLM_SEARCH.value:
                        status = ValidationStatus.NEEDS_REVIEW.value

            # Always try deterministic aggregate fill + LLM enrichment on blank fields.
            enrichment = merge_enrichment_dicts(
                deterministic_formulation_fill(
                    {
                        "revenue_scope": row.revenue_scope,
                        "formulation": row.formulation,
                    }
                ),
                enrichment,
                judgment.get("enrichment") if isinstance(judgment.get("enrichment"), dict) else None,
            )
            if enrichment:
                snapshot = {
                    "period": row.period,
                    "period_type": row.period_type,
                    "revenue_scope": row.revenue_scope,
                    "value_reported": row.value_reported,
                    "value_normalized_usd_millions": row.value_normalized_usd_millions,
                    "currency": row.currency,
                    "unit": row.unit,
                    "geography": row.geography,
                    "formulation": row.formulation,
                    "route_of_administration": row.route_of_administration,
                    "fiscal_year": row.fiscal_year,
                    "fiscal_quarter": row.fiscal_quarter,
                    "calendar_year": row.calendar_year,
                    "calendar_quarter": row.calendar_quarter,
                    "confidence_score": row.confidence_score,
                    "validation_status": status,
                    "issue_flags": list(row.issue_flags or []),
                    "citation_json": dict(row.citation_json or {}),
                }
                enriched, applied = apply_field_enrichment(snapshot, enrichment)
                if applied:
                    row.period = enriched.get("period") or row.period
                    row.period_type = enriched.get("period_type") or row.period_type
                    row.revenue_scope = enriched.get("revenue_scope") or row.revenue_scope
                    if "value_reported" in applied:
                        row.value_reported = enriched.get("value_reported")
                    if "value_normalized_usd_millions" in applied:
                        row.value_normalized_usd_millions = enriched.get("value_normalized_usd_millions")
                    if "currency" in applied:
                        row.currency = enriched.get("currency")
                    if "unit" in applied:
                        row.unit = enriched.get("unit")
                    if "geography" in applied:
                        row.geography = enriched.get("geography")
                    if "formulation" in applied:
                        row.formulation = enriched.get("formulation")
                    if "route_of_administration" in applied:
                        row.route_of_administration = enriched.get("route_of_administration")
                    if "fiscal_year" in applied:
                        row.fiscal_year = enriched.get("fiscal_year")
                    if "fiscal_quarter" in applied:
                        row.fiscal_quarter = enriched.get("fiscal_quarter")
                    if "calendar_year" in applied:
                        row.calendar_year = enriched.get("calendar_year")
                    if "calendar_quarter" in applied:
                        row.calendar_quarter = enriched.get("calendar_quarter")
                    row.confidence_score = float(enriched.get("confidence_score") or row.confidence_score or 0)
                    row.issue_flags = list(enriched.get("issue_flags") or row.issue_flags or [])
                    row.citation_json = enriched.get("citation_json") or row.citation_json
                    status = ValidationStatus.NEEDS_REVIEW.value
                    judgment.setdefault("issues", []).append(
                        f"field_enrichment:{','.join(applied)}"
                    )

            row.source_support = support
            if quote_contains_value(
                row.source_quote or "", row.value_reported
            ) and "field_enrichment_applied" not in (row.issue_flags or []):
                # Don't inflate confidence above enrichment cap when fields were estimated.
                row.confidence_score = max(float(row.confidence_score or 0), 0.85)
            if support == "misclassified":
                status = ValidationStatus.NEEDS_REVIEW.value
                if row.revenue_scope != "Company total" and "total revenue" in (row.source_quote or "").lower():
                    row.revenue_scope = "Company total"
            elif support == "unsupported":
                status = ValidationStatus.NEEDS_REVIEW.value
            elif (
                support == "supported"
                and row.period_type
                in {
                    PeriodType.QUARTERLY.value,
                    PeriodType.ANNUAL.value,
                }
                and "field_enrichment_applied" not in (row.issue_flags or [])
            ):
                status = ValidationStatus.AUTO_PASS.value
            elif support == "partial":
                status = ValidationStatus.NEEDS_REVIEW.value
            if "derived_comparative_column" in (row.issue_flags or []):
                # Reconstructed from a neighbouring table column, so always reviewed
                status = ValidationStatus.NEEDS_REVIEW.value
            row.validation_status = status
            issues = list(judgment.get("issues") or [])
            if row.issue_flags:
                issues = list(set(list(row.issue_flags) + issues))
            row.issue_flags = issues
            if row.citation_json:
                row.citation_json = {**row.citation_json, "validation_status": status}
        self.db.commit()
        await self._reconcile_with_llm(job, rows)

    async def _reconcile_with_llm(self, job: DrugJobORM, rows: list[DatapointORM]) -> None:
        self._set_step(job, JobStep.RECONCILE_CONFLICTS)
        if len(rows) < 2:
            self.db.commit()
            return

        # Deterministic grouping first
        priority_index = {t.value: i for i, t in enumerate(SOURCE_PRIORITY)}
        by_key: dict[tuple, list[DatapointORM]] = {}
        for row in rows:
            key = (row.period, row.revenue_scope or "", row.formulation or "")
            by_key.setdefault(key, []).append(row)

        conflict_payload: list[dict[str, Any]] = []
        for group in by_key.values():
            if len(group) < 2:
                continue
            for r in group:
                conflict_payload.append(
                    {
                        "id": r.id,
                        "period": r.period,
                        "period_type": r.period_type,
                        "value_reported": r.value_reported,
                        "revenue_scope": r.revenue_scope,
                        "formulation": r.formulation,
                        "confidence_score": r.confidence_score,
                        "source_quote": (r.source_quote or "")[:240],
                        "source_type": (r.citation_json or {}).get("source_type"),
                        "validation_status": r.validation_status,
                    }
                )

        winners: set[str] = set()
        losers: set[str] = set()
        if conflict_payload:
            result = await self.llm.reconcile(product=job.drug_name, candidates=conflict_payload)
            for item in result.get("resolved") or []:
                wid = item.get("winner_id")
                if wid:
                    winners.add(wid)
            for item in result.get("conflicts") or []:
                ids = item.get("candidate_ids") or []
                wid = item.get("winner_id")
                if wid:
                    winners.add(wid)
                for cid in ids:
                    if cid != wid:
                        losers.add(cid)

        # Fallback: source-priority within groups when LLM left them unmarked
        for group in by_key.values():
            if len(group) < 2:
                continue
            group_winners = [r for r in group if r.id in winners]
            if group_winners:
                for r in group:
                    if r.id not in winners:
                        losers.add(r.id)
                continue
            if any(r.id in losers for r in group):
                continue
            group.sort(key=lambda r: priority_index.get((r.citation_json or {}).get("source_type", ""), 99))
            winners.add(group[0].id)
            for loser in group[1:]:
                losers.add(loser.id)

        for row in rows:
            if row.id in losers:
                row.validation_status = ValidationStatus.NEEDS_REVIEW.value
                row.issue_flags = list(set((row.issue_flags or []) + ["conflict_with_higher_priority_source"]))
                if row.citation_json:
                    row.citation_json = {**row.citation_json, "validation_status": row.validation_status}
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
        # Merge, so provenance flags raised earlier in the pipeline survive
        job.quality_flags = sorted(
            set(job.quality_flags or []) | {i.issue_type for i in issues if i.severity == "high"}
        )

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
        profile_fields = self.db.query(DrugProfileFieldORM).filter_by(job_id=job.id).all()
        profile = {f.field: f.value for f in profile_fields}
        quarterly = [
            d
            for d in dps
            if d.period_type == PeriodType.QUARTERLY.value
            or ("Q" in (d.period or "") and d.period_type not in {"ytd", "annual", "six_month", "nine_month"})
        ]
        periods = sorted({d.period for d in quarterly if d.period and d.period != "unknown"})
        existing = set(periods)

        def period_key(p: str) -> tuple[int, int]:
            try:
                y = int(p[:4])
                q = int(p[-1])
                return y, q
            except Exception:
                return 9999, 9

        # Deterministic gap fill between min/max quarterly periods
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
        existing_unresolved = {u.period for u in unresolved}
        result = await self.llm.completeness(
            product=job.drug_name,
            profile={"drug_name": job.drug_name, **profile},
            datapoints=[
                {
                    "period": d.period,
                    "period_type": d.period_type,
                    "status": d.validation_status,
                    "value_reported": d.value_reported,
                    "scope": d.revenue_scope,
                }
                for d in dps
            ],
            unresolved=[{"period": u.period, "reason": u.reason_unresolved} for u in unresolved],
            timeline={
                "fda_approval_date": profile.get("fda_approval_date"),
                "known_quarters": periods,
            },
        )

        reason_map = {
            "not_disclosed": ("Product revenue not disclosed for this quarter in retrieved sources", 0.65),
            "need_filing": ("Likely disclosed in a filing not yet retrieved", 0.35),
            "gap": ("Missing quarter — analyst follow-up required", 0.4),
        }
        for miss in result.get("missing_periods") or []:
            if isinstance(miss, str):
                period, code, reason, nxt = miss, "gap", "Missing period", "Review SEC filings"
            else:
                period = miss.get("period")
                code = (miss.get("reason_code") or "gap").lower()
                default_reason, conf = reason_map.get(code, reason_map["gap"])
                reason = miss.get("reason") or default_reason
                nxt = miss.get("recommended_next_step") or "Review SEC 10-Q / earnings for product net sales"
            if not period or period in existing or period in existing_unresolved:
                continue
            if "Q" not in str(period):
                continue
            conf = reason_map.get(code, reason_map["gap"])[1]
            self.db.add(
                UnresolvedQuarterORM(
                    id=new_id(),
                    job_id=job.id,
                    period=str(period),
                    reason_unresolved=f"[{code}] {reason}",
                    sources_checked=[s.source_url for s in job.sources],
                    recommended_next_step=nxt,
                    confidence_that_unavailable=conf,
                )
            )
            existing_unresolved.add(str(period))

        unresolved = self.db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        job.unresolved_count = len(unresolved)
        job.completeness_pct = resolve_completeness_pct(
            result.get("completeness_pct"),
            quarterly_count=len([d for d in dps if d.period_type == PeriodType.QUARTERLY.value]),
            unresolved_quarter_count=len([x for x in unresolved if "Q" in (x.period or "")]),
        )
        self.db.commit()
        logger.info(
            "completeness job_id=%s drug=%s llm_pct=%s resolved_pct=%s quarterly=%s unresolved=%s",
            job.id,
            job.drug_name,
            result.get("completeness_pct"),
            job.completeness_pct,
            len([d for d in dps if d.period_type == PeriodType.QUARTERLY.value]),
            job.unresolved_count,
        )
