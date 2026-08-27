"""Idempotently backfill normalized pharmaceutical metadata from existing jobs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.db.models import DrugJobORM, SessionLocal, init_db  # noqa: E402
from app.remediation.backfill import backfill_job  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--job-id")
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        query = db.query(DrugJobORM)
        if args.run_id:
            query = query.filter_by(run_id=args.run_id)
        if args.job_id:
            query = query.filter_by(id=args.job_id)
        jobs = query.all()
        for job in jobs:
            result = backfill_job(db, job.id)
            print(
                f"job={result.job_id} product={result.product_id} "
                f"created_product={result.created_product}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
