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

fetch_documents.py reads SEC_CONTACT; --discover drives the app's own connector,
which reads SEC_USER_AGENT. Setting one and not the other leaves whichever half
of that is not covered talking to EDGAR as the default User-Agent.

Rows whose document is not cached are reported separately and never counted as
passes: an unreachable filing is a gap in the evidence, not a success.

``--discover`` stops handing the pipeline a URL. Gold then supplies only the
product, the issuer and the quarter - the pipeline resolves the issuer's CIK,
walks EDGAR for earnings exhibits around that quarter, and reads whatever it
finds. That is the pipeline's score. This one, without it, is a diagnostic:

    DOCUMENT_CACHE=/tmp/gold-documents python scripts/eval_extraction_documents.py
    python scripts/eval_extraction_documents.py --discover --limit 40

The difference is not academic, and the warning above was already here while it
was being ignored. Gold cites Johnson & Johnson's investor-relations PDFs, so
this eval scored the pipeline on reading a PDF, which drove a PDF reader to be
built. Meanwhile the connector took one exhibit per earnings 8-K and J&J puts
its schedules in the second one, so sourcing for itself the pipeline scored
0/24 on those quarters. Deleting ``[:1]`` took it to 23/24, from filings on
EDGAR in HTML, which is where it had always been able to look. A measurement
that removes the step that was broken cannot report that it is broken.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import gzip
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bs4 import BeautifulSoup  # noqa: E402

from app.connectors.sources import SECConnector  # noqa: E402
from app.extraction.candidates import extract_revenue_candidates  # noqa: E402
from app.parsing.documents import (  # noqa: E402
    DocumentParser,
    flatten_grid,
    html_table_grid,
    html_table_grids,
    table_caption,
    _selected_tables,
    pdf_table_grids,
)
from app.storage.filestore import FileStore  # noqa: E402

GOLD = REPO / "seed" / "gold"
CACHE = pathlib.Path(os.environ.get("DOCUMENT_CACHE", "/tmp/gold-documents"))
TOLERANCE = 0.51  # the issuers' own independent per-period rounding

# Gold names the issuer as it appears on the filing. EDGAR is asked by ticker,
# which is exact - resolving by company name is substring-matched against the
# registrant title, so it both misses on punctuation and can land on the wrong
# company entirely.
TICKER = {
    "United Therapeutics": "UTHR",
    "Gilead": "GILD",
    "Johnson & Johnson": "JNJ",
    "Actelion/J&J": "JNJ",
    "Merck": "MRK",
    "Liquidia": "LQDA",
}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
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
        blocks, _grids = pdf_table_grids(raw)
        return "\n".join(blocks)[:4000]
    markup = raw.decode("utf-8", "ignore")
    head = markup.lstrip()[:256].lower()
    parser = "lxml-xml" if head.startswith(("<?xml", "<xbrl", "<ix:")) else "lxml"
    soup = BeautifulSoup(markup, parser)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)[:4000]


def tables_of(
    path: pathlib.Path, *, capped: bool = True
) -> tuple[list[list[list[str]]], list[list[list[str | None]]]]:
    """The document as the pipeline sees it - same reader, same limits.

    Returns the tables as ragged rows and as rectangles, in the same order and
    from the same reading, which is how the pipeline itself carries them.

    The capped reading is ``html_table_grids`` itself rather than a copy of it
    here, because a copy is how "same reader, same limits" stops being true:
    while this function selected tables by position, it went on reporting the
    pipeline's number after the pipeline had stopped selecting that way.

    ``capped=False`` keeps every table in the document, selecting nothing. It
    is not the headline measurement; it exists to price the selection, because
    a Gilead 8-K exhibit holds 39 tables and prints its PRODUCT SALES SUMMARY
    in the thirty-seventh.
    """
    raw = path.read_bytes()
    if path.suffix == ".pdf":
        _blocks, grids = pdf_table_grids(raw)
        return [rows for grid in grids if (rows := flatten_grid(grid))], grids, []
    markup = raw.decode("utf-8", "ignore")
    head = markup.lstrip()[:256].lower()
    parser = "lxml-xml" if head.startswith(("<?xml", "<xbrl", "<ix:")) else "lxml"
    soup = BeautifulSoup(markup, parser)
    if capped:
        selected = _selected_tables(soup)
    else:
        selected = [(table, grid) for table in soup.find_all("table")
                    if (grid := html_table_grid(table))]
    grids = [grid for _element, grid in selected]
    captions = [table_caption(element) for element, _grid in selected]
    return [rows for grid in grids if (rows := flatten_grid(grid))], grids, captions


