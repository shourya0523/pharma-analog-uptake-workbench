"""The stored documents behind figures, for reading them beside the figure.

A reviewer checks a figure against the document it was read from. The pipeline
already keeps the bytes it fetched (``SourceDocumentORM.storage_key``), so the
document is served from the file store: the reviewer sees what the extractor
saw, not the page as the host serves it today, and no request leaves this
server on the reviewer's behalf.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.db.models import DatapointORM, SessionLocal, SourceDocumentORM
from app.storage.filestore import FileStore, get_file_store

router = APIRouter(tags=["sources"])

# Read by the frontend from a cross-origin response, so CORS must expose them.
SOURCE_HEADERS = ("X-Source-Url", "X-Source-Content-Type")


def _content_type(document: SourceDocumentORM) -> str:
    """The type the host declared when the pipeline fetched it, else the one the
    stored key's extension names; HTML when neither says."""
    declared = (document.metadata_json or {}).get("content_type")
    if declared:
        return str(declared)
    guessed, _ = mimetypes.guess_type(document.storage_key or "")
    return guessed or "text/html"


def stored_document(db, datapoint: DatapointORM) -> SourceDocumentORM | None:
    """The stored copy a figure was read from.

    The figure's own source row first. A figure entered or re-cited by a person
    has no source row, so the same job's stored copy of the same URL stands in
    for it.
    """
    if datapoint.source_id:
        own = db.get(SourceDocumentORM, datapoint.source_id)
        if own is not None and own.storage_key:
            return own
    return (
        db.query(SourceDocumentORM)
        .filter(
            SourceDocumentORM.job_id == datapoint.job_id,
            SourceDocumentORM.source_url == datapoint.source_url,
            SourceDocumentORM.storage_key.isnot(None),
        )
        .first()
    )


@router.get("/datapoints/{datapoint_id}/source")
async def datapoint_source(datapoint_id: str) -> Response:
    """The document a figure cites, as the pipeline stored it.

    The body is the stored bytes. ``X-Source-Url`` is where they were fetched
    from, for resolving relative links and opening the original;
    ``X-Source-Content-Type`` repeats the type for clients behind proxies that
    rewrite ``Content-Type``. 404 says why when there is nothing to show.
    """
    db = SessionLocal()
    try:
        datapoint = db.get(DatapointORM, datapoint_id)
        if datapoint is None:
            raise HTTPException(404, "datapoint not found")
        document = stored_document(db, datapoint)
        if document is None:
            raise HTTPException(404, "the pipeline holds no stored copy of this figure's source")
        key, url, content_type = document.storage_key, document.source_url, _content_type(document)
    finally:
        db.close()

    store: FileStore = get_file_store()
    if not await store.exists(key):
        raise HTTPException(404, "the stored copy of this source is missing from the file store")
    body = await store.get(key)
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "X-Source-Url": url,
            "X-Source-Content-Type": content_type,
            "Cache-Control": "private, max-age=3600",
        },
    )
