"""LLM fingerprinting: where a document states product revenue, and how.

The deterministic header grammar reads the common shapes. Issuer documents
have an unbounded long tail of others, and a model reads those the way an
analyst does - by looking. This module asks a model to *locate and describe*
the regions of a converted document that state revenue for a set of
products: which grids, what unit and currency, what each column is, which row
is which product, which sentences state a figure. It never asks for the
numbers themselves. The extractor reads the numbers off the described region
and verifies the description against the row's own arithmetic, so a wrong
description produces no value rather than a wrong one.

One call covers every product of interest in a document, and the answer is
cached on disk by document content, prompt version and model, so the
benchmark replays are free and reproducible.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.config import get_settings
from app.domain.models import ParsedDocument
from app.extraction.columns import ColumnLayout, ColumnSpec, canonical_geography
from app.llm.client import OpenRouterClient, load_prompt
from app.parsing.evidence import MONEY_RE, product_aliases
from app.parsing.grids import is_value_token

logger = logging.getLogger(__name__)

PROMPT_NAME = "region_fingerprinter"
CACHE_DIR = Path(__file__).resolve().parents[2] / "storage" / "fingerprints"

_PERIOD_RE = re.compile(r"^(\d{4})(?:Q([1-4]))?$")
_MONTHS_BY_TYPE = {"quarterly": 3, "six_month": 6, "nine_month": 9, "annual": 12}
_UNIT_WORDS = {"units", "thousands", "millions", "billions"}


@dataclass(frozen=True)
class GridRegion:
    grid_index: int
    layout: ColumnLayout
    products: tuple[dict[str, Any], ...]
    why: str = ""


@dataclass(frozen=True)
class ProseRegion:
    product: str
    period: str
    period_type: str
    value: float
    unit: str
    currency: str
    geography: str | None
    quote: str
    period_from_context: bool = False


@dataclass
class Fingerprint:
    grids: list[GridRegion] = field(default_factory=list)
    prose: list[ProseRegion] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
    model: str = ""

    def grids_for(self, product: str, aliases: Iterable[str]) -> list[GridRegion]:
        names = {a.lower() for a in aliases} | {product.lower()}
        return [
            g for g in self.grids
            if any((p.get("product") or "").lower() in names for p in g.products)
        ]


# --------------------------------------------------------------------------
# Document sketch
# --------------------------------------------------------------------------

def _mentions(text: str, aliases: list[str]) -> bool:
    lowered = text.lower()
    return any(alias.lower() in lowered for alias in aliases)


def _grid_text(rows: list[list[str]], max_rows: int) -> str:
    lines = []
    for row in rows[:max_rows]:
        lines.append(" | ".join(cell for cell in row))
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")
    return "\n".join(lines)


def sketch_document(
    doc: ParsedDocument, *, aliases: list[str], max_chars: int = 90_000
) -> tuple[str, str, list[int]]:
    """(grids text, prose text, grid indexes shown) - what the model gets to see.

    Grids that name a product are shown whole (bounded); other grids are
    shown as their header and first rows only when they carry revenue words,
    so the model can still find a product table whose labels use a name the
    catalog does not list.
    """
    grid_parts: list[str] = []
    shown: list[int] = []
    budget = max_chars
    for index, rows in enumerate(doc.tables or []):
        joined = "\n".join(" ".join(row) for row in rows)
        if _mentions(joined, aliases):
            text = _grid_text(rows, 80)
        elif re.search(r"\b(?:product\s+sales|net\s+sales|revenues?)\b", joined, re.I) and len(rows) <= 60:
            text = _grid_text(rows, 12)
        else:
            continue
        block = f"[grid {index}]\n{text}\n"
        if len(block) > budget:
            continue
        budget -= len(block)
        grid_parts.append(block)
        shown.append(index)

    prose_parts: list[str] = []
    text = doc.full_text
    pattern = re.compile("|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)), re.I)
    seen_spans: list[tuple[int, int]] = []
    for match in pattern.finditer(text):
        start = max(0, text.rfind("\n\n", 0, match.start()))
        end = text.find("\n\n", match.end())
        end = len(text) if end == -1 else end
        if end - start > 2500:
            start = max(start, match.start() - 900)
            end = min(end, match.end() + 900)
        if any(s <= match.start() < e for s, e in seen_spans):
            continue
        passage = text[start:end].strip()
        if not MONEY_RE.search(passage):
            continue
        seen_spans.append((start, end))
        block = f"[passage @{start}]\n{passage}\n"
        if len(block) > budget:
            break
        budget -= len(block)
        prose_parts.append(block)
        if len(prose_parts) >= 40:
            break
    return "\n".join(grid_parts), "\n".join(prose_parts), shown


# --------------------------------------------------------------------------
# Parsing the model's description into a layout
# --------------------------------------------------------------------------

def _period_parts(period: str, period_type: str) -> tuple[int | None, int | None, int | None]:
    """(months, end_month, year) for a described column."""
    match = _PERIOD_RE.match((period or "").strip())
    if not match:
        return None, None, None
    year = int(match.group(1))
    quarter = int(match.group(2)) if match.group(2) else None
    months = _MONTHS_BY_TYPE.get(period_type or ("quarterly" if quarter else "annual"))
    if quarter and months == 3:
        return 3, quarter * 3, year
    if quarter and months and months != 3:
        # A quarter label with a longer type is a contradiction; trust the label.
        return 3, quarter * 3, year
    if months is None:
        return None, None, None
    return months, months if months < 12 else 12, year


def layout_from_region(region: dict[str, Any]) -> ColumnLayout | None:
    columns: list[ColumnSpec] = []
    coverage = {
        int(c.get("column_index", -1)): str(c.get("covers", ""))
        for c in region.get("coverage") or []
        if isinstance(c, dict)
    }
    for index, column in enumerate(region.get("columns") or []):
        if not isinstance(column, dict):
            return None
        kind = (column.get("kind") or "value").lower()
        if kind == "change":
            columns.append(ColumnSpec("change", label="change"))
            continue
        months, end_month, year = _period_parts(column.get("period") or "", column.get("period_type") or "")
        if year is None:
            return None
        covers = None
        span = coverage.get(index)
        if span and span.count("/") == 1:
            start, end = span.split("/")
            covers = (start, end)
        geography = canonical_geography(column.get("geography") or "") if column.get("geography") else None
        columns.append(ColumnSpec("value", months, end_month, year, geography=geography, covers=covers, label=str(column.get("period"))))
    if not any(c.kind == "value" for c in columns):
        return None
    unit = (region.get("unit") or "").lower().strip()
    currency = (region.get("currency") or "").upper().strip()
    unit_declared = unit in _UNIT_WORDS
    currency_declared = bool(re.fullmatch(r"[A-Z]{3}", currency))
    return ColumnLayout(
        columns=tuple(columns),
        unit_label=unit if unit_declared else "millions",
        currency=currency if currency_declared else "USD",
        unit_declared=unit_declared,
        currency_declared=currency_declared,
        notes=("llm_fingerprint",) + (() if unit_declared else ("unit_not_declared",)),
    )


def parse_fingerprint(payload: dict[str, Any]) -> Fingerprint:
    result = Fingerprint(raw=payload)
    for region in payload.get("regions") or []:
        if not isinstance(region, dict):
            continue
        kind = (region.get("kind") or "").lower()
        if kind in {"grid", "table"}:
            layout = layout_from_region(region)
            if layout is None or region.get("grid_index") is None:
                continue
            try:
                index = int(region["grid_index"])
            except (TypeError, ValueError):
                continue
            products = tuple(p for p in (region.get("products") or []) if isinstance(p, dict) and p.get("product"))
            result.grids.append(GridRegion(index, layout, products, str(region.get("why") or "")))
        elif kind == "prose":
            try:
                value = float(str(region.get("value")).replace(",", ""))
            except (TypeError, ValueError):
                continue
            period = str(region.get("period") or "")
            if not _PERIOD_RE.match(period) or not region.get("quote"):
                continue
            unit = (region.get("unit") or "millions").lower()
            result.prose.append(
                ProseRegion(
                    product=str(region.get("product")),
                    period=period,
                    period_type=str(region.get("period_type") or ("quarterly" if "Q" in period else "annual")),
                    value=value,
                    unit=unit if unit in _UNIT_WORDS else "millions",
                    currency=(region.get("currency") or "USD").upper(),
                    geography=canonical_geography(region.get("geography") or "") if region.get("geography") else None,
                    quote=str(region.get("quote")),
                    period_from_context=bool(region.get("period_from_context")),
                )
            )
    return result


# --------------------------------------------------------------------------
# The fingerprinter
# --------------------------------------------------------------------------

class LLMFingerprinter:
    def __init__(self, client: OpenRouterClient | None = None, *, model: str | None = None,
                 cache_dir: Path = CACHE_DIR, concurrency: int = 4) -> None:
        self.settings = get_settings()
        self.client = client or OpenRouterClient()
        self.model = model or self.settings.openrouter_model_fingerprint or self.settings.openrouter_model_extract
        self.prompt = load_prompt(PROMPT_NAME)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(concurrency)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openrouter_api_key)

    def _cache_key(self, doc: ParsedDocument, products: list[str], grids_text: str, prose_text: str) -> str:
        digest = hashlib.sha256()
        digest.update(str(self.prompt.get("version", 0)).encode())
        digest.update(self.model.encode())
        digest.update(json.dumps(sorted(products)).encode())
        digest.update(grids_text.encode())
        digest.update(prose_text.encode())
        return digest.hexdigest()

    async def fingerprint(
        self,
        doc: ParsedDocument,
        *,
        products: list[str],
        generics: dict[str, str | None] | None = None,
        title: str = "",
        url: str = "",
    ) -> Fingerprint:
        aliases: list[str] = []
        listed: list[str] = []
        for product in products:
            generic = (generics or {}).get(product)
            aliases.extend(product_aliases(product, generic))
            listed.append(f"{product} ({generic})" if generic else product)
        grids_text, prose_text, _shown = sketch_document(doc, aliases=aliases)
        if not grids_text and not prose_text:
            return Fingerprint(model=self.model)
        key = self._cache_key(doc, products, grids_text, prose_text)
        cache_path = self.cache_dir / f"{key}.json"
        if cache_path.exists():
            payload = json.loads(cache_path.read_text())
            result = parse_fingerprint(payload)
            result.cached = True
            result.model = self.model
            return result
        if not self.enabled:
            return Fingerprint(model=self.model)
        user = self.prompt["user_template"].format(
            products=", ".join(listed), title=title or "(untitled)", url=url,
            grids=grids_text or "(none)", prose=prose_text or "(none)",
        )
        async with self._semaphore:
            try:
                payload = await self.client.chat_json(model=self.model, system=self.prompt["system"], user=user)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fingerprint_failed url=%s error=%s", url, exc)
                return Fingerprint(model=self.model)
        cache_path.write_text(json.dumps(payload, indent=1))
        result = parse_fingerprint(payload)
        result.model = self.model
        return result