class LocalCacheStore(FileStore):
    """Whatever the connector downloads is kept beside the run, not in S3.

    Held gzipped. A full-corpus run walks every issuer and quarter in gold and
    keeps each filing it reads, and stored raw that came to 30GB - which is the
    whole of this environment's writable allowance, so the run died of a full
    disk partway through rather than of anything to do with extraction. Filings
    are HTML and inline XBRL, which is the most compressible thing there is;
    the same corpus costs a few gigabytes this way.

    Plain files still read, so a cache written before this stays usable.
    """

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> pathlib.Path:
        return self.root / (key.replace("/", "_") + ".gz")

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.write_bytes(gzip.compress(data, compresslevel=6))
        return str(path)

    async def get(self, key: str) -> bytes:
        path = pathlib.Path(key)
        if not path.exists():
            path = self._path(key)
        if not path.exists():  # a cache written before this class compressed
            path = self.root / key.replace("/", "_")
        raw = path.read_bytes()
        return gzip.decompress(raw) if path.suffix == ".gz" else raw

    async def exists(self, key: str) -> bool:
        return (
            self._path(key).exists()
            or (self.root / key.replace("/", "_")).exists()
            or pathlib.Path(key).exists()
        )

    def public_uri(self, key: str) -> str:
        return f"file://{key}"


async def discover_and_read(row: dict, store: "LocalCacheStore") -> list[dict]:
    """Let the pipeline find its own filings for this product and quarter.

    Gold contributes the product, the issuer and the quarter. Everything after
    that - which filings exist, which are earnings exhibits, what they say - is
    the pipeline's own work, which is the part a handed-over URL skips.
    """
    import datetime as _dt

    ticker = TICKER.get(row["manufacturer"])
    year, quarter = int(row["period"][:4]), int(row["period"][-1])
    month, day = QUARTER_END[quarter]
    end = _dt.date(year, month, day)
    connector = SECConnector(store)
    sources = await connector.retrieve(
        run_id="discover", job_id="discover", cik=None, ticker=ticker,
        company_name=None if ticker else row["manufacturer"],
        include_primary=False, include_earnings=True,
        earnings_since=end + _dt.timedelta(days=5),
        earnings_until=end + _dt.timedelta(days=120),
    )
    parser = DocumentParser(store)
    candidates: list[dict] = []
    readable = 0
    for source in sources:
        if source.retrieval_status.value not in {"success", "partial"}:
            continue
        doc = await parser.parse(source)
        if doc.parsing_status.value != "success" or not doc.tables:
            continue
        readable += 1
        found, _findings, _skipped = extract_revenue_candidates(
            doc.tables, product=row["drug_name"], generic=row.get("generic_name"),
            context=doc.full_text[:4000], grids=doc.table_grids, captions=doc.table_captions,
        )
        candidates.extend(found)
    return candidates, readable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--since", default="",
                    help="only rows from this period onward, e.g. 2024Q1. The "
                         "SEC connector reads only the recent-submissions "
                         "window, so older quarters are unreachable to it.")
    ap.add_argument(
        "--discover", action="store_true",
        help="the pipeline finds its own filings; gold supplies only the "
             "product, the issuer and the quarter",
    )
    ap.add_argument("--json", default="",
                    help="write one record per scored row, so a run can be "
                         "restricted or diffed without being repeated")
    args = ap.parse_args()

    rows = load_rows()
    if args.since:
        rows = [row for row in rows if row["period"] >= args.since]
    if args.limit:
        rows = rows[: args.limit]
    if args.discover:
        return run_discovery(rows, args)
    # One parse per document, not one per row: a Gilead exhibit backs a dozen
    # products and re-reading it for each would say nothing extra.
    by_document: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_document[row["source_url"]].append(row)

    outcomes: dict[str, list[dict]] = collections.defaultdict(list)
    parsed_cache: dict[str, tuple[list, list]] = {}
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
            tables, grids, captions = parsed_cache[url]
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
                grids=grids,
                captions=captions,
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

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps([
            {"drug_name": row["drug_name"], "manufacturer": row["manufacturer"],
             "period": row["period"], "state": state,
             "value_normalized_usd_millions": row.get("value_normalized_usd_millions")}
            for state, group in outcomes.items() for row in group
        ]))

    scored = len(rows) - len(outcomes["no_document"])
    read = len(outcomes["read"])
    print("DIAGNOSTIC: the pipeline is handed the document, so this measures")
    print("reading and not sourcing. Run --discover for the pipeline's score.")
    print()
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

    # Per issuer, because one blocked issuer can hide another that works.
    per_issuer: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for name in ("read", "wrong_value", "not_found", "unreadable_document"):
        for row in outcomes[name]:
            tally = per_issuer[row["manufacturer"]]
            tally[1] += 1
            if name == "read":
                tally[0] += 1
    print("\nby issuer")
    for issuer, (ok, total) in sorted(per_issuer.items(), key=lambda kv: -kv[1][1]):
        print(f"   {issuer:<24}{ok:>5}/{total:<6}{ok / total:7.1%}")

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


