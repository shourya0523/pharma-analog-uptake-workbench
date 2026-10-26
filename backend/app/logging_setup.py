from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure_logging(level: int | None = None) -> None:
    """Idempotent process logging for API + in-process workers.

    Messages use greppable `event key=value` form so Observability search and
    `scripts/eval.py` output stay aligned with what a person reads in a terminal.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    if level is None:
        name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
        level = getattr(logging, name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    from app.observability import attach_ring_buffer

    attach_ring_buffer(level=level)
    _CONFIGURED = True
