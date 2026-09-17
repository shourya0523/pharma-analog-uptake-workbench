from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ruff: noqa: BLE001
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.connectors.coverage import record_coverage
from app.connectors.llm_search import LLMSearchConnector
from app.connectors.openfda import OpenFDAConnector
from app.connectors.openfda_fields import (
    brand_matched_results,
    earliest_approval_date,
    earliest_approved_match,
    openfda_brand_names,
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
    DerivationLineageORM,
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
    utc_now,
)
from app.domain.claims import stated_labels, stated_number, stated_text
from app.domain.models import (
    NO_FILER_OF_RECORD,
    PUBLISHED_STATUS_VALUES,
    REPORTED_WITH_ANOTHER_PRODUCT,
    JobStatus,
    JobStep,
    PeriodType,
    QualityCheckStatus,
    RetrievalStatus,
    RetrievedSource,
    SeriesSelection,
    SourceType,
    ValidationStatus,
    new_id,
)
from app.extraction import elements, member_store
from app.extraction.bulk_tagged import candidates_from_notes
from app.extraction.candidates import extract_revenue_candidates
from app.extraction.check import _ROUNDING_ABSOLUTE as ROUNDING_ABSOLUTE
from app.extraction.check import _ROUNDING_TOLERANCE as ROUNDING_TOLERANCE
from app.extraction.derive import HELD_FOR_BOUND, complete_series
from app.extraction.elements import Verdict
from app.extraction.fingerprint import UNIT_SCALE_TO_MILLIONS
from app.extraction.members import Resolution, load_products, resolve
from app.extraction.tagged import candidates_from_instance
from app.identity.resolver import ResolvedProductIdentity, resolve_product_identity
from app.llm.aliases import merge_aliases
from app.llm.client import LLMModules, listed, mappings
from app.parsing.documents import DocumentParser
from app.parsing.evidence import (
    build_revenue_llm_text,
    prioritize_sources_for_revenue,
    select_product_evidence_text,
)
from app.parsing.fda_label import (
    format_moa_profile_value,
    parse_label_record,
    profile_fields,
)
from app.parsing.indications import parse_indications, therapeutic_area
from app.parsing.labels import FLAG_COMBINED, QUESTION_FLAGS, footnotes_in
from app.parsing.periods import (
    detect_period_context,
    normalize_period,
    quarters_reported_in,
)
from app.parsing.tables import sibling_row_labels
from app.parsing.xbrl import parse_calculation, parse_facts, unsettled_elements
from app.pipeline.series_identity import (
    CLAIM_STRENGTH,
    SeriesReading,
    _scope_key,
    agrees_within_declared_precision,
    claim_rank,
    normalize_geography,
    select_series_figures,
    series_identity,
)
from app.quality.candidate_filters import (
    filter_revenue_candidates,
    peer_product_names,
    quote_mentions_product,
)
from app.quality.checks import (
    DUPLICATE_SERIES_READING,
    QualityIssue,
    apply_auto_pass_gate,
    moa_epc_contamination_issue,
    quote_contains_value,
    run_quality_checks,
)
from app.quality.comparative import derive_comparative_candidates
from app.quality.completeness import quarters_the_run_asked_for, refresh_completeness
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

# How much authority a document carries for a revenue figure, best first.
# Every member of `SourceType` is ranked, including the two that state no
# revenue at all: a label document ranks last rather than falling through to
# the unknown rank, so the ranking says what it thinks of them instead of
# leaving it to a default. `test_every_source_type_is_ranked` holds the list
# exhaustive, so a source type added to the enum fails here rather than
# silently arriving at the bottom.
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
    SourceType.OPENFDA,
    SourceType.DAILYMED,
]


# How long after a period ends its own report is filed: a 10-Q within about
# 45 days, a 10-K within about 90. A filing later than this reports the
# period as a comparative.
_REPORTING_LAG_DAYS = 120


def period_end(period: str | None) -> date | None:
    """The calendar day a period label ends on: 2024Q2 -> 30 June 2024."""
    match = re.fullmatch(r"(\d{4})(?:Q([1-4]))?", period or "")
    if not match:
        return None
    year, quarter = int(match.group(1)), int(match.group(2) or 4)
    month = quarter * 3
    return date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)


def stamp_series_identity(job: DrugJobORM, row: DatapointORM) -> DatapointORM:
    """Say which series a reading belongs to, from what the reading declares.

    Written when the row is, and again once the labels are final, because
    enrichment and reconciliation both change what a row says it is a figure
    for - a region the enricher reads off the quote, the pair a corroborator
    was reported as - and the series a row belongs to is whatever it ends up
    declaring, not what it declared first. A row a reviewer types is stamped
    the same way, or it would belong to no series at all.
    """
    row.geography_normalized = normalize_geography(row.geography)
    row.series_identity = series_identity(
        issuer=job.cik or job.manufacturer,
        product=job.drug_name,
        revenue_scope=row.revenue_scope,
        geography=row.geography,
        formulation=row.formulation,
        reported_as=row.reported_as,
        currency=row.currency,
        period_type=row.period_type,
        combined_line=FLAG_COMBINED in set(row.issue_flags or []),
    )
    return row


def _declared_uncertainty(row: DatapointORM) -> float | None:
    """The rounding bound this row's source declared, if it declared one."""
    return (row.citation_json or {}).get("rounding_uncertainty_usd_millions")


def _agrees_within_declared_precision(winner: DatapointORM, other: DatapointORM) -> bool:
    """Whether two stored rows for one period are one figure at two precisions."""
    return agrees_within_declared_precision(
        float(winner.value_normalized_usd_millions),
        float(other.value_normalized_usd_millions),
        declared=(_declared_uncertainty(winner), _declared_uncertainty(other)),
    )


# How the judge spells a veto in a row's issues (`apply_judge_hard_vetoes`,
# `llm/client.py`). A snapshot of one prefix, not of the vetoes themselves -
# the list of those grows, and nothing here has to know it. What would make
# this stale is the judge renaming the prefix, and the symptom would be a
# vetoed row treated as clean.
_VETO_PREFIX = "hard_veto:"

# The flag reconciliation raises when a lower-priority source disagrees with a
# higher one, named once so the pass that raises it and the pass that counts it
# cannot drift apart.
FLAG_CONFLICT_WITH_HIGHER_PRIORITY = "conflict_with_higher_priority_source"


def _is_a_code(issue: str) -> bool:
    """Whether an issue is a code, rather than a sentence about the row.

    `issue_flags` is a column readers match against by name, and the model
    answering the judge writes its reasoning into the same list the vetoes
    write their codes into. The two are told apart by shape rather than by a
    register of the codes, which grows: a code is one token and a sentence is
    not. `hard_veto:quote_states_a_different_period` is a code; "The primary
    quote states a different figure" is not.
    """
    return bool(issue) and not any(character.isspace() for character in issue)


def _publishes(row: DatapointORM) -> bool:
    """Whether the status this row carries is one the pipeline stands behind."""
    return row.validation_status in PUBLISHED_STATUS_VALUES


def _unquestioned(row: DatapointORM) -> bool:
    """Whether anything on the row makes it a question rather than an answer.

    A veto the judge recorded, or a label flag the reader raised. Both are
    reasons a person has to look; a row carrying either cannot stand in for a
    reading that was held.
    """
    flags = set(row.issue_flags or [])
    return not (
        any(flag.startswith(_VETO_PREFIX) for flag in flags) or flags & LABEL_FLAGS
    )


def _carry_to_winner(winner: DatapointORM, other: DatapointORM) -> None:
    """Move what a corroborating row knows and the published row does not.

    The two rows are one figure read twice, so anything either of them says
    about the figure is true of the published one: the pair a combined line is
    reported as, the note the filer attached to it, and the flags that note
    raised. Discarding them publishes the figure as something it is not - a
    stub covering part of a quarter published as the quarter, an
    `acme:CalderonMember` line published as Calderon's own when the row it
    corroborates was read from "Calderon + NuVessa".

    A flag that makes a row a question is not information a published row may
    absorb quietly, so carrying one holds the figure.
    """
    if other.reported_as and not winner.reported_as:
        winner.reported_as = other.reported_as
    citation = dict(winner.citation_json or {})
    changed = False
    if other.reported_as and not citation.get("combined_with"):
        combined = (other.citation_json or {}).get("combined_with")
        if combined:
            citation["combined_with"] = list(combined)
            changed = True
    notes = footnotes_in(other.source_quote or "")
    if notes and not footnotes_in(winner.source_quote or ""):
        citation["corroborator_footnote"] = " ".join(notes)
        changed = True
    carried = sorted((set(other.issue_flags or []) & LABEL_FLAGS) - set(winner.issue_flags or []))
    if carried:
        winner.issue_flags = sorted(set((winner.issue_flags or []) + carried))
        winner.validation_status = ValidationStatus.NEEDS_REVIEW.value
        citation["validation_status"] = winner.validation_status
        changed = True
    if changed:
        winner.citation_json = citation


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
# Exhaustive over `SourceType` for the same reason SOURCE_PRIORITY is: a
# product label is a document this pipeline retrieves, and where it sits in a
# search for quarterly sales is a decision, not a fall-through.
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
    SourceType.OPENFDA,
    SourceType.DAILYMED,
]


