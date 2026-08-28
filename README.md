# Pharmaceutical Analog Uptake Workbench

Source-first extraction and validation for pharmaceutical analog uptake data. Citations are mandatory on every source-derived field.

## Stack

- Backend: FastAPI (Python 3.12), SQLAlchemy, OpenRouter (extract, judge, web search)
- Frontend: React + Vite + Recharts (Analog Product Explorer)
- Local default: SQLite + local file store + in-process jobs
- AWS MVP: RDS Postgres + S3 + SQS/ECS via `FileStore` / `JobQueue` (`STORAGE_BACKEND=s3`, `JOB_BACKEND=sqs`) — see [`infra/README.md`](infra/README.md)

## Prerequisites

- Node 20+
- `uv` (https://docs.astral.sh/uv/)
- OpenRouter API key (https://openrouter.ai/keys) for LLM extract/judge/search

## Quick start (local)

```bash
# Backend
cd backend
cp .env.example .env   # set OPENROUTER_API_KEY and SEC_USER_AGENT email
uv sync
uv run uvicorn app.main:app --reload --port 8000

# Frontend (other terminal)
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173 — paste drugs or upload `seed/example_drugs.csv`.

API docs: http://127.0.0.1:8000/docs

## SEC fair access

Set `SEC_USER_AGENT` to a descriptive string with a contact email. Filings are cached under storage keys (local or S3).

## Deploy to AWS

```bash
cd infra
source .venv/bin/activate
pip install -r requirements.txt
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_REGION=us-east-1
cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/$CDK_DEFAULT_REGION   # first time
cdk deploy
```

Use the `ApiUrl` stack output (ALB). After AWS verifies CloudFront, deploy with `-c enable_cloudfront=true` and use `CloudFrontUrl` (`/api` same-origin).

## Pharmaceutical data semantics

- FDA Established Pharmacologic Class (EPC) and mechanism of action (MoA) are separate. EPC is never used as a MoA fallback.
- Approved line of therapy is stored per indication and only assigned from explicit label wording. Label silence is `all_lines_or_unspecified`, not first line.
- Canonical product, active moiety, formulation, delivery device, and analog family remain distinct. Shared moiety does not merge commercial revenue.
- Peak estimates remain typed as `observed`, `consensus`, or `modeled`. The selected value records its policy, as-of date, scope, and source inputs.
- Launch uptake is labeled `revenue_proxy_r4q`: rolling-four-quarter product sales divided by selected annual peak. The first three quarters are `insufficient_history`.
- Competitive intensity uses `competitive_intensity_v1` and stored peer classifications. Cohorts under six launches use provisional thresholds and expose `low_coverage=true`.

Public label, regulatory, SEC, company IR, and ClinicalTrials.gov sources are supported. Licensed consensus, claims, prescription, and patient-volume data use cited manual imports until credentials and redistribution rights are available. The application does not scrape paid vendors.

Consensus/manual peak CSV columns are:

```text
product,estimate_type,value,currency,geography,revenue_scope,as_of_date,source_url
```

All columns are required. Cross-currency values remain unresolved unless a cited, period-compatible FX observation is stored.

## Migrations and backfill

Alembic is authoritative at startup. A legacy unversioned database is stamped only when its table/column fingerprint exactly matches the supported baseline; unknown schemas stop with remediation guidance.

```bash
# Back up backend/storage/workbench.db first
cd backend
uv run alembic upgrade head

# Idempotent normalized metadata backfill
cd ..
uv run --project backend python scripts/backfill_pharma_metadata.py
# optionally: --run-id <id> or --job-id <id>
```

Confirmed reviewer assertions always outrank automated backfill. Label re-fetch is intentionally skipped when no stable application number or SPL set ID is available.

For a connector-free metadata smoke check:

```bash
uv run --project backend python scripts/smoke_validate.py --metadata-only
```

## Export

- Product workbook sheets: Quarterly Revenue, Source Audit Log, Unresolved Quarter Tracker, Drug Profile, Quality Checks
- Power BI product CSV includes normalized indications, MoA and EPC separately, approved LoT, competitive formula/coverage, typed peak method/inputs, uptake methodology, and source URL
- Official Excel template mapper is stubbed until the workbook is provided

## Seed

`seed/example_drugs.csv` — PAH analogs aligned with the dashboard mockup.
