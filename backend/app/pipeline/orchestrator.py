from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

# ruff: noqa: BLE001, DTZ003
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
    RetrievedSource,
    SourceType,
    ValidationStatus,
    new_id,
)
from app.extraction import elements, member_store
from app.extraction.bulk_tagged import candidates_from_notes
from app.extraction.candidates import extract_revenue_candidates
from app.extraction.check import _ROUNDING_ABSOLUTE as ROUNDING_ABSOLUTE
from app.extraction.check import _ROUNDING_TOLERANCE as ROUNDING_TOLERANCE
from app.extraction.derive import complete_series
from app.extraction.elements import Verdict
from app.extraction.fingerprint import UNIT_SCALE_TO_MILLIONS
from app.extraction.members import Resolution, load_products, resolve
from app.extraction.tagged import candidates_from_instance
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
from app.parsing.xbrl import parse_calculation, parse_facts, unsettled_elements
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


# How strong a claim each producer makes, strongest first. This is a different
# axis from SOURCE_PRIORITY, which ranks the document a figure came from: a
# product-sales schedule and a sentence of narrative can sit in the same 8-K
# exhibit, so the source type does not separate them and the schedule is
# plainly the better claim. Ordered by how much has to be inferred - a tagged
# fact states its own period, unit and product; a schedule declares its unit
# and its columns; a derivation is exact arithmetic over figures the issuer
# published; a sentence and a model's reading are recovered from running text.
# Two spellings reach this, and both are listed rather than normalised in one
# of them: a candidate carries the reader's own label ("table_fingerprint",
# "prose_sentence") and a stored datapoint carries the shorter one the export
# uses. One ranking, both vocabularies.
CLAIM_STRENGTH = {
    "xbrl_fact": 0,
    "table": 1,
    "table_fingerprint": 1,
    "derived_from_period_total": 2,
    "derived_sole_formulation": 2,
    "llm": 3,
    "prose": 4,
    "prose_sentence": 4,
}


def claim_rank(extraction_method: str | None) -> int:
    """Where a producer sits in CLAIM_STRENGTH; unknown producers rank last."""
    return CLAIM_STRENGTH.get(str(extraction_method or ""), len(CLAIM_STRENGTH))


# Which document to read first for a product's quarterly sales, best first.
#
# This is not SOURCE_PRIORITY and it is not a second copy of it: they answer
# different questions and give opposite answers, both correctly. SOURCE_PRIORITY
# ranks *authority* - which figure wins when two disagree - and puts the audited
# 10-K above an 8-K press exhibit. This ranks *fitness for the question*: an 8-K
# item 2.02 exhibit is a product-sales schedule and nothing else, while a 10-K
# names a product across dozens of tables for dozens of reasons. The best
# authority is the worse place to look.
#
# Getting this backwards costs rows rather than correctness: ordering by
# authority lets a primary filing's incidental table answer a quarter before
# the schedule built to state it, and the schedule is then never read.
DOCUMENT_FITNESS = [
    SourceType.EARNINGS_RELEASE,
    SourceType.SEC_FILING,
    SourceType.QUARTERLY_REPORT,
    SourceType.ANNUAL_REPORT,
    SourceType.INVESTOR_PRESENTATION,
    SourceType.COMPANY_IR,
    SourceType.TRANSCRIPT,
    SourceType.LLM_SEARCH,
    SourceType.USER_URL,
    SourceType.OTHER,
]


def reading_rank(source_type: Any) -> int:
    """Where a source sits in DOCUMENT_FITNESS; anything unknown reads last."""
    value = getattr(source_type, "value", str(source_type))
    for rank, known in enumerate(DOCUMENT_FITNESS):
        if known.value == value:
            return rank
    return len(DOCUMENT_FITNESS)


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


