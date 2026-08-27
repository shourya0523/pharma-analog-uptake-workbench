from __future__ import annotations

from typing import Any

from app.config import get_settings


def boto3_session(**overrides: Any):
    """Default credential chain on AWS; optional named profile locally."""
    import boto3

    settings = get_settings()
    kwargs: dict[str, Any] = {"region_name": settings.aws_region, **overrides}
    if settings.environment != "aws" and settings.aws_profile:
        kwargs.setdefault("profile_name", settings.aws_profile)
    return boto3.Session(**kwargs)
