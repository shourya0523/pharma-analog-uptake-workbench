"""Are the pipeline's own citations real? No gold is consulted.

A gold row's ``source_quote`` is a receipt a human wrote. The pipeline's
``source_quote`` is the pipeline's claim about where it read a number. Scoring
one against the other says nothing about provenance, because handing the
pipeline a passage means it never had to find or cite anything.

This asks the question that needs no answer key. For every datapoint the
pipeline produces, take the three things it asserts - the value, the quote it
says the value came from, and the document it cites - and check them against
that document:

    is the quote actually in the document the pipeline cites?
    does the value actually appear in that quote?

Both are decidable from the filing alone. A pipeline can be wrong about a
number and still be honest about where it looked; it can also be right about a
number for a reason it cannot show, which is worse, because nothing downstream
can audit it.

The pipeline enforces both rules internally - ``quote_is_verbatim`` in
``app/llm/grounding.py`` and ``value_supported_by_quote`` in
``app/extraction/check.py``. Nothing verified that those held on real output
until this script.

Gold is used for one thing only, and it is not the verdict: it supplies the
list of (product, document) pairs worth running, because a corpus has to come
from somewhere. Swap in any other list and the measurement is unchanged.

    DOCUMENT_CACHE=/tmp/gold-documents python scripts/eval_provenance.py
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bs4 import BeautifulSoup  # noqa: E402

from app.extraction.candidates import extract_revenue_candidates  # noqa: E402
from app.llm.client import LLMModules  # noqa: E402
from app.llm.grounding import quote_is_verbatim  # noqa: E402
from app.parsing.documents import html_tables, pdf_tables  # noqa: E402
from app.parsing.evidence import build_revenue_llm_text  # noqa: E402
from app.quality.candidate_filters import filter_revenue_candidates  # noqa: E402

GOLD = REPO / "seed" / "gold"
CACHE = pathlib.Path(os.environ.get("DOCUMENT_CACHE", "/tmp/gold-documents"))


def cache_path(url: str) -> pathlib.Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    return CACHE / (digest + (".pdf" if url.lower().split("?")[0].endswith(".pdf") else ".html"))


def document_text_and_tables(path: pathlib.Path) -> tuple[str, list]:
    raw = path.read_bytes()
    if path.suffix == ".pdf":
        blocks, tables = pdf_tables(raw)
        return "\n".join(blocks), tables
    markup = raw.decode("utf-8", "ignore")
    head = markup.lstrip()[:256].lower()
    parser = "lxml-xml" if head.startswith(("<?xml", "<xbrl", "<ix:")) else "lxml"
    soup = BeautifulSoup(markup, parser)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True), html_tables(soup)


def value_in_quote(value: float, quote: str) -> bool:
    """The same rule the pipeline applies to itself, applied from outside."""
    text = (quote or "").replace(",", "")
    forms = {f"{value:g}", f"{value:.1f}", f"{value:.3f}", str(abs(value))}
    return any(re.search(rf"(?<!\d){re.escape(form)}(?!\d)", text) for form in forms)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--with-llm", action="store_true",
        help="also audit the model pass, which is the half that can invent a "
             "quote - the deterministic reader copies its quote out of the "
             "table it read, so it is close to auditable by construction",
    )
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in (GOLD / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]
    pairs = sorted({(row["drug_name"], row.get("generic_name") or "", row["source_url"])
                    for row in rows})
    if args.limit:
        pairs = pairs[: args.limit]

    llm = LLMModules() if args.with_llm else None
    parsed: dict[str, tuple[str, list]] = {}
    tally = collections.Counter()
    offenders: list[dict] = []

    for product, generic, url in pairs:
        path = cache_path(url)
        if not path.exists():
            tally["document_not_cached"] += 1
            continue
        if url not in parsed:
            try:
                parsed[url] = document_text_and_tables(path)
            except Exception:
                parsed[url] = ("", [])
        text, tables = parsed[url]
        if not tables:
            tally["document_unreadable"] += 1
            continue
        candidates, _findings, _skipped = extract_revenue_candidates(
            tables, product=product, generic=generic or None, context=text[:4000]
        )
        origin = {id(c): "table" for c in candidates}
        if llm is not None:
            import asyncio
            from app.domain.models import ParsedDocument, ParsingStatus

            doc = ParsedDocument(
                source_id="audit", text_blocks=[text], tables=tables,
                parsing_status=ParsingStatus.SUCCESS,
            )
            llm_text, _evidence = build_revenue_llm_text(
                doc, product=product, generic=generic or None
            )
            result = asyncio.run(
                llm.extract_revenue(product=product, company=None,
                                    source_meta={"url": url}, text=llm_text)
            )
            spans = "\n\n".join(s.get("span_text") or "" for s in (result.get("spans") or []))
            kept, _dropped = filter_revenue_candidates(
                result.get("candidates") or [], product=product,
                generic=generic or None, source_text=spans or llm_text,
            )
            for candidate in kept:
                origin[id(candidate)] = "model"
            candidates = list(candidates) + list(kept)

        for candidate in candidates:
            source = origin.get(id(candidate), "table")
            quote = (candidate.get("source_quote") or "").strip()
            value = candidate.get("value_reported")
            tally["datapoints"] += 1
            tally[f"datapoints_{source}"] += 1
            if not quote:
                tally["no_quote_at_all"] += 1
                offenders.append({"product": product, "url": url, "why": "no quote"})
                continue
            grounded = quote_is_verbatim(quote, text, min_len=1)
            supported = value is not None and value_in_quote(float(value), quote)
            tally["quote_verbatim_in_cited_document"] += grounded
            tally["value_present_in_own_quote"] += supported
            if grounded and supported:
                tally["fully_auditable"] += 1
                tally[f"auditable_{source}"] += 1
            else:
                offenders.append({
                    "product": product, "url": url, "value": value,
                    "quote": quote[:110],
                    "why": ("quote not in cited document" if not grounded
                            else "value not in own quote"),
                })

    total = tally["datapoints"]
    print(f"(product, document) pairs run: {len(pairs)}")
    for key in ("document_not_cached", "document_unreadable"):
        if tally[key]:
            print(f"  {key}: {tally[key]}")
    print(f"\ndatapoints the pipeline published: {total}"
          + (f"   (table {tally['datapoints_table']}, model {tally['datapoints_model']})"
             if tally["datapoints_model"] else ""))
    if not total:
        print("  nothing to audit")
        return 0
    for key, label in (
        ("quote_verbatim_in_cited_document", "quote is verbatim in the document it cites"),
        ("value_present_in_own_quote", "value appears in its own quote"),
        ("fully_auditable", "both - the citation stands on its own"),
    ):
        print(f"  {label:<46}{tally[key]:>6}/{total}  {tally[key] / total:6.1%}")

    if tally["datapoints_model"]:
        print("\n  by origin - the model pass is the one that can invent a quote:")
        for origin_name in ("table", "model"):
            seen = tally[f"datapoints_{origin_name}"]
            if seen:
                ok = tally[f"auditable_{origin_name}"]
                print(f"    {origin_name:<8}{ok:>5}/{seen:<6}{ok / seen:7.1%}")

    if offenders:
        print(f"\n{len(offenders)} citation(s) that do not stand up:")
        for bad in offenders[:12]:
            print(f"   {bad['product']:<14}{bad['why']}")
            print(f"      {bad.get('quote', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