def reading_rank(source_type: Any) -> int:
    """Where a source sits in DOCUMENT_FITNESS; anything unknown reads last."""
    value = getattr(source_type, "value", str(source_type))
    for rank, known in enumerate(DOCUMENT_FITNESS):
        if known.value == value:
            return rank
    return len(DOCUMENT_FITNESS)


def reported_as_for(product: str, candidate: dict[str, Any]) -> str | None:
    """What a figure is a figure for, where that is not the product asked about.

    A filer that sells two products together prints one line for both, and
    nobody publishes the split: there is no agreed way to divide a
    co-administered regimen from outside, and the companies themselves report
    the pair. So the pair is the honest unit, and this names it.

    The order is the filer's, taken from where each name falls in the row the
    quote holds, so that one printed line has one name however it was reached:
    a row printed "NuVessa / Calderon" is the NuVessa + Calderon line whether
    the question was about Calderon or about NuVessa. A name the quote does not
    carry keeps its place behind the ones that do.

    `None` for a row that is the product's own, which is almost all of them.
    """
    combined = list(candidate.get("combined_with") or ())
    if not combined:
        return None
    names = [product, *combined]
    quote = (candidate.get("source_quote") or "").lower()
    def printed_at(pair: tuple[int, str]) -> tuple[int, int]:
        position = quote.find(pair[1].lower())
        return (len(quote) if position < 0 else position, pair[0])
    return " + ".join(name for _, name in sorted(enumerate(names), key=printed_at))


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
# The label flags that keep a figure from being published without a person.
# What the judge is told about a row's label: every flag that makes the row a
# question, plus the bound a derivation held rather than published. Built from
# the reader's vocabulary rather than restated, so a flag the reader starts
# raising cannot be one the judge never hears about.
LABEL_FLAGS = QUESTION_FLAGS | {HELD_FOR_BOUND}

_DETERMINISTIC_METHODS = {"table_fingerprint": "table", "prose_sentence": "prose"}


def _read_from(candidate: dict[str, Any], src: Any) -> dict[str, Any]:
    """The candidate with the document it was read from stamped on it.

    A period total is kept aside rather than stored, so it never becomes a row
    whose source anything can look up. A derivation that subtracts it has to
    say which filing the figure came out of, and this is the only point where
    that is known.
    """
    return {
        **candidate,
        "source_id": candidate.get("source_id") or getattr(src, "source_id", None),
        "source_url": candidate.get("source_url") or getattr(src, "url", None),
    }


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
    label = stated_text(unit).lower()
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


def claim_ranking(db: Session, job: DrugJobORM) -> tuple[Callable, Callable, Callable]:
    """How the claims about one period of one job rank, strongest first.

    Returned as functions over rows rather than computed in place, because
    reconciliation and the series selection have to rank the same readings
    the same way: a selection that ordered them differently would publish
    one figure and select another.
    """
    priority_index = {t.value: i for i, t in enumerate(SOURCE_PRIORITY)}
    # When each source was filed, for telling the filing that reports a
    # period from a later filing's comparative of it.
    job_sources = db.query(SourceDocumentORM).filter_by(job_id=job.id).all()
    source_dates = {src.id: src.source_date for src in job_sources}
    accessions = {src.id: src.accession_number for src in job_sources}

    def accession_of(row: DatapointORM) -> str | None:
        """The filing a claim came from, by the source row it cites."""
        return accessions.get(str(row.source_id or "")) or (
            (row.citation_json or {}).get("accession_number")
            or (row.citation_json or {}).get("accession")
        )

    def reports_own_period(row: DatapointORM) -> int:
        """0 for the filing that reports the period, 2 for a later one, 1 unknown.

        The filing that reports a quarter states it; a filing a year later
        prints it as a comparative, restated if the issuer recast anything
        since. The first is the figure for the period; the second is what
        the issuer later said about it, which is a different claim.
        """
        filed = parse_filing_date(source_dates.get(str(row.source_id or "")))
        ends = period_end(row.period)
        if filed is None or ends is None:
            return 1
        return 0 if filed <= ends + timedelta(days=_REPORTING_LAG_DAYS) else 2

    def claim_tier(row: DatapointORM) -> tuple[int, int, int]:
        """Where a row stands among the claims about one period.

        The filing that reports the period comes first, because a later
        filing's comparative is a different claim about it. Then how
        strong a claim the producer makes, and only then the document it
        sits in - in that order, because the two do not measure the same
        thing and the document was measuring the wrong one: an instance
        retrieved inside a 10-Q is typed a quarterly report while the
        human-readable document of the same accession is typed a filing,
        so a fact the filer tagged lost to a model's sentence about the
        page beside it. Which document a figure is in cannot separate two
        readings of one document; what produced the reading can.
        """
        return (
            reports_own_period(row),
            claim_rank(row.extraction_method),
            priority_index.get((row.citation_json or {}).get("source_type", ""), 99),
        )

    return claim_tier, reports_own_period, accession_of