# How a deterministic candidate was obtained, from the reader that produced it.
# The reader's own label is used, and only if it is one this module recognises:
# the LLM branch and the deterministic branch share this code path, so trusting
# the key outright would let model output name its own provenance.
_DETERMINISTIC_METHODS = {"table_fingerprint": "table", "prose_sentence": "prose"}


def _deterministic_method(candidate: dict[str, Any]) -> str:
    """``table``, ``prose`` or ``llm`` for a candidate about to be stored.

    A sentence read by ``extraction/prose.py`` used to be stored as ``table``,
    because both readers reach this point through the same call and the label
    was decided by which branch it was on rather than by what produced it. That
    is the mistake this project keeps making: a field stamped on a whole branch,
    then read back as though it described the row.
    """
    if not candidate.get("_from_table"):
        return "llm"
    return _DETERMINISTIC_METHODS.get(str(candidate.get("extraction_method") or ""), "table")


# How far apart a declared figure and its normalization must be before the
# difference can only be a scaling error. Ten times is already impossible from
# rounding; the error this exists for is a thousand.
_SCALE_DISAGREEMENT = 10.0


def _scale_disagrees(
    value: float | None, normalized: float | None, unit: str | None, currency: str | None
) -> bool:
    """Whether a candidate's own two figures cannot both be right.

    Checked only for USD, because `scale_to_millions` models the unit and not
    an exchange rate, and only at an order of magnitude, because that is the
    difference a mis-scaling makes and rounding cannot.
    """
    if value is None or normalized is None:
        return False
    if (currency or "USD").strip().upper() != "USD":
        return False
    expected = scale_to_millions(float(value), unit)
    if not expected or not float(normalized):
        return False
    ratio = abs(float(normalized) / expected)
    return ratio >= _SCALE_DISAGREEMENT or ratio <= 1 / _SCALE_DISAGREEMENT


