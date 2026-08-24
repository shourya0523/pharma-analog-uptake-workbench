from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class ExtractionRunORM(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    jobs: Mapped[list[DrugJobORM]] = relationship(back_populates="run", cascade="all, delete-orphan")


class DrugJobORM(Base):
    __tablename__ = "drug_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    drug_name: Mapped[str] = mapped_column(String(256))
    generic_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    indication: Mapped[str | None] = mapped_column(String(512), nullable=True)
    known_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    current_step: Mapped[str] = mapped_column(String(64), default="queued")
    completeness_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sources_found: Mapped[int] = mapped_column(Integer, default=0)
    candidates_extracted: Mapped[int] = mapped_column(Integer, default=0)
    auto_pass_count: Mapped[int] = mapped_column(Integer, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, default=0)
    unresolved_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    run: Mapped[ExtractionRunORM] = relationship(back_populates="jobs")
    profile_fields: Mapped[list[DrugProfileFieldORM]] = relationship(cascade="all, delete-orphan")
    sources: Mapped[list[SourceDocumentORM]] = relationship(cascade="all, delete-orphan")
    datapoints: Mapped[list[DatapointORM]] = relationship(cascade="all, delete-orphan")
    validation_tasks: Mapped[list[ValidationTaskORM]] = relationship(cascade="all, delete-orphan")
    quality_checks: Mapped[list[QualityCheckORM]] = relationship(cascade="all, delete-orphan")
    unresolved_quarters: Mapped[list[UnresolvedQuarterORM]] = relationship(cascade="all, delete-orphan")


class DrugProfileFieldORM(Base):
    __tablename__ = "drug_profile_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("drug_jobs.id"), index=True)
    field: Mapped[str] = mapped_column(String(128))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")


class SourceDocumentORM(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("drug_jobs.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(64))
    source_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filing_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_or_section: Mapped[str | None] = mapped_column(String(256), nullable=True)
    retrieval_status: Mapped[str] = mapped_column(String(32))
    parsing_status: Mapped[str] = mapped_column(String(32), default="pending")
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevant_datapoints_found: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DatapointORM(Base):
    __tablename__ = "datapoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("drug_jobs.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    period: Mapped[str] = mapped_column(String(32))
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calendar_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calendar_quarter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_reported: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_normalized_usd_millions: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric: Mapped[str] = mapped_column(String(64), default="revenue")
    period_type: Mapped[str] = mapped_column(String(32), default="unknown")
    revenue_scope: Mapped[str] = mapped_column(String(64), default="Unknown")
    geography: Mapped[str | None] = mapped_column(String(128), nullable=True)
    formulation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    route_of_administration: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    source_support: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(64), default="llm")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    citation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ValidationTaskORM(Base):
    __tablename__ = "validation_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("drug_jobs.id"), index=True)
    datapoint_id: Mapped[str] = mapped_column(String(36), index=True)
    reason: Mapped[str] = mapped_column(String(256))
    judge_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deterministic_results: Mapped[list[Any]] = mapped_column(JSON, default=list)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    issues: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="open")
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class QualityCheckORM(Base):
    __tablename__ = "quality_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("drug_jobs.id"), index=True)
    issue_type: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16))
    affected_datapoint: Mapped[str | None] = mapped_column(String(36), nullable=True)
    explanation: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")


class UnresolvedQuarterORM(Base):
    __tablename__ = "unresolved_quarters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("drug_jobs.id"), index=True)
    period: Mapped[str] = mapped_column(String(32))
    reason_unresolved: Mapped[str] = mapped_column(Text)
    sources_checked: Mapped[list[Any]] = mapped_column(JSON, default=list)
    recommended_next_step: Mapped[str] = mapped_column(Text)
    confidence_that_unavailable: Mapped[float] = mapped_column(Float, default=0.0)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewEventORM(Base):
    __tablename__ = "review_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    datapoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExportORM(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    format: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_settings = get_settings()
# Sync engine for MVP simplicity (API + in-process workers in one process)
_sync_url = _settings.database_url.replace("sqlite+aiosqlite://", "sqlite://")
engine = create_engine(_sync_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Path = __import__("pathlib").Path
    if _sync_url.startswith("sqlite"):
        Path("./storage").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
