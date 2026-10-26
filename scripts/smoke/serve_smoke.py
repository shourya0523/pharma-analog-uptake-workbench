"""Smoke-only server entrypoint.

Named so blanket `pkill -f 'uvicorn app.main:app'` from competing agent
sessions does not match this process.
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("SMOKE_PORT", "18765"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