def scale_to_millions(value: float, unit: str | None) -> float:
    """Convert a reported value to USD millions using its declared unit.

    LLM candidates carry a free-text ``unit`` (see revenue_extractor.yaml)
    rather than the canonical label the deterministic table path produces,
    so this matches on substrings against the same UNIT_SCALE_TO_MILLIONS
    table fingerprint.py uses, defaulting to "millions" (scale 1.0) when the
    unit is missing or unrecognized - never silently truncating a "thousands"
    or "units" figure the way an unconditional else-branch did.
    """
    label = (unit or "").strip().lower()
    if "billion" in label:
        scale = UNIT_SCALE_TO_MILLIONS["billions"]
    elif "thousand" in label:
        scale = UNIT_SCALE_TO_MILLIONS["thousands"]
    elif "unit" in label:
        scale = UNIT_SCALE_TO_MILLIONS["units"]
    elif "million" in label:
        scale = UNIT_SCALE_TO_MILLIONS["millions"]
    else:
        scale = UNIT_SCALE_TO_MILLIONS["millions"]
    return value * scale


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
            # If the exception came out of a flush the session refuses every
            # statement, reading an expired attribute included, until it is
            # rolled back; without this the failure is never recorded and the
            # job looks as though it were still running.
            self.db.rollback()
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
                    # The 10-Q and 10-K carry the filer's own tagged facts. A
                    # figure it tagged states its period, its unit and which
                    # product it belongs to, so nothing about it has to be
                    # recovered from how a page is laid out.
                    include_xbrl=want_primary or want_earnings,
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
                job.product_id = product.id
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

        # The product and family rows above are complete, so they are committed
        # here rather than at the end of the step: a flushed row is a write
        # transaction, SQLite admits one writer, and the model calls below take
        # long enough that every other job's commit would time out against it.
        self.db.commit()

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

    @staticmethod
    def _candidate_of(row: DatapointORM) -> dict:
        """A stored datapoint read back as the candidate it came from."""
        return {
            "period": row.period,
            "period_type": row.period_type,
            "value_reported": row.value_reported,
            "value_normalized_usd_millions": row.value_normalized_usd_millions,
            "currency": row.currency,
            "unit": row.unit,
            "source_quote": row.source_quote,
        }

    def _datapoint_from_candidate(self, job: DrugJobORM, src, candidate: dict) -> DatapointORM:
        """A tagged fact, or a derived quarter, stored as a datapoint.

        Nothing is normalized on the way in because nothing was inferred: for a
        tagged fact the period, the unit and the currency are the filer's own
        and the quote is the citation naming the fact rather than a line of
        prose; for a derivation they come from the figures it subtracted.

        How the value was obtained is the candidate's to say. This wrote
        ``xbrl_fact`` for everything it stored, so a quarter that
        ``complete_series`` derived by arithmetic was exported as a figure the
        filer had tagged - a claim about provenance, in a column a reader uses
        to decide how much to trust the number, that was false for every
        derived row.
        """
        period = str(candidate.get("period") or "unknown")
        derived = bool(candidate.get("_derived"))
        row = DatapointORM(
            id=new_id(),
            job_id=job.id,
            source_id=src.source_id,
            period=period,
            value_reported=float(candidate["value_reported"]),
            value_normalized_usd_millions=float(candidate["value_normalized_usd_millions"]),
            currency=candidate.get("currency") or "USD",
            unit=candidate.get("unit") or "units",
            period_type=candidate.get("period_type") or "quarterly",
            revenue_scope=candidate.get("revenue_scope") or "Product family",
            formulation=candidate.get("formulation"),
            source_url=src.url,
            source_quote=candidate.get("source_quote") or "",
            extraction_method=str(candidate.get("extraction_method") or "xbrl_fact"),
            confidence_score=float(candidate.get("confidence") or 0.9),
            validation_status=ValidationStatus.PENDING.value,
            citation_json={
                "source_url": src.url,
                # The document tier reconciliation ranks by, before the claim.
                # Every other producer writes it; without it a tagged fact
                # sorted below a number read off a page.
                "source_type": src.source_type.value,
                "filing_type": src.filing_type,
                "accession": src.accession_number,
                "xbrl_member": candidate.get("xbrl_member"),
                "xbrl_context": candidate.get("xbrl_context"),
                "member_resolved_by": candidate.get("member_resolved_by"),
                # How far this figure may sit from the truth given how its
                # sources rounded, in USD millions. Absent means unknown, not
                # exact. A derived quarter inherits one rounding per input, so
                # this is the difference between a reader treating it as exact
                # and knowing it is a million either way.
                "rounding_uncertainty_usd_millions": candidate.get(
                    "rounding_uncertainty_usd_millions"
                ),
                "validation_status": ValidationStatus.PENDING.value,
                "interpreted": False,
                "period_reported": period,
            },
            issue_flags=["derived_from_reported_series"] if derived else ["extracted_from_xbrl"],
        )
        self.db.add(row)
        return row

    def _bulk_tagged_revenue(
        self, job: DrugJobORM
    ) -> tuple[list[DatapointORM], list[dict[str, Any]]]:
        """The same tagged facts, from the Commission's bulk extracts.

        `_tagged_revenue` can only read an instance the retrieve stage fetched,
        which is capped and walks EDGAR filing by filing. The Financial
        Statement and Notes Data Sets carry every filer's note-level facts for
        a whole month in one file, so a quarter the walk never reached is
        answered here without another request to sec.gov.

        It is off unless `notes_dataset_dirs` names a downloaded extract, and
        it adds nothing the instance path would have contradicted: both produce
        the same claim about the same fact, and the reconciler treats a
        duplicate as one answer rather than two votes.
        """
        settings = get_settings()
        configured = [
            Path(part)
            for part in (settings.notes_dataset_dirs or "").split(os.pathsep)
            if part.strip()
        ]
        if not configured or not job.cik:
            return [], []
        try:
            cik = int(str(job.cik).lstrip("0") or "0")
        except ValueError:
            return [], []

        register = member_store.load_register(self.db)
        products = self._candidate_products(job)
        rows: list[DatapointORM] = []
        totals: list[dict[str, Any]] = []
        for root in configured:
            if not (root / "num.tsv").exists():
                logger.info("notes_dataset_missing job_id=%s root=%s", job.id, root)
                continue
            try:
                found, notes = candidates_from_notes(
                    root,
                    product=job.drug_name,
                    issuer=job.manufacturer or "",
                    cik=cik,
                    products=products,
                    register=register,
                )
            except Exception:
                logger.exception("notes_dataset_unreadable job_id=%s root=%s", job.id, root)
                continue
            for note in notes:
                logger.info("notes_note job_id=%s root=%s %s", job.id, root.name, note)
            for candidate in found:
                # One source row per filing the facts were tagged in, so the
                # citation resolves the same way the instance path's does.
                src = RetrievedSource(
                    source_type=SourceType.SEC_FILING,
                    url=candidate.get("source_url") or "",
                    title=f"XBRL facts, {candidate.get('xbrl_accession')}",
                    filing_type="10-Q/10-K",
                    accession_number=candidate.get("xbrl_accession"),
                    retrieval_status=RetrievalStatus.SUCCESS,
                    metadata={"notes_dataset": root.name},
                )
                self._persist_sources(job, [src])
                if candidate.get("period_type") != PeriodType.QUARTERLY.value:
                    totals.append(candidate)
                    continue
                rows.append(self._datapoint_from_candidate(job, src, candidate))
        if rows or totals:
            logger.info(
                "notes_dataset_facts job_id=%s drug=%s quarters=%d totals=%d",
                job.id, job.drug_name, len(rows), len(totals),
            )
        return rows, totals

    def _candidate_products(self, job: DrugJobORM) -> list[str]:
        """The products a member may resolve to for this job.

        Everything the pipeline tracks, so that a member ending in a sibling's
        name goes to the sibling rather than to this one - and every drug this
        run was asked about, which is not always in the catalog yet. Without
        the second, a drug uploaded at run time is a drug no member can ever
        name: the rules would be asked to place `acme:CalderonMember` against a
        list with no Calderon in it, and would rightly decline.

        This can only narrow what is published: a resolution to any product
        other than the one asked for is dropped downstream.
        """
        return sorted(
            set(load_products()) | set(member_store.run_products(self.db, job.run_id))
        )

    async def _tagged_revenue(
        self, job: DrugJobORM, sources: list
    ) -> tuple[list[DatapointORM], list[dict[str, Any]]]:
        """Revenue this issuer tagged for this product, from its XBRL instances.

        Empty is the ordinary answer for a filing from before the issuer's
        detail-tagging cutoff. Nothing here knows what that cutoff is: an
        instance that tags no product-level revenue simply yields nothing.
        """
        rows: list[DatapointORM] = []
        totals: list[dict[str, Any]] = []
        register = member_store.load_register(self.db)
        products = self._candidate_products(job)
        learned: dict[tuple[str, str], Resolution] = {}
        element_register = elements.load_register()
        element_verdicts = elements.verdicts(element_register)
        for src in sources:
            if not (src.metadata or {}).get("xbrl_instance") or not src.storage_key:
                continue
            try:
                raw = await self.file_store.get(src.storage_key)
            except Exception:
                continue
            if not raw:
                continue
            # The filing's own arithmetic says which elements are sales and
            # which are costs of them. What it leaves unplaced is asked of the
            # model once per element and remembered, so the same question is
            # never asked twice and never answered by counting.
            calculation = None
            calculation_key = (src.metadata or {}).get("calculation_key")
            if calculation_key:
                try:
                    calculation = parse_calculation(await self.file_store.get(calculation_key))
                except Exception:
                    calculation = None
            try:
                facts = parse_facts(raw)
            except Exception:
                continue
            def names_a_product(member: str, issuer: str = job.manufacturer or "") -> bool:
                return resolve(member, products, register, issuer=issuer).resolved

            for element in sorted(unsettled_elements(
                facts, names_a_product=names_a_product, calculation=calculation
            )):
                if element in element_verdicts:
                    continue
                examples = [
                    f"{f.members} {f.value:,.0f} {f.unit} {f.start}..{f.end}"
                    for f in facts if f.element == element
                ][:12]
                answer = await self.llm.judge_element(element=element, examples=examples)
                verdict = Verdict(
                    element=element, is_revenue=answer.get("is_revenue"),
                    method="llm", confidence=float(answer.get("confidence") or 0.0),
                    note=answer.get("reason", ""),
                )
                element_register[element] = verdict
                if verdict.usable:
                    element_verdicts[element] = bool(verdict.is_revenue)
                logger.info("xbrl_element_judged job_id=%s element=%s is_revenue=%s conf=%.2f",
                            job.id, element, verdict.is_revenue, verdict.confidence)
            try:
                found, notes = candidates_from_instance(
                    raw,
                    product=job.drug_name,
                    issuer=job.manufacturer or "",
                    products=products,
                    register=register,
                    learned=learned,
                    calculation=calculation,
                    verdicts=element_verdicts,
                )
            except Exception:
                continue
            for note in notes:
                logger.info("xbrl_note job_id=%s source_id=%s %s", job.id, src.source_id, note)
            for candidate in found:
                # A twelve-month fact is not an answer to a quarter, so it is
                # not stored as a datapoint; it is what the fourth quarter is
                # derived against.
                if candidate.get("period_type") != PeriodType.QUARTERLY.value:
                    totals.append(candidate)
                    continue
                rows.append(self._datapoint_from_candidate(job, src, candidate))
        if learned:
            written = member_store.record_many(self.db, learned, products=products)
            logger.info("xbrl_members_learned job_id=%s members=%d", job.id, written)
        if rows or totals:
            logger.info(
                "xbrl_facts job_id=%s drug=%s quarters=%d totals=%d",
                job.id, job.drug_name, len(rows), len(totals),
            )
        return rows, totals

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
        # Period totals, kept aside rather than stored: they are not answers to
        # a quarterly question, they are what a missing quarter is subtracted
        # from.
        derivation_pool: list[dict[str, Any]] = []
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

        # The filer's own assertions come first. A tagged fact needs no
        # geometry read off it, so where one exists it is the better claim; the
        # table reader still runs, and the two are reconciled downstream like
        # any other pair of candidates.
        #
        # Every source, not the prioritised ones. `prioritize_sources_for_revenue`
        # ranks documents by how much prose and layout is worth reading, and
        # keeps the types in `REVENUE_PRIMARY_SOURCE_TYPES` when two of them
        # exist - which an XBRL instance is not, because a retrieved instance is
        # labelled by the report it belongs to. Passing the ranked subset here
        # fed the highest-trust reader on the output of a ranking that exists to
        # bound the most expensive ones, and dropped every instance whenever the
        # filer had two other filings in the window. `_tagged_revenue` reads
        # only what carries `xbrl_instance`, so handing it everything costs a
        # dictionary lookup per source.
        tagged_rows, tagged_totals = await self._tagged_revenue(job, sources)
        rows.extend(tagged_rows)
        derivation_pool.extend(tagged_totals)

        # The same class of claim, for filings the retrieve stage never reached.
        # Off unless an extract has been downloaded and configured.
        bulk_rows, bulk_totals = self._bulk_tagged_revenue(job)
        rows.extend(bulk_rows)
        derivation_pool.extend(bulk_totals)

        # A period a tagged fact already answered for this filing. The filer's
        # own XBRL is the strongest claim there is; nothing needs a second
        # reading of the same quarter in the same document.
        tagged_periods: dict[str, set[str]] = {}
        for row in tagged_rows:
            tagged_periods.setdefault(str(row.source_id or ""), set()).add(str(row.period))

        for src in selected_sources:
            doc = parsed.get(src.source_id)
            if not doc or doc.parsing_status.value != "success":
                continue
            if src.source_type == SourceType.OPENFDA:
                continue

            period_context = detect_period_context(doc.full_text)

            # The deterministic readers run first, and the model is what
            # happens when they come back empty.
            #
            # It used to be the other way around: the model ran on every
            # in-budget filing and the table reader was bolted on beside it
            # because "the model omits rows unpredictably". The backstop then
            # outgrew the thing it was backing - it reads tagged facts, it
            # fingerprints a table for the unit it declares, it runs on every
            # source rather than the first few - and `CLAIM_STRENGTH` was
            # updated to say so, ranking `llm` below every deterministic
            # producer. What never moved was the call site.
            #
            # So the model was still being asked about quarters that were
            # already answered, and its answer could not win: two rows for one
            # period are sorted by `claim_rank` and the loser is flagged
            # `conflict_with_higher_priority_source`. An eager model call could
            # only agree - costing a request to confirm what was already known
            # - or disagree and manufacture a `needs_review` row for a human to
            # adjudicate, having already lost. Asking it only where nothing
            # else could answer keeps every row it can actually contribute.
            fingerprinted, table_findings, table_skips = extract_revenue_candidates(
                doc.tables,
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
                context=doc.full_text[:4000],
                grids=doc.table_grids, captions=doc.table_captions,
                prose=doc.full_text,
                # What the filing says it covers, for a schedule that states no
                # period itself. Computed just above and, until now, handed only
                # to the model - while the reader four lines down was skipping
                # tables for want of exactly this.
                period_context=period_context,
            )
            # A producer says everything it can about the source; which of
            # those answers is a datapoint and which is something to subtract
            # from is decided here, because only here are both destinations
            # known. A quarter the issuer never stated on its own is the
            # difference between a total it did state and the quarters it did,
            # so the totals are routed, not discarded.
            period_totals = [
                candidate
                for candidate in fingerprinted
                if candidate.get("period_type") != PeriodType.QUARTERLY.value
            ]
            fingerprinted = [
                candidate
                for candidate in fingerprinted
                if candidate.get("period_type") == PeriodType.QUARTERLY.value
            ]
            derivation_pool.extend(period_totals)
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
            kept = list(table_rows)

            # Which quarters of this filing already have a deterministic
            # answer. A period in here is not put to the model, and a model row
            # for one is not merged: it is the losing side of a conflict that
            # has already been decided.
            answered = set(tagged_periods.get(str(src.source_id), set()))
            answered.update(
                str(row.get("period"))
                for row in table_rows
                if row.get("value_reported") is not None
            )

            llm_text, evidence_meta = build_revenue_llm_text(
                doc,
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
            )
            if evidence_meta.get("had_product_money_hits"):
                any_product_money = True

            # Skip the LLM when a filing has no product+$ evidence (avoid XBRL /
            # company-total noise), when it is beyond the extraction budget, or
            # now when the deterministic readers have already answered it.
            no_product_evidence = evidence_meta.get("strategy") in {
                "no_product_mention",
                "empty",
            } or not evidence_meta.get("had_product_money_hits")
            # A finding means a table was read but something about the reading
            # is suspect, so the filing is worth a second opinion even though
            # it produced rows.
            deterministic_answered = bool(answered) and not table_findings
            use_llm = (
                src.source_id in llm_source_ids
                and not no_product_evidence
                and not deterministic_answered
            )
            if not use_llm:
                src_row = self.db.get(SourceDocumentORM, src.source_id)
                if src_row:
                    if deterministic_answered:
                        reason = "deterministic_answered"
                    elif no_product_evidence:
                        reason = "no_product_evidence"
                    else:
                        reason = "over_source_budget"
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
            llm_kept, dropped = filter_revenue_candidates(
                result.get("candidates") or [],
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
                source_text=span_corpus,
            )
            dropped = list(llm_dropped) + list(dropped)
            dropped_total += len(dropped)

            # Only the quarters nothing else answered.
            added = [row for row in llm_kept if str(row.get("period")) not in answered]
            if added:
                kept = kept + added
            if llm_kept or table_rows:
                logger.info(
                    "revenue_rows_extracted job_id=%s source_id=%s "
                    "deterministic=%s llm_offered=%s llm_added=%s",
                    job.id,
                    src.source_id,
                    len(table_rows),
                    len(llm_kept),
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
                    normalized = scale_to_millions(float(value), unit)
                # A candidate may supply its own normalization, and it was
                # taken verbatim. A candidate arrived reported as 87.4 with
                # 87,400 beside it and was published, because every check
                # downstream reads `value_reported` - the judge confirms 87.4
                # against a quote saying 87.4 - while a consumer reads the
                # normalized figure that nothing had looked at.
                #
                # Recomputed from the figure and unit the candidate declares
                # itself. Only an order-of-magnitude disagreement is acted on,
                # which is the shape a scaling error has; anything smaller can
                # be rounding or an FX rate this function does not model, so it
                # is left alone rather than guessed at.
                mis_scaled = _scale_disagrees(value, normalized, unit, currency)

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
                if mis_scaled:
                    issue_flags.append("normalization_disagrees_with_unit")
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
                    extraction_method=_deterministic_method(cand),
                    confidence_score=float(cand.get("confidence") or 0.5),
                    validation_status=ValidationStatus.PENDING.value,
                    citation_json=citation,
                    issue_flags=issue_flags or None,
                )
                self.db.add(row)
                rows.append(row)
                if src_row:
                    src_row.relevant_datapoints_found = (src_row.relevant_datapoints_found or 0) + 1

        # Stage 3b: the quarters this product's own series implies. A fourth
        # quarter an issuer never stated on its own is the difference between
        # the year it did state and the three quarters it did, which is the
        # issuer's arithmetic rather than an estimate - and it is applied only
        # where every other quarter of that total is present.
        # Only figures at least as strong as a derivation count as reported
        # when deciding which quarter is missing. `complete_series` treats any
        # candidate for a period as that period being answered, so a sentence
        # reading 1.0 made the quarter non-missing and the derivation was never
        # computed at all - which is upstream of the ranking below, and is why
        # ranking alone moved nothing. The weak reading is not discarded; it is
        # simply not evidence about what still needs deriving.
        derived_rank = claim_rank("derived_from_period_total")
        derivable = [
            self._candidate_of(row)
            for row in rows
            if claim_rank(row.extraction_method) <= derived_rank
        ]
        derived = complete_series(
            {job.drug_name: derivable + derivation_pool}, product=job.drug_name
        )
        # Only a stronger claim pre-empts a derivation. Skipping every period
        # anything had been found for meant a sentence reading 1.0 did not lose
        # to a family total that derives exactly to 94.645 - it stopped that
        # total ever being computed. Where a weaker reader answered, both now
        # stand and reconciliation ranks them.
        strongest: dict[str, int] = {}
        for row in rows:
            rank = claim_rank(row.extraction_method)
            if rank < strongest.get(row.period, len(CLAIM_STRENGTH) + 1):
                strongest[row.period] = rank
        for candidate in derived:
            if strongest.get(str(candidate["period"]), len(CLAIM_STRENGTH) + 1) <= derived_rank:
                continue
            source = next((s for s in selected_sources), None)
            if source is None:
                break
            rows.append(self._datapoint_from_candidate(job, source, candidate))
        if derived:
            logger.info(
                "derived_quarters job_id=%s drug=%s derived=%d", job.id, job.drug_name, len(derived)
            )

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

            # A "Product family" row with no formulation is one line covering the
            # whole family, and "aggregate" is the word for that. Writing it
            # restates the scope the row already carries rather than estimating
            # anything, so it goes on the row directly.
            #
            # It used to go through apply_field_enrichment, whose contract is
            # that any fill forces needs_review and caps confidence at 0.55.
            # That contract is right for a model's suggestion about a blank
            # field and wrong for a tautology. The flag disqualifies a row from
            # auto_pass twice over - directly, and by holding confidence under
            # the threshold the quality gate needs - so applying it here
            # withholds rows the judge has already called supported, with
            # nothing else against them.
            fill = deterministic_formulation_fill(
                {"revenue_scope": row.revenue_scope, "formulation": row.formulation}
            )
            if fill:
                row.formulation = fill["suggested_formulation"]

            # LLM enrichment on whatever is still blank. A suggestion about a
            # field nobody read off the document is an estimate, and it keeps
            # the review that estimates get.
            enrichment = merge_enrichment_dicts(
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
            if "normalization_disagrees_with_unit" in (row.issue_flags or []):
                # The row's own two figures cannot both be right, and the judge
                # cannot see it: it reads the quote against `value_reported`,
                # which is the one that agrees. Nothing else looks at the
                # normalized figure, which is the one published.
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
                if not wid:
                    # The model saw the disagreement and declined to settle it.
                    # That is a question for the ranking below, not a verdict
                    # against everything in the group: marking them all losers
                    # withheld the right answer along with the wrong one, and
                    # then the fallback skipped the group because it already
                    # had losers in it: a schedule and a sentence disagreeing,
                    # both demoted, nothing published.
                    continue
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
            # The document first, then how strong a claim the producer makes,
            # then confidence. Source type alone leaves a sentence and a
            # schedule from one exhibit tied, and the tie was broken by
            # whichever happened to be extracted first.
            group.sort(key=lambda r: (
                priority_index.get((r.citation_json or {}).get("source_type", ""), 99),
                claim_rank(r.extraction_method),
                -float(r.confidence_score or 0),
            ))
            winners.add(group[0].id)
            for loser in group[1:]:
                losers.add(loser.id)

        # Neither the model nor the ranking may settle a disagreement between
        # claims of equal strength. Two tagged facts for one product and
        # period - one filing's own quarter and a later filing's comparative
        # of it - rank identically, and whichever sorted first was published
        # while the other was held as the loser of a conflict it had not lost.
        # Where the strongest claims in a group disagree by more than the
        # precision their sources declared, every one of them is held: the
        # documents contradict each other, and that is a question for a
        # person, not a coin toss dressed as a verdict.
        contested: set[str] = set()
        for group in by_key.values():
            if len(group) < 2:
                continue
            tier = lambda r: (
                priority_index.get((r.citation_json or {}).get("source_type", ""), 99),
                claim_rank(r.extraction_method),
            )
            top = min(tier(r) for r in group)
            strongest = [r for r in group if tier(r) == top
                         and r.value_normalized_usd_millions is not None]
            if len(strongest) < 2:
                continue
            values = [float(r.value_normalized_usd_millions) for r in strongest]
            declared = [(r.citation_json or {}).get("rounding_uncertainty_usd_millions")
                        for r in strongest]
            if all(d is not None for d in declared):
                slack = sum(float(d) for d in declared)
            else:
                slack = max(ROUNDING_ABSOLUTE, ROUNDING_TOLERANCE * max(abs(v) for v in values))
            if max(values) - min(values) > slack:
                contested.update(r.id for r in strongest)
        winners -= contested
        losers |= contested

        for row in rows:
            if row.id in losers:
                row.validation_status = ValidationStatus.NEEDS_REVIEW.value
                flag = ("equal_strength_claims_disagree" if row.id in contested
                        else "conflict_with_higher_priority_source")
                row.issue_flags = list(set((row.issue_flags or []) + [flag]))
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
