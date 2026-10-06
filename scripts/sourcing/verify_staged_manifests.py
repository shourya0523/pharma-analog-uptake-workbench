"""Hold every row of a staged manifest up against the document it cites.

A sourcing agent hands back one CSV per product plus a ``.meta.json`` beside
it. Nothing it wrote is trusted: each row's ``source_url`` is fetched and the
row passes only if the document itself prints the row.

What "prints the row" means, per row:

* **fetched** - the URL answers with a document.
* **row_match** - one line of that document carries the row's ``row_label``
  and every figure the quote lists after its first ``|``, in that order. For
  HTML a line is a table row; for a PDF it is a text line, joined with the
  next two because PDF tables wrap.
* **value_in_quote** - for a direct read, the row's own figure is one of the
  figures quoted. A derived row instead has to quote at least two figures,
  because its arithmetic must be visible.
* **year_named** - the document mentions the row's year at all.

Then per series: no duplicate or missing quarter between first and last, and
where a fourth-quarter quote states a full year, the four quarters sum to it
within the rounding of the printed figures.

    python scripts/sourcing/verify_staged_manifests.py STAGING_DIR [--json OUT]

Exit status is non-zero when any row fails, so a batch cannot be integrated
by accident.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

CACHE = Path(os.environ.get("SOURCING_WORKDIR", "/tmp/gold-sourcing")) / "verify-cache"
USER_AGENT = os.environ.get(
    "SEC_CONTACT", "pharma-analog-uptake-workbench research contact@example.com"
)
SCALE = {"thousands": 1000, "millions": 1, "billions": 0.001}
DIRECT = {"direct_reported", "direct_reported_rounded", "direct_prior_year_column",
          "direct_retrospective_table", "direct_prior_year_schedule"}
NUMBER = re.compile(r"\(?-?[\d][\d,]*(?:\.\d+)?\)?")


def fetch(client: httpx.Client, url: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / hashlib.sha256(url.encode()).hexdigest()
    if path.is_file() and path.stat().st_size:
        return path.read_bytes()
    for attempt in range(4):
        response = client.get(url)
        if response.status_code in {403, 429, 503} and attempt < 3:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        break
    path.write_bytes(response.content)
    time.sleep(0.15)
    return response.content


def normalize(text: str) -> str:
    text = text.replace("\xa0", " ").replace("’", "'").replace("®", "").replace("™", "")
    return " ".join(text.split()).lower()


DASH_CELL = re.compile(r"(?:(?<=\|)|^)\s*[—–-]\s*(?=\||$)")


def numbers_in(text: str) -> list[float]:
    """Figures in reading order; a cell that is only a dash is a printed nil."""
    text = DASH_CELL.sub(" 0 ", text)
    out = []
    for token in NUMBER.findall(text):
        negative = token.startswith("(") and token.endswith(")") or token.startswith("-")
        digits = token.strip("()-").replace(",", "")
        if not digits or digits == ".":
            continue
        try:
            value = float(digits)
        except ValueError:
            continue
        out.append(-value if negative else value)
    return out


def document_lines(raw: bytes) -> list[str]:
    """Candidate lines: table rows for HTML, wrapped text lines for PDF."""
    if raw[:5] == b"%PDF-":
        import pdfplumber

        lines: list[str] = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                lines.extend((page.extract_text() or "").splitlines())
        joined = [" ".join(lines[i:i + 3]) for i in range(len(lines))]
        return [normalize(line) for line in lines + joined]
    soup = BeautifulSoup(raw.decode("utf-8", errors="ignore"), "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if cells:
            rows.append(normalize(" | ".join(cells)))
    text_lines = [normalize(t) for t in soup.get_text("\n").splitlines() if t.strip()]
    joined = [" ".join(text_lines[i:i + 3]) for i in range(len(text_lines))]
    return rows + text_lines + joined


def quoted_figures(quote: str) -> list[float]:
    if "|" not in quote:
        return []
    return numbers_in("|" + quote.split("|", 1)[1])


def contains_in_order(haystack: list[float], needles: list[float]) -> bool:
    position = 0
    for needle in needles:
        while position < len(haystack) and abs(haystack[position] - needle) > 1e-9:
            position += 1
        if position == len(haystack):
            return False
        position += 1
    return True


REGION_LABEL = re.compile(
    r"^\s*(?:ww|worldwide|total|u\.s\.|us|intl|international|rest of world|row|ex-u\.s\.)\b\s*\|?",
)


def row_matches(lines: list[str], index: int, label: str, figures: list[float]) -> bool:
    """The labelled line carries the figures, or an unlabelled total line under it does.

    Some issuers print a product as a heading over regional lines: closed by a
    total line with no label of its own, or by lines labelled only with a
    region ("US", "Intl", "WW"). Only such a line may stand in for the
    labelled one, so a neighbouring product's row can never satisfy the match.
    """
    if contains_in_order(numbers_in(lines[index].split(label, 1)[1]), figures):
        return True
    for line in lines[index + 1:index + 6]:
        rest = REGION_LABEL.sub("", line, count=1)
        if not re.search(r"[a-z]", rest) and contains_in_order(numbers_in(rest), figures):
            return True
    return False


SPACE_GROUPED = re.compile(r"(?<=\d)[ \u202f\u2009](?=\d{3}(?!\d))")


def check_row(row: dict, raw: bytes, lines: list[str]) -> dict:
    result = {"fetched": True}
    label = normalize(row.get("row_label") or "")
    quote = row["source_quote"]
    # Some issuers group thousands with a space ("1 109"). Only when the quote
    # itself is written that way are the document's lines read the same way,
    # so two adjacent single-figure cells elsewhere are never run together.
    if SPACE_GROUPED.search(quote.split("|", 1)[-1]):
        quote = quote.split("|", 1)[0] + "|" + SPACE_GROUPED.sub("", quote.split("|", 1)[1])
        lines = [SPACE_GROUPED.sub("", line) for line in lines]
    figures = quoted_figures(quote)
    result["row_match"] = bool(label and figures) and any(
        row_matches(lines, index, label, figures)
        for index, line in enumerate(lines)
        if label in line
    )
    reported = float(row.get("source_value_reported") or row["value_reported"])
    if row["derivation"] in DIRECT:
        result["value_in_quote"] = any(abs(f - reported) < 1e-6 for f in figures)
    else:
        result["value_in_quote"] = len(figures) >= 2
    # The figure as printed, in its printed unit, has to be the recorded
    # millions: a derived row that stores an input there instead of its own
    # result would otherwise pass every other check.
    scale = SCALE.get(row.get("source_unit") or "millions", 1)
    result["unit_consistent"] = abs(reported / scale - float(row["value_reported"])) <= 1e-6 * max(1.0, abs(float(row["value_reported"])))
    year = row["period"][:4]
    result["year_named"] = year in raw.decode("utf-8", errors="ignore") or any(year in l for l in lines)
    return result


def quarters_between(first: str, last: str) -> list[str]:
    year, quarter = int(first[:4]), int(first[-1])
    out = []
    while f"{year}Q{quarter}" <= last:
        out.append(f"{year}Q{quarter}")
        quarter += 1
        if quarter == 5:
            year, quarter = year + 1, 1
    return out


def decimals(text: str) -> int:
    return len(text.split(".", 1)[1]) if "." in text else 0


def check_series(rows: list[dict]) -> list[str]:
    problems = []
    periods = [r["period"] for r in rows]
    for period in {p for p in periods if periods.count(p) > 1}:
        problems.append(f"duplicate quarter {period}")
    if periods:
        missing = sorted(set(quarters_between(min(periods), max(periods))) - set(periods))
        if missing:
            problems.append(f"missing quarters {', '.join(missing)}")
    by_period = {r["period"]: r for r in rows}
    for row in rows:
        if row["period"][-2:] != "Q4" or row["derivation"] not in DIRECT:
            continue
        figures = quoted_figures(SPACE_GROUPED.sub("", row["source_quote"]))
        if len(figures) < 4 or "twelve months" not in row["source_quote"].lower() and "full year" not in row["source_quote"].lower():
            continue
        # The quote ends "quarter | prior quarter | year | prior year"; any
        # header figures (column years) come before those four.
        stated = figures[-2]
        year = row["period"][:4]
        quarters = [by_period.get(f"{year}Q{q}") for q in range(1, 5)]
        if None in quarters:
            continue
        # Compared in millions: a year can mix thousands and millions, so the
        # printed figures are only summable after each row's own scaling.
        total = sum(float(q["value_reported"]) for q in quarters)
        stated = stated / SCALE.get(row.get("source_unit") or "millions", 1)
        places = max(decimals(str(q["value_reported"])) for q in quarters)
        tolerance = 4 * 0.5 * 10 ** -places + 1e-9
        if abs(total - stated) > max(tolerance, abs(stated) * 0.002):
            problems.append(f"{year}: quarters sum to {total:g}, Q4 quote states full year {stated:g}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("staging", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    report: dict = {"products": {}}
    failed = 0
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=90)
    documents: dict[str, tuple[bytes, list[str]] | None] = {}
    for manifest in sorted(args.staging.glob("*_quarterly.csv")):
        rows = list(csv.DictReader(manifest.open(newline="")))
        product: dict = {"rows": {}, "series_problems": check_series(rows)}
        failed += len(product["series_problems"])
        for row in rows:
            url = row["source_url"]
            if url not in documents:
                try:
                    raw = fetch(client, url)
                    documents[url] = (raw, document_lines(raw))
                except Exception as exc:  # noqa: BLE001 - every failure is a finding
                    print(f"FETCH FAIL {url}: {exc}", file=sys.stderr)
                    documents[url] = None
            doc = documents[url]
            result = {"fetched": False} if doc is None else check_row(row, *doc)
            result["ok"] = all(result.values())
            if not result["ok"]:
                failed += 1
            product["rows"][row["period"]] = result
        bad = {p: r for p, r in product["rows"].items() if not r["ok"]}
        print(f"{manifest.name}: {len(rows)} rows, {len(rows) - len(bad)} pass, "
              f"{len(bad)} fail, series problems: {product['series_problems'] or 'none'}")
        for period, result in sorted(bad.items()):
            print(f"    {period}: " + ", ".join(k for k, v in result.items() if not v and k != "ok"))
        report["products"][manifest.name] = product
    client.close()
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
    print(f"TOTAL FAILURES: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
