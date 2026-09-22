"""Deterministic amount loci for Jev revenue pick.

Harvest finds money spans the document already printed. Jev only chooses among
them; it never invents an amount or a quote.
"""

from __future__ import annotations

import re
from typing import Any

from app.parsing.evidence import MONEY_RE, REVENUE_HINT_RE, product_aliases

# Max Choice options Jev accepts; leave room for the none hatch.
MAX_LOCI = 254
# Hard cap before chunking into System One calls. A 10-K can mint hundreds of
# money spans; only the best of them are worth asking about.
MAX_LOCI_FOR_JEV = 32
# Questions per /systemone call. Each locus is one keep Noul (+ optional period
# Choice); past this the request trips max_tokens_exceeded.
LOCI_PER_CALL = 12

_PERIOD_HINT_RE = re.compile(
    r"("
    r"(?:three|six|nine|twelve)\s+months?\s+ended[^.\n]{0,40}"
    r"|year\s+ended[^.\n]{0,40}"
    r"|Q[1-4]\s*20\d{2}"
    r"|20\d{2}\s*Q[1-4]"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+20\d{2}"
    r")",
    re.IGNORECASE,
)


def harvest_amount_loci(
    text: str,
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
    max_loci: int = MAX_LOCI,
) -> list[dict[str, Any]]:
    """Money spans near a product alias, each a closed option for Jev Choice.

    A locus carries the verbatim quote the amount sat in. Downstream copies
    that quote; nothing retypes the digits.
    """
    if not text.strip():
        return []
    aliases = product_aliases(product, generic, extra=extra_aliases)
    alias_re = None
    if aliases:
        parts = [re.escape(a) for a in sorted(aliases, key=len, reverse=True)]
        alias_re = re.compile(r"(" + "|".join(parts) + r")", re.IGNORECASE)

    loci: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in MONEY_RE.finditer(text):
        amount = match.group(0).strip()
        if not amount:
            continue
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 180)
        quote = text[start:end].strip()
        key = f"{amount}|{quote[:80]}"
        if key in seen:
            continue
        seen.add(key)
        window = quote
        if alias_re and not alias_re.search(window):
            # Keep money near a revenue word even without the brand in-window;
            # Jev can refuse. Skip pure noise far from either signal.
            if not REVENUE_HINT_RE.search(window):
                continue
        period_hints = [m.group(0).strip() for m in _PERIOD_HINT_RE.finditer(window)]
        loci.append(
            {
                "locus_id": f"l{len(loci) + 1}",
                "amount": amount,
                "period_hints": period_hints[:4],
                "row_label": _row_label(window, amount),
                "quote": quote[:800],
                "source_id": None,
            }
        )
        if len(loci) >= max_loci:
            break
    return loci


def harvest_table_loci(
    tables: list[list[list[str]]],
    *,
    product: str,
    generic: str | None = None,
    extra_aliases: list[str] | None = None,
    max_loci: int = MAX_LOCI,
) -> list[dict[str, Any]]:
    """Amount cells from parsed tables that mention the product or revenue."""
    aliases = [a.lower() for a in product_aliases(product, generic, extra=extra_aliases)]
    loci: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ti, table in enumerate(tables or []):
        flat = " | ".join(" ".join(cell for cell in row) for row in table)
        if aliases and not any(a in flat.lower() for a in aliases) and not MONEY_RE.search(flat):
            continue
        headers = _header_row(table)
        for ri, row in enumerate(table[:60]):
            row_label = (row[0] if row else "") or ""
            if aliases and not any(a in row_label.lower() for a in aliases):
                # Still allow money cells when the table itself names the product.
                if not any(a in flat.lower() for a in aliases):
                    continue
            for ci, cell in enumerate(row[1:] if len(row) > 1 else row, start=1):
                amounts = MONEY_RE.findall(cell or "")
                if not amounts and re.fullmatch(r"[\d,]+(?:\.\d+)?", (cell or "").strip()):
                    amounts = [(cell or "").strip()]
                for amount in amounts:
                    amount = amount.strip()
                    if not amount:
                        continue
                    period_hint = headers[ci] if ci < len(headers) else ""
                    quote = f"{row_label} | {period_hint} | {amount}".strip(" |")
                    key = f"{ti}:{ri}:{ci}:{amount}"
                    if key in seen:
                        continue
                    seen.add(key)
                    loci.append(
                        {
                            "locus_id": f"t{ti}_{ri}_{ci}",
                            "amount": amount,
                            "period_hints": [period_hint] if period_hint else [],
                            "row_label": row_label[:200],
                            "quote": quote[:800],
                            "source_id": None,
                        }
                    )
                    if len(loci) >= max_loci:
                        return loci
    return loci


def merge_loci(*groups: list[dict[str, Any]], max_loci: int = MAX_LOCI) -> list[dict[str, Any]]:
    """Deduped loci in document order of first appearance."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for locus in group:
            key = f"{locus.get('amount')}|{(locus.get('quote') or '')[:60]}"
            if key in seen:
                continue
            seen.add(key)
            out.append({**locus, "locus_id": locus.get("locus_id") or f"m{len(out) + 1}"})
            if len(out) >= max_loci:
                return out
    return out


def _header_row(table: list[list[str]]) -> list[str]:
    if not table:
        return []
    # First row that looks like period labels rather than amounts.
    for row in table[:3]:
        joined = " ".join(row)
        if _PERIOD_HINT_RE.search(joined) or re.search(r"20\d{2}", joined):
            return list(row)
    return list(table[0]) if table else []


def _row_label(window: str, amount: str) -> str:
    before = window.split(amount, 1)[0]
    line = before.rsplit("\n", 1)[-1].strip()
    return line[-120:] if line else ""
