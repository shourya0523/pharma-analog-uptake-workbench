#!/usr/bin/env python3
from __future__ import annotations

import os

import aws_cdk as cdk

from workbench.stack import WorkbenchStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

WorkbenchStack(
    app,
    "Workbench",
    env=env,
    description="Pharma Analog Uptake Workbench MVP on ECS + OpenRouter",
)

app.synth()
