"""CDK skeleton for AWS MVP — parameterized for org migration.

Deploy after `aws login --profile Sandbox`.

Resources (planned):
- S3 bucket for sources/exports
- SQS queue for drug jobs
- RDS Postgres
- ECS Fargate services (API + worker)
- CloudFront + S3 for frontend

This file is a placeholder; use Agent Toolkit aws-cdk / aws-deployment skills
to expand for the Sandbox account without hard-coding account IDs.
"""

APP_NAME = "pharma-analog-uptake-workbench"
DEFAULT_REGION = "us-east-1"
