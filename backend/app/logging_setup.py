from __future__ import annotations

import logging


_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent process logging for API + in-process workers."""
    global _CONFIGURED
    if _CONFIGURED:
        return
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
