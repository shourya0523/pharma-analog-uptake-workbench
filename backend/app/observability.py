"""In-process observability: ring-buffered logs + DB table browsing helpers."""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from app.db.models import (
    DatapointORM,
    DrugJobORM,
    ExportORM,
    ExtractionRunORM,
    QualityCheckORM,
    ReviewEventORM,
    SourceDocumentORM,
    UnresolvedQuarterORM,
    ValidationTaskORM,
)

_MAX_LOGS = 2000
_logs: deque[dict[str, Any]] = deque(maxlen=_MAX_LOGS)
_lock = threading.Lock()

TABLE_REGISTRY: dict[str, Any] = {
    "extraction_runs": ExtractionRunORM,
    "drug_jobs": DrugJobORM,
    "source_documents": SourceDocumentORM,
    "datapoints": DatapointORM,
    "review_events": ReviewEventORM,
    "quality_checks": QualityCheckORM,
    "validation_tasks": ValidationTaskORM,
    "unresolved_quarters": UnresolvedQuarterORM,
    "exports": ExportORM,
}


class RingBufferHandler(logging.Handler):
    """Capture recent application logs for the Observability UI."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["exc_info"] = self.formatException(record.exc_info)
            with _lock:
                _logs.append(entry)
        except Exception:
            self.handleError(record)


def attach_ring_buffer(level: int = logging.INFO) -> RingBufferHandler:
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RingBufferHandler):
            return h
    handler = RingBufferHandler(level=level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    return handler


def get_recent_logs(
    *,
    limit: int = 200,
    level: str | None = None,
    q: str | None = None,
    logger_name: str | None = None,
) -> list[dict[str, Any]]:
    level_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    min_rank = level_rank.get((level or "").upper(), 0)
    needle = (q or "").strip().lower()
    logger_filter = (logger_name or "").strip().lower()
    with _lock:
        items = list(_logs)
    out: list[dict[str, Any]] = []
    for entry in reversed(items):
        if min_rank and level_rank.get(entry["level"], 0) < min_rank:
            continue
        if logger_filter and logger_filter not in entry["logger"].lower():
            continue
        hay = f"{entry['logger']} {entry['message']} {entry.get('exc_info') or ''}".lower()
        if needle and needle not in hay:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _serialize_row(orm_cls: Any, row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    mapper = inspect(orm_cls)
    for col in mapper.columns:
        val = getattr(row, col.key)
        if isinstance(val, datetime):
            data[col.key] = val.isoformat()
        else:
            data[col.key] = val
    return data


def table_counts(db: Session) -> list[dict[str, Any]]:
    counts = []
    for name, cls in TABLE_REGISTRY.items():
        counts.append({"name": name, "count": db.query(func.count()).select_from(cls).scalar() or 0})
    return counts


def query_table(
    db: Session,
    table: str,
    *,
    limit: int = 100,
    offset: int = 0,
    run_id: str | None = None,
    job_id: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    if table not in TABLE_REGISTRY:
        raise KeyError(table)
    cls = TABLE_REGISTRY[table]
    query = db.query(cls)
    cols = {c.key for c in inspect(cls).columns}
    if run_id and "run_id" in cols:
        query = query.filter(getattr(cls, "run_id") == run_id)
    if run_id and table == "extraction_runs" and "id" in cols:
        query = query.filter(cls.id == run_id)
    if job_id and "job_id" in cols:
        query = query.filter(getattr(cls, "job_id") == job_id)
    if job_id and table == "drug_jobs" and "id" in cols:
        query = query.filter(cls.id == job_id)
    if q:
        needle = f"%{q.strip()}%"
        text_cols = [
            getattr(cls, c.key)
            for c in inspect(cls).columns
            if c.type.__class__.__name__ in {"String", "Text"}
        ]
        if text_cols:
            from sqlalchemy import or_

            query = query.filter(or_(*[col.ilike(needle) for col in text_cols]))
    total = query.count()
    order_col = getattr(cls, "created_at", None) or getattr(cls, "updated_at", None) or getattr(cls, "id")
    rows = query.order_by(order_col.desc() if hasattr(order_col, "desc") else order_col).offset(offset).limit(limit)
    return {
        "table": table,
        "total": total,
        "limit": limit,
        "offset": offset,
        "columns": [c.key for c in inspect(cls).columns],
        "rows": [_serialize_row(cls, r) for r in rows],
    }


def overview(db: Session) -> dict[str, Any]:
    runs = db.query(ExtractionRunORM).order_by(ExtractionRunORM.created_at.desc()).limit(20).all()
    job_status = (
        db.query(DrugJobORM.status, func.count())
        .group_by(DrugJobORM.status)
        .all()
    )
    recent_errors = [
        {
            "id": j.id,
            "drug_name": j.drug_name,
            "run_id": j.run_id,
            "status": j.status,
            "current_step": j.current_step,
            "error": j.error,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in db.query(DrugJobORM)
        .filter(DrugJobORM.error.isnot(None))
        .order_by(DrugJobORM.updated_at.desc())
        .limit(25)
        .all()
    ]
    return {
        "tables": table_counts(db),
        "job_status_counts": {status: count for status, count in job_status},
        "recent_runs": [
            {
                "id": r.id,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "error": r.error,
                "job_count": len(r.jobs) if r.jobs is not None else None,
            }
            for r in runs
        ],
        "recent_job_errors": recent_errors,
        "log_buffer_size": len(_logs),
    }


def normalize_analog_key(name: str | None) -> str:
    return " ".join((name or "").strip().lower().split())


def dedupe_jobs_by_analog(jobs: list[DrugJobORM]) -> list[DrugJobORM]:
    """Keep one job per analog product name (case/whitespace-insensitive).

    Preference: higher completeness, then newer updated_at, then id.
    """
    best: dict[str, DrugJobORM] = {}
    for job in jobs:
        key = normalize_analog_key(job.drug_name)
        if not key:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = job
            continue
        prev_ts = prev.updated_at or prev.created_at
        job_ts = job.updated_at or job.created_at
        better = (
            (job.completeness_pct or 0) > (prev.completeness_pct or 0)
            or (
                (job.completeness_pct or 0) == (prev.completeness_pct or 0)
                and (job_ts or datetime.min) > (prev_ts or datetime.min)
            )
            or (
                (job.completeness_pct or 0) == (prev.completeness_pct or 0)
                and (job_ts or datetime.min) == (prev_ts or datetime.min)
                and job.id > prev.id
            )
        )
        if better:
            best[key] = job
    return sorted(best.values(), key=lambda j: (j.drug_name or "").lower())
