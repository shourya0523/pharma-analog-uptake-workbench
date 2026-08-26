# Pharma Analog Uptake Workbench — AWS CDK

Deploys the MVP:

- VPC (public subnets)
- RDS Postgres 16 (`db.t4g.micro`)
- S3 (data + static web)
- SQS job queue + DLQ
- ECS Fargate API + worker (same image; worker runs `python -m app.worker`)
- ALB (HTTP) + CloudFront (`/api*` → ALB with URI rewrite; SPA from S3)
- IAM for S3/SQS + Bedrock Converse + Mantle Web Search (no OpenRouter secret)

## Prerequisites

- AWS CLI authenticated (`aws login` or env credentials)
- Docker (for backend image asset)
- Node 20+ (for local frontend bundling during synth/deploy)
- Python 3.12+ with `infra/.venv`

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm i -g aws-cdk   # if needed
```

## Deploy

```bash
# Build SPA for CloudFront (/api proxy)
cd ../frontend && VITE_API_URL=/api npm ci && VITE_API_URL=/api npm run build && cd ../infra

export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_REGION=us-east-1

cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/$CDK_DEFAULT_REGION
cdk synth
cdk diff
cdk deploy
```

Enable Bedrock model access in the console for:

- Claude Sonnet (extract/judge) — default `us.anthropic.claude-sonnet-4-6`
- OpenAI GPT for Mantle Web Search — default `openai.gpt-5.6-terra`

**Account activation:** new accounts must finish AWS signup (payment method / service activation) before `cdk bootstrap` / `cdk deploy`. Until then EC2, S3, ECS, RDS, and CloudFormation return `OptInRequired` / `NotSignedUp`. Bedrock can still work earlier for local LLM smokes.

Outputs include `CloudFrontUrl`.
