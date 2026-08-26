# Pharmaceutical Analog Uptake Workbench

Source-first extraction and validation for pharmaceutical analog uptake data. Citations are mandatory on every source-derived field.

## Stack

- Backend: FastAPI (Python 3.12), SQLAlchemy, Amazon Bedrock (Claude Converse + Mantle Web Search)
- Frontend: React + Vite + Recharts (Analog Product Explorer)
- Local default: SQLite + local file store + in-process jobs
- AWS MVP: RDS Postgres + S3 + SQS/ECS via `FileStore` / `JobQueue` (`STORAGE_BACKEND=s3`, `JOB_BACKEND=sqs`) — see [`infra/README.md`](infra/README.md)

## Prerequisites

- AWS CLI v2 (`aws login`) for Bedrock and optional AWS deploy (Region `us-east-1`)
- Node 20+
- `uv` (https://docs.astral.sh/uv/)
- Bedrock model access for Claude (extract/judge) and GPT Mantle Web Search (optional local LLM search)

## Quick start (local)

```bash
# Backend
cd backend
cp .env.example .env   # set SEC_USER_AGENT email; AWS creds for Bedrock
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

Use the `CloudFrontUrl` stack output. Frontend calls the API at `/api` on the same host.
