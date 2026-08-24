from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def new_id() -> str:
    return str(uuid4())


class SourceType(str, Enum):
    SEC_FILING = "sec_filing"
    OPENFDA = "openfda"
    COMPANY_IR = "company_ir"
    EARNINGS_RELEASE = "earnings_release"
    INVESTOR_PRESENTATION = "investor_presentation"
    ANNUAL_REPORT = "annual_report"
    QUARTERLY_REPORT = "quarterly_report"
    USER_URL = "user_url"
    TRANSCRIPT = "transcript"
    OTHER = "other"


class RetrievalStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    NOT_CONFIGURED = "not_configured"
    SKIPPED = "skipped"


class ParsingStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    PENDING = "pending"


class ValidationStatus(str, Enum):
    PENDING = "pending"
    AUTO_PASS = "auto_pass"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FOLLOW_UP = "follow_up"
    IMPORTED = "imported"
    UNRESOLVED = "unresolved"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStep(str, Enum):
    QUEUED = "queued"
    IDENTITY_RESOLVE = "identity_resolve"
    SOURCE_RETRIEVE = "source_retrieve"
    PARSE_SOURCES = "parse_sources"
    EXTRACT_METADATA = "extract_metadata"
    EXTRACT_REVENUE = "extract_revenue"
    EVIDENCE_JUDGE = "evidence_judge"
    RECONCILE_CONFLICTS = "reconcile_conflicts"
    QUALITY_CHECKS = "quality_checks"
    COMPLETENESS = "completeness"
    VALIDATION_TASKS = "validation_tasks"
    READY_FOR_REVIEW = "ready_for_review"


class RevenueScope(str, Enum):
    US = "U.S."
    EX_US = "ex-U.S."
    WORLDWIDE = "Worldwide"
    INTERNATIONAL = "International"
    REGIONAL = "Regional"
    FRANCHISE = "Franchise"
    PRODUCT_FAMILY = "Product family"
    FORMULATION_SPECIFIC = "Formulation-specific"
    COMPANY_TOTAL = "Company total"
    UNKNOWN = "Unknown"


class PeriodType(str, Enum):
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    YTD = "ytd"
    SIX_MONTH = "six_month"
    NINE_MONTH = "nine_month"
    CUMULATIVE = "cumulative"
    GUIDANCE = "guidance"
    UNKNOWN = "unknown"


class IssueSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Citation(BaseModel):
    """Mandatory citation for every source-derived field."""

    source_id: str
    source_type: SourceType
    source_url: str
    source_title: str | None = None
    source_quote: str | None = None
    retrieval_date: datetime = Field(default_factory=datetime.utcnow)
    filing_type: str | None = None
    accession_number: str | None = None
    page_or_section: str | None = None
    confidence: float = 0.0
    validation_status: ValidationStatus = ValidationStatus.PENDING
    interpreted: bool = False

    @field_validator("source_url")
    @classmethod
    def url_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_url is required for citations")
        return v.strip()

    @model_validator(mode="after")
    def quote_or_structured(self) -> Citation:
        # OpenFDA structured fields may use source_quote as field path
        if self.source_type != SourceType.OPENFDA and not (self.source_quote and self.source_quote.strip()):
            # Allow pending extraction; quality layer will flag before auto-pass
            pass
        return self


class CitedValue(BaseModel):
    field: str
    value: Any
    citation: Citation


class RetrievedSource(BaseModel):
    source_id: str = Field(default_factory=new_id)
    source_type: SourceType
    url: str
    title: str | None = None
    source_date: date | None = None
    filing_type: str | None = None
    accession_number: str | None = None
    raw_text: str | None = None
    storage_key: str | None = None
    retrieval_status: RetrievalStatus
    metadata: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class ParsedDocument(BaseModel):
    source_id: str
    text_blocks: list[str] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)
    page_or_section: str | None = None
    parsing_status: ParsingStatus
    notes: str | None = None

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.text_blocks)


class RevenueCandidate(BaseModel):
    period: str
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    calendar_year: int | None = None
    calendar_quarter: int | None = None
    value_reported: float | None = None
    value_normalized_usd_millions: float | None = None
    currency: str | None = None
    unit: str | None = None
    metric: str = "revenue"
    period_type: PeriodType = PeriodType.UNKNOWN
    revenue_scope: RevenueScope = RevenueScope.UNKNOWN
    geography: str | None = None
    formulation: str | None = None
    route_of_administration: str | None = None
    source_quote: str
    confidence: float = 0.0
    extraction_method: str = "llm"


class DrugInput(BaseModel):
    drug_name: str
    generic_name: str | None = None
    manufacturer: str | None = None
    ticker: str | None = None
    cik: str | None = None
    indication: str | None = None
    known_source_url: str | None = None


class ExtractionOptions(BaseModel):
    quarterly_revenue: bool = True
    product_metadata: bool = True
    sec_filings: bool = True
    company_ir: bool = True
    openfda: bool = True
    earnings_releases: bool = True
    transcripts: bool = False
    pdfs: bool = True
    llm_evidence_judge: bool = True
    random_validation_sampling: bool = True
    use_uploaded_template: bool = False


class RunCreate(BaseModel):
    drugs: list[DrugInput]
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)
