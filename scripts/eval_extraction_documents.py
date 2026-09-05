"""Score the extraction pipeline against the filings themselves.

`eval_extraction.py` replays each gold row's ``source_quote`` - the passage the
row cites as evidence - and asks whether the right figure can be recovered from
it. That measures column alignment, which is worth measuring, but it also makes
the score depend on how the quote was written: widening a quote to include the
document's column header moved that number by 1.5 points in a single afternoon
without the pipeline changing at all.

This eval closes that loop. A gold row's quote goes back to being a receipt, and
the pipeline is handed the whole document the row cites - parsed by the app's
own reader, with the app's own limits - and asked for that product and quarter.
The score is then a property of the pipeline rather than of the prose.

    SEC_CONTACT='...' DOCUMENT_CACHE=/tmp/gold-documents \
        python scripts/sourcing/fetch_documents.py
    DOCUMENT_CACHE=/tmp/gold-documents python scripts/eval_extraction_documents.py

Rows whose document is not cached are reported separately and never counted as
passes: an unreachable filing is a gap in the evidence, not a success.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bs4 import BeautifulSoup  # noqa: E402

from app.extraction.candidates import extract_revenue_candidates  # noqa: E402
from app.parsing.documents import html_tables, pdf_tables  # noqa: E402

GOLD = REPO / "seed" / "gold"
CACHE = pathlib.Path(os.environ.get("DOCUMENT_CACHE", "/tmp/gold-documents"))
TOLERANCE = 0.51  # the issuers' own independent per-period rounding
# EVAL_UNCAPPED=1 lifts the pipeline's ceiling on tables kept per document.
# It is a diagnostic, not the headline: it prices that ceiling rather than
# pretending the pipeline does not have one.
CAPPED = os.environ.get("EVAL_UNCAPPED") != "1"


def load_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in (GOLD / "quarterly_revenue.jsonl").read_text().splitlines()
        if line.strip()
    ]


def cache_path(url: str) -> pathlib.Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    suffix = ".pdf" if url.lower().split("?")[0].endswith(".pdf") else ".html"
    return CACHE / (digest + suffix)


def document_text(path: pathlib.Path) -> str:
    """The leading text the pipeline passes as fingerprint context.

    `orchestrator.py` hands `doc.full_text[:4000]` to the extractor, and that
    is where unit and currency declarations are found when a filing states
    them outside the table element. Passing anything narrower here would judge
    the pipeline on an input it never actually receives.
    """
    raw = path.read_bytes()
    if path.suffix == ".pdf":
        blocks, _tables = pdf_tables(raw)
        return "\n".join(blocks)[:4000]
    markup = raw.decode("utf-8", "ignore")
    head = markup.lstrip()[:256].lower()
    parser = "lxml-xml" if head.startswith(("<?xml", "<xbrl", "<ix:")) else "lxml"
    soup = BeautifulSoup(markup, parser)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)[:4000]


def tables_of(path: pathlib.Path, *, capped: bool = True) -> list[list[list[str]]]:
    """The document as the pipeline sees it - same reader, same limits.

    ``capped=False`` lifts the pipeline's own ceiling on how many tables it
    keeps per document. It is not the headline measurement; it exists to price
    that ceiling, because a Gilead 8-K exhibit holds 39 tables and prints its
    PRODUCT SALES SUMMARY in the thirty-seventh.
    """
    raw = path.read_bytes()
    if path.suffix == ".pdf":
        _blocks, tables = pdf_tables(raw)
        return tables
    markup = raw.decode("utf-8", "ignore")
    head = markup.lstrip()[:256].lower()
    parser = "lxml-xml" if head.startswith(("<?xml", "<xbrl", "<ix:")) else "lxml"
    soup = BeautifulSoup(markup, parser)
    if capped:
        return html_tables(soup)
    rows_out = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            rows_out.append(rows)
    return rows_out


def main() -> int:
    rows = load_rows()
    # One parse per document, not one per row: a Gilead exhibit backs a dozen
    # products and re-reading it for each would say nothing extra.
    by_document: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_document[row["source_url"]].append(row)

    outcomes: dict[str, list[dict]] = collections.defaultdict(list)
    parsed_cache: dict[str, list[list[list[str]]]] = {}
    context_cache: dict[str, str] = {}

    for url, group in by_document.items():
        path = cache_path(url)
        if not path.exists():
            for row in group:
                outcomes["no_document"].append(row)
            continue
        try:
            if url not in parsed_cache:
                parsed_cache[url] = tables_of(path, capped=CAPPED)
                context_cache[url] = document_text(path)
            tables = parsed_cache[url]
            context = context_cache[url]
        except Exception as exc:  # a document the pipeline cannot open at all
            for row in group:
                outcomes["unreadable_document"].append({**row, "why": str(exc)})
            continue

        for row in group:
            candidates, _findings, _skipped = extract_revenue_candidates(
                tables,
                product=row["drug_name"],
                generic=row.get("generic_name"),
                context=context,
            )
            wanted = [
                candidate
                for candidate in candidates
                if candidate["period"] == row["period"]
            ]
            target = row["value_normalized_usd_millions"]
            if not wanted:
                outcomes["not_found"].append(row)
            elif any(
                abs(candidate["value_normalized_usd_millions"] - target) <= TOLERANCE
                for candidate in wanted
            ):
                outcomes["read"].append(row)
            else:
                best = min(
                    wanted,
                    key=lambda c: abs(c["value_normalized_usd_millions"] - target),
                )
                outcomes["wrong_value"].append(
                    {
                        **row,
                        "read": best["value_normalized_usd_millions"],
                        "label": best.get("product_label") or best.get("scope", "?"),
                    }
                )

    scored = len(rows) - len(outcomes["no_document"])
    read = len(outcomes["read"])
    print(f"gold quarterly rows: {len(rows)}")
    print(f"  documents cited:   {len(by_document)}")
    print(f"  not cached:        {len(outcomes['no_document'])} rows (not scored)")
    print()
    print("pipeline over the cited document, whole-document in, value out"
          + ("" if CAPPED else "   [EVAL_UNCAPPED: table ceiling lifted]"))
    print(f"  read correctly     {read}/{scored}   {read / scored:6.2%}")
    for name in ("wrong_value", "not_found", "unreadable_document"):
        if outcomes[name]:
            print(f"  {name:<18} {len(outcomes[name])}")

    if outcomes["wrong_value"]:
        print("\nwrong value - the pipeline answered, and was wrong:")
        for row in sorted(outcomes["wrong_value"], key=lambda r: (r["drug_name"], r["period"]))[:15]:
            print(f"   {row['drug_name']:<12}{row['period']}  gold "
                  f"{row['value_normalized_usd_millions']:<10g} read {row['read']:<10g}"
                  f" as {row['label']!r}")

    if outcomes["not_found"]:
        worst = collections.Counter(r["drug_name"] for r in outcomes["not_found"])
        print("\nno value found for that product and quarter, by product:")
        for name, count in worst.most_common(12):
            print(f"   {name:<20}{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
