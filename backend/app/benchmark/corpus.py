"""The documents the gold rows cite, served to the pipeline as retrieved sources.

The pipeline's retrieval stage fetches from EDGAR and issuer sites. This
module stands in for that stage only: it hands the parser the same documents
a live run would have fetched, from a local copy when the network is not
available, so the parsing, extraction, normalization and derivation stages
run exactly as they would in production and can be scored offline.

A live refresh (``scripts/fetch_gold_corpus.py``) rebuilds the copy through
the pipeline's own HTTP path when a network is present.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.domain.models import RetrievalStatus, RetrievedSource, SourceType
from app.storage.filestore import FileStore

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLD_DIR = REPO_ROOT / "seed" / "gold"
CORPUS_DIR = GOLD_DIR / "corpus"
RAW_CORPUS_DIR = REPO_ROOT / "backend" / "storage" / "gold_corpus" / "raw"


@dataclass(frozen=True)
class CorpusDocument:
    url: str
    file: Path
    content_type: str
    sha256: str
    chars: int
    fetched_via: str
    fetched_from: str

    @property
    def key(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]


class CorpusFileStore(FileStore):
    """A read-only file store over the corpus, keyed by document URL hash."""

    def __init__(self, corpus: "Corpus") -> None:
        self.corpus = corpus

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        raise RuntimeError("the benchmark corpus is read-only")

    async def get(self, key: str) -> bytes:
        document = self.corpus.by_key[key.split("/")[-1].split(".")[0]]
        return self.corpus.read(document)

    async def exists(self, key: str) -> bool:
        return key.split("/")[-1].split(".")[0] in self.corpus.by_key

    def public_uri(self, key: str) -> str:
        return f"file://{self.corpus.by_key[key.split('/')[-1].split('.')[0]].file}"


def _source_type_for(url: str) -> SourceType:
    host = urlparse(url).netloc.lower()
    if host.endswith("sec.gov"):
        return SourceType.SEC_FILING
    return SourceType.COMPANY_IR


def _filing_type_for(url: str) -> str | None:
    path = urlparse(url).path.lower()
    if "sec.gov" not in urlparse(url).netloc:
        return None
    if "10vk" in path or "10-k" in path or "10k" in path:
        return "10-K"
    if "10vq" in path or "10-q" in path or "10q" in path:
        return "10-Q"
    if "ex99" in path or "ex-99" in path or "dex99" in path or "_99-" in path:
        return "8-K"
    return None


class Corpus:
    """The cited documents, in one rendering.

    ``rendering="raw"`` serves the bytes the pipeline's own HTTP fetch
    returned (HTML, PDF), from the cache ``scripts/fetch_gold_corpus.py``
    writes; a document missing from that cache falls back to the committed
    text rendering. ``rendering="markdown"`` serves only the committed text
    rendering, which is what a session without network access has.
    """

    def __init__(self, directory: Path = CORPUS_DIR, *, rendering: str = "raw") -> None:
        self.directory = directory
        self.rendering = rendering
        manifest = json.loads((directory / "manifest.json").read_text())
        documents = {
            entry["url"]: CorpusDocument(
                url=entry["url"],
                file=directory / entry["file"],
                content_type=entry.get("content_type", "text/markdown"),
                sha256=entry["sha256"],
                chars=entry["chars"],
                fetched_via=entry.get("fetched_via", ""),
                fetched_from=entry.get("fetched_from", entry["url"]),
            )
            for entry in manifest["documents"]
        }
        raw_manifest = RAW_CORPUS_DIR / "manifest.json"
        if rendering == "raw" and raw_manifest.exists():
            for entry in json.loads(raw_manifest.read_text())["documents"]:
                if entry.get("error") or not entry.get("file"):
                    continue
                path = RAW_CORPUS_DIR / entry["file"]
                if not path.exists():
                    continue
                documents[entry["url"]] = CorpusDocument(
                    url=entry["url"],
                    file=path,
                    content_type=(entry.get("content_type") or "").split(";")[0] or "application/octet-stream",
                    sha256=entry.get("sha256", ""),
                    chars=entry.get("bytes", 0),
                    fetched_via="pipeline_http",
                    fetched_from=entry.get("final_url", entry["url"]),
                )
        self.documents: list[CorpusDocument] = list(documents.values())
        self.by_url = {d.url: d for d in self.documents}
        self.by_key = {d.key: d for d in self.documents}

    def read(self, document: CorpusDocument) -> bytes:
        raw = document.file.read_bytes()
        if document.file.suffix == ".gz":
            return gzip.decompress(raw)
        return raw

    def file_store(self) -> FileStore:
        return CorpusFileStore(self)

    def source_for(self, url: str) -> RetrievedSource | None:
        document = self.by_url.get(url)
        if document is None:
            return None
        if document.file.name.endswith(".pdf"):
            suffix = ".pdf"
        elif document.file.name.endswith((".htm", ".html")):
            suffix = ".htm"
        elif "markdown" in document.content_type:
            suffix = ".md"
        else:
            suffix = ".txt"
        return RetrievedSource(
            source_id=document.key,
            source_type=_source_type_for(url),
            url=url,
            title=None,
            filing_type=_filing_type_for(url),
            storage_key=f"corpus/{document.key}{suffix}",
            retrieval_status=RetrievalStatus.SUCCESS,
            metadata={"fetched_via": document.fetched_via, "fetched_from": document.fetched_from},
        )