def run_discovery(rows: list[dict], args) -> int:
    """Score the pipeline when it has to locate the filing itself."""
    import asyncio

    store = LocalCacheStore(pathlib.Path(os.environ.get("DISCOVER_CACHE", "/tmp/discovered")))
    outcomes: dict[str, list[dict]] = collections.defaultdict(list)

    async def go() -> None:
        for row in rows:
            try:
                candidates, readable = await discover_and_read(row, store)
            except Exception as exc:
                outcomes["error"].append({**row, "why": f"{type(exc).__name__}: {exc}"})
                continue
            if not readable:
                # Nothing readable came back. That is not the same as nothing
                # coming back, and reading it as the latter cost real time: an
                # issuer whose press release is prose and whose schedules are a
                # separate exhibit lands here when only the prose is fetched,
                # which looks like a connector finding no filing and is a
                # connector discarding the exhibit with the numbers in it.
                outcomes["no_readable_document"].append(row)
                continue
            state, value = "not_found", None
            same = [c for c in candidates if str(c.get("period")) == row["period"]]
            target = row["value_normalized_usd_millions"]
            if same:
                values = [float(c["value_normalized_usd_millions"]) for c in same
                          if c.get("value_normalized_usd_millions") is not None]
                if any(abs(v - target) <= TOLERANCE for v in values):
                    state, value = "read", target
                elif values:
                    state = "wrong_value"
                    value = min(values, key=lambda v: abs(v - target))
            outcomes[state].append({**row, "read": value})

    asyncio.run(go())
    scored = len(rows)
    read = len(outcomes["read"])
    print("pipeline finds its own filings - gold supplies product, issuer and quarter only")
    print(f"  rows tested        {scored}")
    print(f"  read correctly     {read}/{scored}  {read / max(scored, 1):.2%}")
    if outcomes["no_readable_document"]:
        print(f"  no readable document {len(outcomes['no_readable_document'])}"
              "   <- filings were found; none of them parsed to a table")
    for name in ("wrong_value", "not_found", "error"):
        if outcomes[name]:
            print(f"  {name:<18} {len(outcomes[name])}")
    if outcomes["error"]:
        reasons = collections.Counter(r["why"].split(":")[0] for r in outcomes["error"])
        print("  error kinds:", dict(reasons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
