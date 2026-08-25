# Pharmaceutical Analog Uptake Workbench

Source-first extraction and validation for pharmaceutical analog uptake data. Citations are mandatory on every source-derived field.

## Stack

- Backend: FastAPI (Python 3.12), SQLAlchemy, OpenRouter LLM
- Frontend: React + Vite + Recharts (Analog Product Explorer)
- Local default: SQLite + local file store + in-process jobs
- AWS MVP path: RDS Postgres + S3 + SQS/ECS via `FileStore` / `JobQueue` interfaces (`STORAGE_BACKEND=s3`, `JOB_BACKEND=sqs`)

## Prerequisites

- AWS CLI v2 + Agent Toolkit profile `Sandbox` (Region `us-east-1`)
- Node 20+
- `uv` (https://docs.astral.sh/uv/)
- OpenRouter API key for LLM extraction/judge (optional for connector-only smoke tests)

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

## Citations

- Every datapoint/profile field stores `source_url` + quote/field + confidence + validation status
- Missing citation → high-severity quality flag; cannot auto-pass or export as confirmed
- Dashboard drill-through shows citation metadata

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

## AWS (this account → later org)

Current login profile: `Sandbox` / `us-east-1`.

1. Create bucket + queue + RDS via `infra/` (CDK skeleton)
2. Set env: `ENVIRONMENT=aws`, `STORAGE_BACKEND=s3`, `JOB_BACKEND=sqs`, `DATABASE_URL=postgresql+...`, `S3_BUCKET=...`, `SQS_QUEUE_URL=...`
3. Deploy API/workers to ECS; frontend to S3+CloudFront

**Org migration checklist**

1. `aws login --profile <new-org-profile>`
2. Add profile to `AWS_MCP_PROXY_PROFILES` in `~/.cursor/mcp.json`
3. Redeploy parameterized stack (no account IDs in app code)
4. Re-create secrets (OpenRouter, DB)

Agent Toolkit credentials last 12 hours (renewable up to 90 days).

## Export

- Product workbook sheets: Quarterly Revenue, Source Audit Log, Unresolved Quarter Tracker, Drug Profile, Quality Checks
- Power BI product CSV includes normalized indications, MoA and EPC separately, approved LoT, competitive formula/coverage, typed peak method/inputs, uptake methodology, and source URL
- Official Excel template mapper is stubbed until the workbook is provided

## Seed

`seed/example_drugs.csv` — PAH analogs aligned with the dashboard mockup.
