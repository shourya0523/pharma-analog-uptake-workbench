# Pharma Analog Uptake Workbench — AWS CDK

Deploys the MVP:

- VPC (public subnets)
- RDS Postgres 16 (`db.t4g.micro`)
- S3 (data + static web)
- SQS job queue + DLQ
- ECS Fargate API + worker (same image; worker runs `python -m app.worker`)
- ALB (HTTP) + CloudFront (`/api*` → ALB with URI rewrite; SPA from S3)
- Secrets Manager secret for `OPENROUTER_API_KEY` (injected into API + worker tasks)

## Prerequisites

- AWS CLI authenticated (`aws login` or env credentials)
- Docker (for backend image asset)
- Node 20+ (for local frontend bundling during synth/deploy)
- Python 3.12+ with `infra/.venv`
- OpenRouter API key from https://openrouter.ai/keys

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm i -g aws-cdk   # if needed
```

## OpenRouter API key in AWS

The stack creates an empty Secrets Manager secret named **`pharma-workbench/openrouter-api-key`**. ECS reads it as the `OPENROUTER_API_KEY` environment variable. Set the value **after** you have the key from OpenRouter (do not commit the key to git).

### Option A — AWS Console

1. Deploy the stack (or create the secret manually with the same name).
2. Open [Secrets Manager](https://console.aws.amazon.com/secretsmanager/home?region=us-east-1).
3. Select **`pharma-workbench/openrouter-api-key`**.
4. **Retrieve secret value** → **Edit**.
5. Choose **Plaintext** and paste your OpenRouter key (starts with `sk-or-...`).
6. Save. Restart ECS tasks (or wait for the next deploy) so containers pick up the new value.

### Option B — AWS CLI (recommended)

Replace `YOUR_OPENROUTER_KEY` with the key from https://openrouter.ai/keys:

```bash
aws secretsmanager put-secret-value \
  --region us-east-1 \
  --secret-id pharma-workbench/openrouter-api-key \
  --secret-string 'YOUR_OPENROUTER_KEY'
```

If the secret does not exist yet (pre-deploy), create it first:

```bash
aws secretsmanager create-secret \
  --region us-east-1 \
  --name pharma-workbench/openrouter-api-key \
  --description "OpenRouter API key for Pharma Workbench LLM" \
  --secret-string 'YOUR_OPENROUTER_KEY'
```

After updating the secret, force a new ECS deployment so tasks reload env:

```bash
aws ecs update-service \
  --region us-east-1 \
  --cluster WorkbenchStack-Cluster* \
  --service WorkbenchStack-ApiService* \
  --force-new-deployment

aws ecs update-service \
  --region us-east-1 \
  --cluster WorkbenchStack-Cluster* \
  --service WorkbenchStack-WorkerService* \
  --force-new-deployment
```

(Use exact cluster/service names from the ECS console or `aws ecs list-clusters`.)

Stack outputs **`OpenRouterSecretArn`** and **`OpenRouterSecretName`** for reference.

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

Then set the OpenRouter secret (see above) and restart ECS services if they were already running.

Default models (override via ECS env in `stack.py` if needed):

- **Extract / search:** `openai/gpt-4o-mini`
- **Judge:** `openai/gpt-4o-mini`

**Account activation:** new accounts must finish AWS signup (payment method / service activation) before `cdk bootstrap` / `cdk deploy`. Until then EC2, S3, ECS, RDS, and CloudFormation may return `OptInRequired` / `NotSignedUp`.

Outputs include `CloudFrontUrl`.