def select_job_series(
    db: Session,
    job: DrugJobORM,
    rows: list[DatapointORM],
    checks: Sequence[tuple[QualityIssue, QualityCheckORM]] = (),
) -> None:
    """Say which reading each of the job's series holds for each quarter.

    Every surface downstream - the chart, the export, Product Detail -
    used to answer this for itself, from the rows in whatever order they
    arrived, which is how a quarter reported worldwide and again for one
    region plotted the region. It is answered once here, against the same
    ranking reconciliation used, and written on the row.

    A duplicate the selection settles is not left open: the check that
    recorded it says which reading the series holds instead, so a reader
    of the quality sheet sees a question answered rather than a hundred
    that never close.
    """
    claim_tier, _reports_own_period, _accession_of = claim_ranking(db, job)
    standings = select_series_figures(
        [
            SeriesReading(
                id=row.id,
                cell=(row.period, row.period_type or ""),
                identity=row.series_identity or "",
                value=row.value_normalized_usd_millions,
                publishes=_publishes(row),
                strength=(*claim_tier(row), -float(row.confidence_score or 0)),
                rounding_uncertainty=_declared_uncertainty(row),
            )
            for row in rows
        ]
    )
    for row in rows:
        row.series_selection = standings[row.id].selection
    for issue, check in checks:
        standing = standings.get(str(issue.affected_datapoint or ""))
        if issue.issue_type != DUPLICATE_SERIES_READING or standing is None:
            continue
        if standing.selection in {SeriesSelection.DUPLICATE.value,
                                  SeriesSelection.SUPERSEDED.value}:
            check.status = QualityCheckStatus.RESOLVED.value
            check.explanation = (
                f"{issue.explanation} The series holds {standing.held_by} "
                f"for this quarter; this reading is {standing.selection}."
            )
    logger.info(
        "series_selection job_id=%s drug=%s selected=%s duplicate=%s superseded=%s undecided=%s",
        job.id,
        job.drug_name,
        sum(1 for s in standings.values() if s.selection == SeriesSelection.SELECTED.value),
        sum(1 for s in standings.values() if s.selection == SeriesSelection.DUPLICATE.value),
        sum(1 for s in standings.values() if s.selection == SeriesSelection.SUPERSEDED.value),
        sum(1 for s in standings.values() if s.selection is None),
    )

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
        job.updated_at = utc_now()
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
            # The product's own documents first, then who files for it, then
            # the filings. A drug label names its sponsor; EDGAR's name index
            # answers a company name. Asked in the old order, `_identity` had
            # only what the caller typed - a drug name on its own reaches no
            # index, so it went to the model, which returned a CIK and a
            # confidence nobody read, and the filings of whoever that was were
            # downloaded against this job.
            #
            # The aliases are expanded before any of it, because the label
            # documents are matched to the product by name and the brand the
            # caller typed is one of the names it goes by.
            await self._expand_aliases(job)
            characterisation = {**options, "sec_filings": False,
                                "earnings_releases": False, "company_ir": False,
                                "transcripts": False}
            sources = await self._retrieve(job, characterisation)
            parsed = await self._parse(job, sources)
            await self._label_metadata(job, sources, parsed, options)
            await self._identity(job)
            filings = await self._retrieve(job, {**options, "openfda": False})
            filings_parsed = await self._parse(job, filings)
            # The label pass reads openFDA and the narrative pass reads a
            # filing, so the second half of the metadata step waits for the
            # filings the first half runs in order to find.
            await self._narrative_metadata(job, filings, filings_parsed, options)
            sources = list(sources) + list(filings)
            parsed = {**parsed, **filings_parsed}
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
            unfiled = self._quarters_no_filing_covers(job, sources, options, datapoint_rows)
            searched: list = []
            if unfiled and get_settings().enable_llm_search:
                searched, extra_parsed = await self._search_quarters_fallback(job, unfiled)
                if searched:
                    sources = list(sources) + searched
                    parsed = {**parsed, **extra_parsed}
                    datapoint_rows = datapoint_rows + await self._extract_revenue(
                        job, sources, parsed, options, only_source_ids={s.source_id for s in searched}
                    )
            self._record_unfiled_quarters(job, unfiled, datapoint_rows, searched)
            await self._judge(job, datapoint_rows, sources, parsed, options)
            await self._quality_and_validation(job)
            self._record_quarters_only_reported_with_another_product(job, datapoint_rows)
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

    # What the model is asked when it expands a product's aliases, and so what
    # a stored answer is an answer to. A stored decision cannot answer a
    # question it was not asked: the same brand from a different filer, or
    # under a different generic, is a different question.
    ALIAS_QUESTION = ("drug_name", "generic_name", "manufacturer", "ticker")

    def _aliases_already_expanded(self, job: DrugJobORM) -> list[str] | None:
        """The answer a job that asked this same question already recorded.

        Aliases are a property of the product, not of the run, so a second
        job for the same product asking the same question has already been
        answered. This is a cache in front of a procedure that still works
        without it: delete every stored answer and the next job asks the model
        again, at one call.

        The question is `ALIAS_QUESTION`, not the product name alone. A stored
        answer may only be reused for a job that asked the same thing - a
        negative recorded against one manufacturer is not an answer for
        another's.
        """
        query = self.db.query(DrugProfileFieldORM).join(
            DrugJobORM, DrugJobORM.id == DrugProfileFieldORM.job_id
        ).filter(
            DrugProfileFieldORM.field == "llm_aliases",
            DrugJobORM.id != job.id,
        )
        for field in self.ALIAS_QUESTION:
            asked = getattr(job, field)
            column = getattr(DrugJobORM, field)
            query = query.filter(column.is_(None) if asked is None else column == asked)
        row = query.order_by(DrugProfileFieldORM.id.desc()).first()
        if row is None:
            return None
        try:
            stored = json.loads(row.value or "{}")
        except json.JSONDecodeError:
            return None
        merged = stored.get("merged")
        if not isinstance(merged, list) or not merged:
            return None
        return [str(alias) for alias in merged]

    async def _expand_aliases(self, job: DrugJobORM) -> list[str]:
        settings = get_settings()
        base = merge_aliases(job.drug_name, job.generic_name)
        if not settings.enable_llm_search:
            self._job_aliases = base
            return base
        reused = self._aliases_already_expanded(job)
        if reused is not None:
            self._job_aliases = reused
            logger.info("alias_expansion_reused job_id=%s drug=%s aliases=%d",
                        job.id, job.drug_name, len(reused))
            return reused
        result = await self.llm.expand_aliases(
            product=job.drug_name,
            generic=job.generic_name,
            manufacturer=job.manufacturer,
            ticker=job.ticker,
        )
        merged = merge_aliases(
            job.drug_name,
            job.generic_name,
            llm_aliases=listed(result, "aliases"),
            formulations=listed(result, "formulations"),
            parent_companies=listed(result, "parent_companies"),
        )
        self._job_aliases = merged
        payload = {
            "aliases": listed(result, "aliases"),
            "formulations": listed(result, "formulations"),
            "parent_companies": listed(result, "parent_companies"),
            "search_terms": listed(result, "search_terms"),
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
                    "retrieval_date": utc_now().isoformat(),
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
        """Who files for this product, from the index before the model.

        The name index answers a ticker or a company name, either on its own,
        so the question is put to it whenever the job holds one of them. What
        the job holds depends on what ran first: a drug name alone reaches no
        index at all, which is why the label is read before this step and the
        sponsor it names is on the job by the time this asks.
        """
        self._set_step(job, JobStep.IDENTITY_RESOLVE)
        if not self._job_aliases:
            await self._expand_aliases(job)
        if not job.cik and (job.ticker or job.manufacturer):
            cik = await self.sec.resolve_cik(job.ticker, job.manufacturer)
            if cik:
                job.cik = cik
                self.db.commit()
                logger.info("cik_resolved job_id=%s drug=%s cik=%s via=sec", job.id, job.drug_name, cik)
        if not job.cik and get_settings().enable_llm_search:
            # The resolution says what it decided and why, so a refusal reaches
            # the job as well as an acceptance: "the model named no CIK" and
            # "the model was never asked" are different things for a reader,
            # and only the second leaves nothing behind.
            resolution = await self.search.resolve_identity_from_search(
                product=job.drug_name,
                manufacturer=job.manufacturer,
                ticker=job.ticker,
                aliases=self._job_aliases,
            )
            if resolution:
                job.quality_flags = sorted(set((job.quality_flags or []) + resolution.flags))
            if resolution and resolution.accepted:
                job.cik = resolution.cik
                self.db.commit()
                logger.info(
                    "cik_resolved job_id=%s drug=%s cik=%s via=llm_search",
                    job.id, job.drug_name, resolution.cik,
                )

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

        # What the search fallback is for is an issuer with nothing filed, not
        # an issuer whose filings could not be fetched. EDGAR refuses under
        # load, and a run that was refused looked exactly like a run that found
        # nothing: the fallback then published investor-relations pages for a
        # filer whose own quarterly reports were sitting behind a rate limit.
        sec_found = [
            s for s in collected
            if s.source_type in {SourceType.SEC_FILING, SourceType.EARNINGS_RELEASE}
        ]
        sec_ok = any(s.retrieval_status == RetrievalStatus.SUCCESS for s in sec_found)
        # A third question, before the two above: were filings asked for at
        # all. The pass that reads the product's own label asks for none, and
        # "nothing was listed" is not a finding about an issuer there.
        asked_for_filings = want_primary or want_earnings
        if sec_found and not sec_ok:
            job.quality_flags = list(set((job.quality_flags or []) + ["sec_retrieval_failed"]))
            logger.warning(
                "sec_retrieval_failed job_id=%s drug=%s listed=%d fetched=0",
                job.id, job.drug_name, len(sec_found),
            )
        elif asked_for_filings and not sec_found and not job.cik:
            # Nothing was listed because nothing said who files for this
            # product. "We could not reach the SEC" is a different sentence
            # from "we do not know whose filings to ask for", and the first
            # was being shown for the second: a reader takes it as a transient
            # failure of ours and retries, when what is missing is the issuer.
            job.quality_flags = list(set((job.quality_flags or []) + [NO_FILER_OF_RECORD]))
            logger.warning(
                "no_filer_of_record job_id=%s drug=%s ticker=%s manufacturer=%s",
                job.id, job.drug_name, job.ticker, job.manufacturer,
            )
        if asked_for_filings and get_settings().enable_llm_search and not sec_ok and not sec_found:
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
        # What each document turned out to hold for this product, asked once
        # here because this is the only place a retrieved source and its
        # parsed document are both in hand. It decides nothing about what was
        # fetched; it records what the fetching got, which is the measurement
        # any argument about retrieval order has to start from.
        periods = sorted(quarters_the_run_asked_for(job))
        aliases = self._job_aliases or [job.drug_name]
        products = self._candidate_products(job)
        for src in sources:
            doc = await self.parser.parse(src)
            parsed_map[src.source_id] = doc
            verdict = record_coverage(
                src, doc, aliases=aliases, periods=periods, products=products
            )
            row = self.db.get(SourceDocumentORM, src.source_id)
            if row:
                row.parsing_status = doc.parsing_status.value
                row.page_or_section = doc.page_or_section
                row.metadata_json = {**(row.metadata_json or {}),
                                     "coverage": verdict.as_metadata()}
                if doc.notes:
                    row.notes = (row.notes or "") + f" | parse: {doc.notes}"
        self.db.commit()
        return parsed_map

    async def _label_metadata(self, job: DrugJobORM, sources: list, parsed: dict, options: dict) -> None:
        """What the product's own label and application records say about it.

        Runs before the issuer is resolved, because a label names its sponsor
        and the sponsor is what the filing index is asked for. It reads only
        openFDA records; the filings this job has not fetched yet are read by
        `_narrative_metadata`.
        """
        if not options.get("product_metadata", True):
            return
        self._set_step(job, JobStep.EXTRACT_METADATA)

        written: dict[str, str] = {}
        conflicts: dict[str, dict[str, Any]] = {}

        # The approval date and the indications come from two different openFDA
        # records: the drugsFDA application carries `submissions` and no
        # indication prose, the SPL label carries the prose and no submissions.
        # So the date is held across the source loop and written onto the
        # indication rows afterwards, whichever order the two records arrive in.
        openfda_approval: str | None = None
        openfda_identity: ResolvedProductIdentity | None = None
        anchored_indications: list[ProductIndicationORM] = []
        openfda_products: list[CanonicalProductORM] = []

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
            matches = brand_matched_results(
                results,
                product=job.drug_name,
                generic=job.generic_name,
                aliases=self._job_aliases,
            )
            selected, matched_brand = earliest_approved_match(matches)
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
            # A brand with more than one application has more than one
            # approval, and the earliest ORIG is the one the product launched
            # on; openFDA documents no order, so the first result is not it.
            approval, approval_field = earliest_approval_date([r for r, _ in matches])
            openfda_approval = openfda_approval or approval
            application_numbers = [
                str(r.get("application_number")) for r, _ in matches if r.get("application_number")
            ]
            # The indication readings are the label's, and `profile_fields`
            # has the label: a value computed from it here can only be the
            # same value. `parsed_indications` stays because the indication
            # rows below are written from it.
            mapping = profile_fields(
                selected,
                first_label,
                moa_value=moa_value,
                approval=approval,
                approval_path=approval_field,
            )
            for field, sourced in mapping.items():
                value = sourced.value
                if is_missing_value(value) or not sourced.path:
                    continue
                if field in written:
                    # Two openFDA records disagreeing is the judge's question,
                    # not something to settle by which source arrived first.
                    if values_conflict(written[field], value):
                        conflicts[field] = {
                            "value": str(value),
                            "source_type": SourceType.OPENFDA.value,
                            "source_url": src.url,
                            "source_quote": sourced.quote or sourced.path,
                            "source_field": sourced.path,
                        }
                    continue
                if field in SIBLING_SENSITIVE_FIELDS and blends_sibling_brand(
                    value,
                    product=job.drug_name,
                    aliases=self._job_aliases,
                    source_quote=sourced.quote,
                ):
                    job.quality_flags = list(
                        set((job.quality_flags or []) + [f"sibling_blend_skipped:{field}"])
                    )
                    continue
                written[field] = str(value)
                citation = {
                    "source_id": src.source_id,
                    "source_type": SourceType.OPENFDA.value,
                    "source_url": src.url,
                    "source_title": src.title,
                    # The key the value was read from, and the document's own
                    # words where it came from prose. `openfda.<field>` was
                    # neither: for most of these there is no such key.
                    "source_field": sourced.path,
                    "source_quote": sourced.quote or sourced.path,
                    "retrieval_date": utc_now().isoformat(),
                    "confidence": 0.9 if field == "fda_approval_date" else 0.85,
                    "validation_status": ValidationStatus.NEEDS_REVIEW.value,
                    "interpreted": False,
                    "openfda_application_number": selected.get("application_number"),
                    "openfda_matched_brand": matched_brand,
                }
                if len(matches) > 1:
                    citation["openfda_matched_applications"] = application_numbers
                if sourced.rival:
                    citation["conflicting_source"] = {
                        "value": "; ".join(
                            value
                            for values in sourced.rival["readings"].values()
                            for value in values
                        ),
                        "source_type": SourceType.OPENFDA.value,
                        "source_url": src.url,
                        "source_field": "; ".join(sourced.rival["readings"]),
                        "source_quote": "; ".join(sourced.rival["readings"]),
                    }
                    job.quality_flags = list(
                        set((job.quality_flags or []) + [f"openfda_conflicting_reading:{field}"])
                    )
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
                # One job is one product, so its identity is resolved from the
                # first openFDA record that names a brand and reused after
                # that. The two records answering for a product do not describe
                # it equally: the SPL label states no formulation at all, so a
                # second resolution from it keys the same product on a poorer
                # reading and files it as a second canonical row.
                identity = openfda_identity or resolve_product_identity(
                    brand_name=first_label.brand_names[0],
                    active_ingredients=first_label.active_ingredients,
                    dosage_form=first_label.dosage_forms[0] if first_label.dosage_forms else None,
                    route_terms=first_label.routes,
                )
                openfda_identity = identity
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
                # The launch anchor every curve is drawn from. The dashboard
                # reads this column in preference to the profile field, so a
                # canonical row with the column unset costs the approval date
                # exactly where resolution succeeded.
                openfda_products.append(product)
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
                        anchored_indications.append(existing_indication)
                        continue
                    row = ProductIndicationORM(
                        id=new_id(),
                        product_id=product.id,
                        disease=indication.disease,
                        therapeutic_area=therapeutic_area(indication.disease),
                        setting=indication.setting,
                        population=indication.population,
                        biomarker=indication.biomarker,
                        approved_lot=indication.approved_lot.value.value,
                        approved_lot_quote=indication.source_quote,
                    )
                    self.db.add(row)
                    anchored_indications.append(row)
                for field, sourced in mapping.items():
                    if sourced.value and sourced.path:
                        self.db.add(
                            EvidenceAssertionORM(
                                id=new_id(),
                                entity_type="product",
                                entity_id=product.id,
                                field_name=field,
                                value_json={"value": sourced.value},
                                source_id=src.source_id,
                                source_url=src.url,
                                source_section=sourced.path,
                                source_quote=sourced.quote or sourced.path,
                                confidence=0.9,
                                validation_status=ValidationStatus.NEEDS_REVIEW.value,
                                extraction_method="structured_fda",
                                selected=True,
                            )
                        )

        # The approval belongs to the application record and the indications to
        # the label record, so the anchor is written after both have been read
        # rather than from whichever record was in hand when a row was built.
        if openfda_approval:
            anchor = datetime.fromisoformat(openfda_approval).date()
            for product in openfda_products:
                if not product.initial_approval_date:
                    product.initial_approval_date = anchor
            for row in anchored_indications:
                if not row.approval_date:
                    row.approval_date = anchor
                    row.launch_anchor_type = "indication_approval_date"

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
            "label_metadata_extracted job_id=%s drug=%s fields=%s conflicts=%s",
            job.id,
            job.drug_name,
            sorted(written),
            sorted(conflicts),
        )

    async def _narrative_metadata(self, job: DrugJobORM, sources: list, parsed: dict, options: dict) -> None:
        """What the first readable filing says about the product.

        Runs after the filings are fetched, because that is when there is a
        narrative source to read at all. What the label pass already wrote is
        read back from the job's own profile rows rather than carried between
        the two, so a field either pass filled is one this pass disagrees with
        rather than overwrites.
        """
        if not options.get("product_metadata", True):
            return
        self._set_step(job, JobStep.EXTRACT_METADATA)

        written: dict[str, str] = {
            row.field: row.value
            for row in self.db.query(DrugProfileFieldORM).filter_by(job_id=job.id).all()
            if row.field and row.value
        }
        conflicts: dict[str, dict[str, Any]] = {}

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
                    "retrieval_date": utc_now().isoformat(),
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
            "narrative_metadata_extracted job_id=%s drug=%s fields=%s conflicts=%s",
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

    def _quarters_no_filing_covers(
        self, job: DrugJobORM, sources: list, options: dict[str, Any], rows: list[DatapointORM]
    ) -> list[str]:
        """The window's quarters, when the given issuer filed nothing in it.

        A product that changed hands - a filer with no SEC filings, then an
        acquirer whose first release covers a stub - has quarters no filing
        of the named issuer reports. The retrieval stage cannot say so: it
        finds nothing and moves on. When no successful SEC filing or release
        of this issuer is dated inside the window, every quarter the window
        would have reported, and nothing answered, is such a quarter.
        """
        since = parse_filing_date(options.get("earnings_since"))
        until = parse_filing_date(options.get("earnings_until"))
        window = quarters_reported_in(since, until)
        if not window:
            return []
        covered = any(
            s.source_type in {SourceType.SEC_FILING, SourceType.EARNINGS_RELEASE}
            and s.retrieval_status == RetrievalStatus.SUCCESS
            and s.source_date is not None
            and (since is None or s.source_date >= since)
            and (until is None or s.source_date <= until)
            for s in sources
        )
        if covered:
            return []
        answered = {row.period for row in rows}
        return [quarter for quarter in window if quarter not in answered]

    async def _search_quarters_fallback(
        self, job: DrugJobORM, quarters: list[str]
    ) -> tuple[list, dict[str, Any]]:
        """Ask the search who reported these quarters for this product.

        The issuer named on the job filed nothing for them, so the query
        names the quarters and the product and lets the filer be found: an
        acquirer's historical schedule, a predecessor's own release.
        """
        search_sources = await self.search.fallback_retrieve(
            run_id=job.run_id,
            job_id=job.id,
            goal="quarters",
            product=job.drug_name,
            aliases=self._job_aliases,
            manufacturer=job.manufacturer,
            ticker=job.ticker,
            context=(
                f"Product-level net sales for {', '.join(quarters)}. "
                f"{job.manufacturer or 'The named issuer'} filed nothing with the SEC "
                f"in this window, so find who reported the product then: a predecessor "
                f"or acquirer's earnings release, historical sales schedule, or IR document."
            ),
        )
        if not search_sources:
            return [], {}
        self._persist_sources(job, search_sources)
        job.sources_found = (job.sources_found or 0) + len(search_sources)
        job.quality_flags = list(set((job.quality_flags or []) + ["llm_search_quarters_fallback"]))
        self.db.commit()
        parsed = await self._parse(job, search_sources)
        return search_sources, parsed

    def _record_quarters_only_reported_with_another_product(
        self, job: DrugJobORM, rows: list[DatapointORM]
    ) -> None:
        """Say which quarters the issuer reports only as part of a pair.

        Where every figure for a quarter is a line covering this product and
        another, the pair's figure is published under the pair's name and the
        product's own is not a number anybody discloses. Left unsaid, the
        quarter reads as a gap the pipeline failed to fill, and a reviewer
        goes looking for a figure that does not exist.
        """
        by_period: dict[str, list[DatapointORM]] = {}
        for row in rows:
            if row.period and row.period_type == PeriodType.QUARTERLY.value:
                by_period.setdefault(row.period, []).append(row)
        recorded = {
            u.period
            for u in self.db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        }
        added = 0
        for period, figures in sorted(by_period.items()):
            if period in recorded or not all(f.reported_as for f in figures):
                continue
            names = sorted({f.reported_as for f in figures if f.reported_as})
            self.db.add(
                UnresolvedQuarterORM(
                    id=new_id(),
                    job_id=job.id,
                    period=period,
                    reason_unresolved=(
                        f"[{REPORTED_WITH_ANOTHER_PRODUCT}] {job.drug_name} is reported only as "
                        f"{', '.join(names)}; that figure is published under this quarter, and no "
                        f"figure for {job.drug_name} alone is disclosed"
                    ),
                    sources_checked=sorted({f.source_url for f in figures if f.source_url}),
                    recommended_next_step=(
                        "Use the combined figure, or supply a source that reports this product "
                        "on its own"
                    ),
                    confidence_that_unavailable=0.8,
                )
            )
            added += 1
        if added:
            self.db.commit()
            logger.info(
                "reported_with_another_product job_id=%s drug=%s quarters=%d",
                job.id, job.drug_name, added,
            )

    def _record_unfiled_quarters(
        self, job: DrugJobORM, quarters: list[str], rows: list[DatapointORM], searched: list
    ) -> None:
        """Say plainly which quarters have no filer of record.

        Left to the completeness stage these would read as gaps to fill from
        a filing not yet retrieved. There is no such filing: the reason is
        recorded as its own kind, so a reviewer is asked who the filer was,
        not to look again.
        """
        answered = {row.period for row in rows}
        for quarter in quarters:
            if quarter in answered:
                continue
            self.db.add(
                UnresolvedQuarterORM(
                    id=new_id(),
                    job_id=job.id,
                    period=quarter,
                    reason_unresolved=(
                        f"[{NO_FILER_OF_RECORD}] No SEC filer of record for {job.drug_name} in this "
                        f"window: {job.manufacturer or 'the named issuer'} filed nothing in it, and "
                        f"{len(searched)} document(s) found by search stated no figure"
                    ),
                    sources_checked=[s.url for s in searched],
                    recommended_next_step=(
                        "Name the issuer that reported this quarter - the product may have "
                        "changed hands - or supply that issuer's own release"
                    ),
                    confidence_that_unavailable=0.6,
                )
            )
        if quarters:
            self.db.commit()

    @staticmethod
    def _candidate_of(row: DatapointORM) -> dict:
        """A stored datapoint read back as the candidate it came from.

        Every column that says what the figure is a figure for, and where it
        was read. A derivation subtracts these rows, and what it does not
        receive it cannot pass on: the eight keys this used to return dropped
        the pair a combined line was reported as, the region it covered, the
        route, the precision its source declared, and the document it came
        out of - so the derived quarter asserted the family's identity, cited
        whichever document happened to be first, and was published as exact.
        """
        citation = row.citation_json or {}
        return {
            "period": row.period,
            "period_type": row.period_type,
            "value_reported": row.value_reported,
            "value_normalized_usd_millions": row.value_normalized_usd_millions,
            "currency": row.currency,
            "unit": row.unit,
            "source_quote": row.source_quote,
            "revenue_scope": row.revenue_scope,
            "geography": row.geography,
            "formulation": row.formulation,
            "route_of_administration": row.route_of_administration,
            "reported_as": row.reported_as,
            "combined_with": list(citation.get("combined_with") or []),
            "label_flags": [f for f in (row.issue_flags or []) if f in LABEL_FLAGS],
            "extraction_method": row.extraction_method,
            "rounding_uncertainty_usd_millions": citation.get(
                "rounding_uncertainty_usd_millions"
            ),
            "source_id": row.source_id,
            "source_url": row.source_url,
            "_datapoint_id": row.id,
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
            # What the figures it was computed from were figures for. A
            # derivation is a claim about the same product, the same region and
            # the same line of the schedule as the total it subtracted, and
            # defaulting these published a region's arithmetic as the family's
            # and a two-product line as one product's own.
            reported_as=(
                candidate.get("reported_as")
                or reported_as_for(job.drug_name, candidate)
            ),
            geography=candidate.get("geography"),
            route_of_administration=candidate.get("route_of_administration"),
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
                # Every figure this was computed from, with the document each
                # was read from. The quote states the arithmetic; this states
                # where a person goes to check each term of it, which is not
                # the one document the row cites.
                "derived_from": candidate.get("_inputs") or None,
                "combined_with": list(candidate.get("combined_with") or []) or None,
                "validation_status": ValidationStatus.PENDING.value,
                "interpreted": False,
                "period_reported": period,
            },
            issue_flags=(
                (["derived_from_reported_series"] if derived else ["extracted_from_xbrl"])
                + stated_labels(candidate.get("label_flags"))
            ),
        )
        stamp_series_identity(job, row)
        self.db.add(row)
        return row

    def _record_derivation_lineage(
        self, job: DrugJobORM, row: DatapointORM, candidate: dict[str, Any]
    ) -> None:
        """Write what this derived row was computed from, as rows not as prose.

        The quote says the arithmetic in a sentence and the citation repeats it
        as fields; neither can be joined against. `DerivationLineageORM` is the
        join, and it is written over `EvidenceAssertionORM`, which is the table
        it points at: one assertion per figure, the derived one and each input,
        so an input that several derivations used is one row that all of them
        name.

        An input that never became a stored datapoint - a period total, kept
        aside because it is not an answer to a quarterly question - still gets
        an assertion, keyed on the source it was read from and the value it
        carried. What it cannot get is an entity that outlives this run, which
        is why the entity id falls back to the row's own.
        """
        inputs = candidate.get("_inputs") or []
        if not inputs:
            return

        def assertion(
            entity_id: str, value: Any, url: str | None, source_id: str | None,
            method: str, quote: str | None, section: str,
        ) -> EvidenceAssertionORM:
            record = EvidenceAssertionORM(
                id=new_id(),
                entity_type="datapoint",
                entity_id=entity_id,
                field_name="value_normalized_usd_millions",
                value_json={"value": value},
                source_id=source_id,
                source_url=url or "",
                source_section=section,
                source_quote=quote,
                confidence=float(candidate.get("confidence") or 0.0),
                validation_status=ValidationStatus.PENDING.value,
                extraction_method=method,
                selected=True,
            )
            self.db.add(record)
            return record

        output = assertion(
            row.id, row.value_normalized_usd_millions, row.source_url, row.source_id,
            str(row.extraction_method or ""), row.source_quote, f"{job.drug_name} {row.period}",
        )
        for term in inputs:
            source = assertion(
                str(term.get("datapoint_id") or row.id),
                term.get("value_normalized_usd_millions"),
                term.get("source_url"),
                term.get("source_id"),
                str(term.get("extraction_method") or ""),
                None,
                f"{job.drug_name} {term.get('period')} {term.get('period_type')}",
            )
            self.db.add(
                DerivationLineageORM(
                    id=new_id(),
                    output_assertion_id=output.id,
                    input_assertion_id=source.id,
                    role=str(term.get("role") or "input"),
                    formula_version=str(row.extraction_method or ""),
                )
            )

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
                    totals.append(_read_from(candidate, src))
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
                # derived against. It is stamped with the document it was read
                # from on the way out: it never becomes a row of its own, so
                # nothing downstream can look its source up, and a derivation
                # that subtracts it has to say where it came from.
                if candidate.get("period_type") != PeriodType.QUARTERLY.value:
                    totals.append(_read_from(candidate, src))
                    continue
                rows.append(self._datapoint_from_candidate(job, src, candidate))
        if learned:
            written = member_store.record_many(self.db, learned, products=products)
            # The savepoint inside record_many flushes everything pending, the
            # rows above included, which makes this session a writer; the
            # caller goes on to await the model for other sources, and a write
            # held open across that stalls every other job. So it is committed
            # here, where it was opened.
            self.db.commit()
            logger.info("xbrl_members_learned job_id=%s members=%d", job.id, written)
        if rows or totals:
            logger.info(
                "xbrl_facts job_id=%s drug=%s quarters=%d totals=%d",
                job.id, job.drug_name, len(rows), len(totals),
            )
        return rows, totals

    def _model_budget(
        self,
        job: DrugJobORM,
        sources: list,
        prepared: dict[str, dict[str, Any]],
    ) -> set[str]:
        """Which sources the model is asked about, by what each one holds.

        Eligible is a source the deterministic readers did not answer and
        that carries product-and-dollar evidence. Among those, first the ones
        whose product tables were found and not read - the reader recorded
        each with a reason - then the ones with the most product excerpts,
        then the fitter document type, then the newer filing. The budget is
        the configured count of sources; what changes is which ones.
        """
        aliases = self._job_aliases or None

        def unread_product_table(state: dict[str, Any]) -> bool:
            return any(
                quote_mentions_product(skip, job.drug_name, job.generic_name, extra_aliases=aliases)
                for skip in state.get("table_skips") or []
            )

        eligible = [
            src for src in sources
            if (state := prepared.get(src.source_id)) is not None
            and not state["deterministic_answered"]
            and not state["no_product_evidence"]
        ]
        eligible.sort(key=lambda src: (
            0 if unread_product_table(prepared[src.source_id]) else 1,
            -int((prepared[src.source_id]["evidence_meta"] or {}).get("window_count") or 0),
            reading_rank(src.source_type),
            -(src.source_date.toordinal() if src.source_date else 0),
        ))
        return {src.source_id for src in eligible[: get_settings().llm_max_extract_sources]}

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

        # Two passes. The deterministic readers read every source first, and
        # what they leave - the product tables they could not read, the
        # product-and-dollar excerpts they found nothing in - is what decides
        # which sources the model is asked about. Choosing the model's sources
        # by document type and date, before anyone had looked inside them,
        # spent the budget on the newest 8-Ks and left the 10-Qs with the
        # product tables unread by both.
        prepared: dict[str, dict[str, Any]] = {}
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
                footnotes=doc.table_footnotes,
                # What each table's own tagged figures declare their scale to
                # be, for a schedule whose caption states no unit in words.
                units=doc.table_units,
                # The other products this run knows, so a label naming two of
                # them reads as a combined line rather than as unknown words.
                products=self._candidate_products(job),
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
                _read_from(candidate, src)
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
            # The other brands this document gives a row of their own. The
            # filer's schedule is its own product list, so what could be
            # confused with ours is read off the document rather than held in
            # a catalogue of brands here.
            peers = peer_product_names(
                sibling_row_labels(
                    doc.tables, product=job.drug_name,
                    generic=job.generic_name, extra_aliases=extra,
                ),
                job.drug_name, job.generic_name, extra,
            )
            table_rows, table_dropped = filter_revenue_candidates(
                fingerprinted,
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
                peer_names=peers,
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
            prepared[src.source_id] = {
                "period_context": period_context,
                "table_rows": table_rows,
                "table_findings": table_findings,
                "table_skips": table_skips,
                "answered": answered,
                "kept": kept,
                "llm_text": llm_text,
                "evidence_meta": evidence_meta,
                "no_product_evidence": no_product_evidence,
                "deterministic_answered": deterministic_answered,
                "peers": peers,
            }

        llm_source_ids = self._model_budget(job, selected_sources, prepared)

        for src in selected_sources:
            state = prepared.get(src.source_id)
            if state is None:
                continue
            period_context = state["period_context"]
            table_rows, table_findings = state["table_rows"], state["table_findings"]
            answered, kept = state["answered"], state["kept"]
            llm_text, evidence_meta = state["llm_text"], state["evidence_meta"]
            no_product_evidence = state["no_product_evidence"]
            deterministic_answered = state["deterministic_answered"]
            # This document's own sibling rows, not the last document prepared.
            peers = state["peers"]
            use_llm = src.source_id in llm_source_ids
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
                (s.get("span_text") or "") for s in listed(result, "spans")
            ) or llm_text
            llm_dropped = result.get("dropped") or []
            llm_kept, dropped = filter_revenue_candidates(
                listed(result, "candidates"),
                product=job.drug_name,
                generic=job.generic_name,
                extra_aliases=extra,
                source_text=span_corpus,
                peer_names=peers,
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
                quote = stated_text(cand.get("source_quote"))
                url = src.url
                period_type = stated_text(cand.get("period_type"), "unknown").lower()
                raw_period = str(cand.get("period") or "unknown")
                period = normalize_period(
                    raw_period, period_type=period_type, context=period_context
                )
                dp_id = new_id()
                # The figure and the figure the candidate normalized itself,
                # each read as a number so that a candidate quoting one as text
                # still carries it and one holding an array carries nothing.
                value = stated_number(cand.get("value_reported"))
                unit = stated_text(cand.get("unit")) or None
                currency = stated_text(cand.get("currency"), "USD")
                normalized = stated_number(cand.get("value_normalized_usd_millions"))
                if normalized is None and value is not None:
                    normalized = scale_to_millions(value, unit)
                # A candidate may supply its own normalization, and it was
                # taken verbatim. Nothing downstream looks at it: every check
                # reads `value_reported`, the judge included, so a candidate
                # whose own normalized figure is a thousand times its reported
                # one is confirmed against its quote and published, and the
                # consumer reads the figure nobody checked.
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
                    "retrieval_date": utc_now().isoformat(),
                    "filing_type": src.filing_type,
                    "accession_number": src.accession_number,
                    "confidence": stated_number(cand.get("confidence")) or 0.5,
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
                # What the row label said beyond the name: a combined line, a
                # partial period, words nobody could account for. The flags
                # decide what the judge may do with the figure.
                for flag in stated_labels(cand.get("label_flags")):
                    if flag not in issue_flags:
                        issue_flags.append(flag)
                if cand.get("label_residue"):
                    citation["label_residue"] = cand["label_residue"]
                if cand.get("combined_with"):
                    citation["combined_with"] = list(cand["combined_with"])
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
                    reported_as=reported_as_for(job.drug_name, cand),
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
                stamp_series_identity(job, row)
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
        # is enough to make a quarter non-missing and stop the derivation being
        # computed at all. The weak reading is not discarded; it is simply not
        # evidence about what still needs deriving.
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
        # anything had been found for did not make the weak reading win the
        # ranking; it stopped the derivation existing to be ranked against.
        # Where a weaker reader answered, both now stand and reconciliation
        # ranks them.
        strongest: dict[str, int] = {}
        for row in rows:
            rank = claim_rank(row.extraction_method)
            if rank < strongest.get(row.period, len(CLAIM_STRENGTH) + 1):
                strongest[row.period] = rank
        # The document each figure was actually read from. A derivation cites
        # the filing its principal input came out of, so a reader opening the
        # citation finds the total that was subtracted; the other inputs are
        # named in the citation with their own documents. Citing whichever
        # source sorted first meant none of the figures in a derived quote
        # appeared in the document cited for it.
        by_source_id = {s.source_id: s for s in sources}
        for candidate in derived:
            if strongest.get(str(candidate["period"]), len(CLAIM_STRENGTH) + 1) <= derived_rank:
                continue
            source = by_source_id.get(candidate.get("source_id")) or next(
                (s for s in selected_sources), None
            )
            if source is None:
                break
            row = self._datapoint_from_candidate(job, source, candidate)
            self._record_derivation_lineage(job, row, candidate)
            rows.append(row)
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
            label_flags = [f for f in (row.issue_flags or []) if f in LABEL_FLAGS]
            residue = (row.citation_json or {}).get("label_residue") or ""
            notes = footnotes_in(row.source_quote or "")
            # What the row already says about itself. Every one of these is a
            # column the pipeline filled and the judge was not shown, so it was
            # asked whether a quote supports a figure without being told what
            # the figure is denominated in, where it was sold, or what read it.
            candidate = {
                "period": row.period,
                "value_reported": row.value_reported,
                "unit": row.unit,
                "currency": row.currency,
                "period_type": row.period_type,
                "revenue_scope": row.revenue_scope,
                "geography": row.geography,
                "formulation": row.formulation,
                "extraction_method": row.extraction_method,
                "source_type": (row.citation_json or {}).get("source_type"),
                "label_flags": label_flags,
                "label_residue": residue,
            }
            if notes:
                # The filer's own footnote about this figure, which the table
                # reader carried out on the end of the quote.
                candidate["footnote"] = " ".join(notes)
            doc = parsed.get(row.source_id or "")
            peers = peer_product_names(
                sibling_row_labels(
                    getattr(doc, "tables", None), product=job.drug_name,
                    generic=job.generic_name, extra_aliases=aliases,
                ),
                job.drug_name, job.generic_name, aliases,
            )
            context = row.source_quote or ""
            if residue:
                # The judge is shown what the label said that the reader could
                # not account for, which is the question it is being asked.
                context = f"Row label words not accounted for: {residue}\n\n{context}"
            judgment = None
            if settings.llm_skip_judge_when_deterministic:
                judgment = try_deterministic_judgment(
                    product=job.drug_name,
                    generic=job.generic_name,
                    candidate=candidate,
                    quote=row.source_quote or "",
                    extra_aliases=aliases,
                    peer_names=peers,
                )
            if judgment is None:
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
                    peer_names=peers,
                )

            support = judgment.get("support_classification")
            status = judgment.get("validation_status") or "needs_review"
            if label_flags and status == ValidationStatus.AUTO_PASS.value:
                # A label the reader could not account for, or a footnote that
                # made the figure a partial period, is a question for a person.
                # The judge's reading is kept; the decision is not automated.
                status = ValidationStatus.NEEDS_REVIEW.value
                judgment = {**judgment, "validation_status": status,
                            "issues": [*(judgment.get("issues") or []),
                                       f"label:{','.join(label_flags)}"]}
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
                    peer_names=peers,
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
            # that any fill forces needs_review and caps confidence at
            # `ENRICHMENT_CONFIDENCE_CAP` (`quality/enrichment.py`).
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
                    "period_type": row.period_type,
                    "revenue_scope": row.revenue_scope,
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
                    row.period_type = enriched.get("period_type") or row.period_type
                    row.revenue_scope = enriched.get("revenue_scope") or row.revenue_scope
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
                # The same flags that held this row above. "Supported" is an
                # answer about the quote: the figure is in the text cited for
                # it. A label flag is a question about what the figure is a
                # figure for - which product's line it was read from, what
                # part of the quarter it covers - and a quote cannot settle
                # that, so a row demoted for one was promoted straight back
                # here and published as this product's own quarter.
                and not label_flags
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
            # The judge answers with both: codes its own vetoes raised, and the
            # model's sentences about the row. Only the codes belong in the
            # column readers match against; the sentences are what a person
            # reads, and that is `reviewer_notes`.
            issues = list(judgment.get("issues") or [])
            codes = [issue for issue in issues if _is_a_code(issue)]
            said = [issue for issue in issues if not _is_a_code(issue)]
            if row.issue_flags:
                codes = list(set(list(row.issue_flags) + codes))
            row.issue_flags = codes
            if said:
                row.reviewer_notes = "\n".join([*filter(None, [row.reviewer_notes]), *said])
            if row.citation_json:
                row.citation_json = {**row.citation_json, "validation_status": status}
        self.db.commit()
        await self._reconcile_with_llm(job, rows)

    async def _reconcile_with_llm(self, job: DrugJobORM, rows: list[DatapointORM]) -> None:
        self._set_step(job, JobStep.RECONCILE_CONFLICTS)
        if len(rows) < 2:
            self.db.commit()
            return

        # Deterministic grouping first. "Worldwide" and "Product family" are
        # both the whole product - a sentence says the first, a schedule the
        # second - and grouped apart they were published twice for one quarter.
        #
        # The period label alone is not the period. A nine-month figure and the
        # quarter that ends it carry the same label, and they are two different
        # claims about two different spans: pooled under one key the longer one
        # contests the shorter, the reconciliation calls the disagreement a
        # conflict, and the figure that is right for the quarter is held. The
        # span is part of what a group is a group of.
        by_key: dict[tuple, list[DatapointORM]] = {}
        for row in rows:
            key = (
                row.period,
                row.period_type or "",
                _scope_key(row.revenue_scope),
                row.formulation or "",
            )
            by_key.setdefault(key, []).append(row)
        claim_tier, reports_own_period, accession_of = claim_ranking(self.db, job)

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
            # A verdict names a candidate. The reply is free text, and one that
            # names anything else - the figure where the id belongs, an id for
            # a group it was not shown - is not a verdict on any group here.
            # The candidates are the ones just sent, so the names that may be
            # admitted come from the payload rather than from a second list.
            offered = {str(candidate["id"]) for candidate in conflict_payload}

            def named(value: Any) -> str | None:
                """The candidate a reply names, or None if it names a non-candidate."""
                ident = str(value) if value is not None else ""
                return ident if ident in offered else None

            for item in mappings(result, "resolved"):
                wid = named(item.get("winner_id"))
                if wid:
                    winners.add(wid)
            for item in mappings(result, "conflicts"):
                ids = [cid for cid in map(named, item.get("candidate_ids") or []) if cid]
                wid = named(item.get("winner_id"))
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
            # The claim, then the document, then confidence. Source type alone
            # leaves a sentence and a schedule from one exhibit tied, and the
            # tie was broken by whichever happened to be extracted first.
            group.sort(key=lambda r: (*claim_tier(r), -float(r.confidence_score or 0)))
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
            top = min(claim_tier(r) for r in group)
            strongest = [r for r in group if claim_tier(r) == top
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

        # A filing that contradicts itself publishes nothing for the period.
        # A fact the filer tagged and a figure the same filing prints, for
        # one period, disagreeing beyond what the two declared: neither is
        # the answer, whatever tier each sits on, since the filing is the
        # only witness and it has said two things. Both are held, and the
        # flag names the filing rather than the stronger claim.
        self_contradicting: set[str] = set()
        for group in by_key.values():
            by_accession: dict[str, list[DatapointORM]] = {}
            for r in group:
                accession = accession_of(r)
                if accession and r.value_normalized_usd_millions is not None:
                    by_accession.setdefault(accession, []).append(r)
            for claims in by_accession.values():
                for index, first in enumerate(claims):
                    for second in claims[index + 1 :]:
                        if not _agrees_within_declared_precision(first, second):
                            self_contradicting.update({first.id, second.id})
        winners -= self_contradicting
        losers |= self_contradicting

        # One figure, one publication. A claim that lost only on tier and
        # agrees with the winner within the coarser of the two declared
        # precisions is the same figure read twice - a tagged fact in
        # thousands beside a printed one in millions with one decimal. It is
        # cited beside the published figure, not published as a second one and
        # not held as a conflict it never was.
        #
        # The group is looked at again here rather than read off the winner,
        # because the winner is not always the row that publishes. Which side
        # of a pair carries the figure is settled above; whether that row is
        # publishable was settled by the judge before this ran, and a row held
        # by a veto takes the whole cell down with it when the only other
        # reading of the quarter is turned into its citation.
        corroborating: set[str] = set()
        by_id = {row.id: row for row in rows}
        for group in by_key.values():
            winner = next((r for r in group if r.id in winners), None)
            if winner is None or winner.value_normalized_usd_millions is None:
                continue
            agreeing = [
                other
                for other in group
                if other.id != winner.id
                and other.id not in contested
                and other.id not in self_contradicting
                and other.value_normalized_usd_millions is not None
                and _agrees_within_declared_precision(winner, other)
            ]
            replaced = False
            if not _publishes(winner):
                # The winner is held. A reading of the same figure that is
                # publishable and carries no question of its own is the
                # quarter's answer, and citing it under a row nobody will
                # publish loses the quarter. It takes the winner's place; the
                # held row keeps the verdict it was given, and is not recorded
                # as corroborating a figure it lost to.
                #
                # Which of them takes the place is the same question the group
                # was ranked on, so it is asked with the same key rather than
                # by whichever the group happens to list first.
                instead = next(
                    (
                        o
                        for o in sorted(agreeing, key=claim_tier)
                        if _publishes(o) and _unquestioned(o)
                    ),
                    None,
                )
                if instead is not None:
                    replaced = True
                    winners.discard(winner.id)
                    losers.discard(instead.id)
                    winners.add(instead.id)
                    logger.info(
                        "corroborator_published job_id=%s drug=%s period=%s held=%s published=%s",
                        job.id, job.drug_name, winner.period,
                        winner.extraction_method, instead.extraction_method,
                    )
                    agreeing = [o for o in agreeing if o.id != instead.id]
                    winner = instead
            for other in agreeing:
                corroborating.add(other.id)
                cited = list((winner.citation_json or {}).get("corroborated_by") or [])
                cited.append({
                    "datapoint_id": other.id,
                    "source_url": other.source_url,
                    "value_normalized_usd_millions": other.value_normalized_usd_millions,
                    "extraction_method": other.extraction_method,
                })
                winner.citation_json = {**(winner.citation_json or {}), "corroborated_by": cited}
                if not replaced:
                    # The row that took a held winner's place was chosen
                    # because it publishes and carries no question of its own.
                    # A reading the pipeline refuses to publish may not turn it
                    # back into one: that withdraws the only answer the quarter
                    # has, and leaves the cell as empty as the promotion was
                    # there to stop it being.
                    _carry_to_winner(winner, other)

        for row in rows:
            if row.id in corroborating:
                row.validation_status = ValidationStatus.CORROBORATES.value
                row.issue_flags = list(set((row.issue_flags or []) + ["corroborates_published_figure"]))
                if row.citation_json:
                    row.citation_json = {**row.citation_json, "validation_status": row.validation_status}
            elif row.id in losers:
                row.validation_status = ValidationStatus.NEEDS_REVIEW.value
                if row.id in self_contradicting:
                    flag = "filing_contradicts_itself"
                elif row.id in contested:
                    flag = "equal_strength_claims_disagree"
                elif reports_own_period(row) == 2 and any(
                    reports_own_period(by_id[w]) == 0 for w in winners
                    if by_id[w].period == row.period
                ):
                    flag = "restated_in_later_filing"
                else:
                    flag = FLAG_CONFLICT_WITH_HIGHER_PRIORITY
                row.issue_flags = list(set((row.issue_flags or []) + [flag]))
                if row.citation_json:
                    row.citation_json = {**row.citation_json, "validation_status": row.validation_status}
        self.db.commit()

    async def _quality_and_validation(self, job: DrugJobORM) -> None:
        self._set_step(job, JobStep.QUALITY_CHECKS)
        dps = self.db.query(DatapointORM).filter_by(job_id=job.id).all()
        profile_fields = self.db.query(DrugProfileFieldORM).filter_by(job_id=job.id).all()
        profile = {f.field: f.value for f in profile_fields}
        # The labels are final by now - the enricher has read what it could
        # off the quotes, and reconciliation has carried a corroborator's line
        # onto the row it corroborates - so this is where a row's series is
        # settled, and the duplicate check keys on it.
        for d in dps:
            stamp_series_identity(job, d)
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
                "geography_normalized": d.geography_normalized,
                "series_identity": d.series_identity,
                "currency": d.currency,
                "unit": d.unit,
                "confidence_score": d.confidence_score,
                "validation_status": d.validation_status,
            }
            for d in dps
        ]
        issues = run_quality_checks(dp_dicts, profile)
        checks: list[tuple[QualityIssue, QualityCheckORM]] = []
        for issue in issues:
            check = QualityCheckORM(
                id=new_id(),
                job_id=job.id,
                issue_type=issue.issue_type,
                severity=issue.severity,
                affected_datapoint=issue.affected_datapoint,
                explanation=issue.explanation,
                recommended_action=issue.recommended_action,
                status=issue.status,
            )
            checks.append((issue, check))
            self.db.add(check)
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

        select_job_series(self.db, job, dps, checks)

        job.auto_pass_count = sum(1 for d in dps if d.validation_status == ValidationStatus.AUTO_PASS.value)
        job.needs_review_count = sum(1 for d in dps if d.validation_status == ValidationStatus.NEEDS_REVIEW.value)
        # Merge, so provenance flags raised earlier in the pipeline survive
        job.quality_flags = sorted(
            set(job.quality_flags or []) | {i.issue_type for i in issues if i.severity == "high"}
        )

        self._set_step(job, JobStep.VALIDATION_TASKS)
        conflict_ids = {
            d.id
            for d in dps
            if FLAG_CONFLICT_WITH_HIGHER_PRIORITY in (d.issue_flags or [])
        }
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
        # A quarter already recorded as unresolved - by the search stage, or a
        # previous pass - is not recorded again as a gap.
        existing |= {
            u.period for u in self.db.query(UnresolvedQuarterORM).filter_by(job_id=job.id).all()
        }

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
                    existing.add(label)
                q += 1
                if q > 4:
                    q = 1
                    y += 1

        # Flushed first: the gaps just added are unresolved quarters too, and
        # the model's list of missing periods must not record them a second
        # time.
        self.db.flush()
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
        for miss in listed(result, "missing_periods"):
            # A missing period arrives either as the label on its own or as an
            # object saying why it is missing. Only the second carries the
            # fields read here, so the branch turns on being an object rather
            # than on being a string: a reply is free to put a number, a null
            # or a nested array in that list, and each of those is a label
            # that no quarter answers to, not a reason to end the job.
            if not isinstance(miss, dict):
                period, code, reason, nxt = miss, "gap", "Missing period", "Review SEC filings"
            else:
                period = miss.get("period")
                code = stated_text(miss.get("reason_code"), "gap").lower()
                default_reason, conf = reason_map.get(code, reason_map["gap"])
                reason = stated_text(miss.get("reason"), default_reason)
                nxt = stated_text(
                    miss.get("recommended_next_step"),
                    "Review SEC 10-Q / earnings for product net sales",
                )
            period = str(period).strip() if period is not None else ""
            if not period or period in existing or period in existing_unresolved:
                continue
            if "Q" not in period:
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

        counted = refresh_completeness(self.db, job)
        self.db.commit()
        logger.info(
            "completeness job_id=%s drug=%s resolved_pct=%s quarterly=%s unresolved=%s",
            job.id,
            job.drug_name,
            job.completeness_pct,
            counted.quarters,
            job.unresolved_count,
        )
