"""Check period detection against issuers the gold dataset does not contain.

Every other eval here scores the pipeline on documents gold cites, which makes
it easy to tune detection until those documents pass and call the result an
improvement. This one exists to catch that: it fetches earnings exhibits from
companies that appear nowhere in seed/gold and scores the same function on them.

A change that moves the gold-cited number a lot and this one barely is fitted to
the corpus, not to the problem. That is not hypothetical - a "FIRST QUARTER"
heading reader was written during this work, lifted gold's documents by 18, and
turned out to be worth exactly zero here even though three quarters of these
documents use that phrasing. It was removed on the strength of this number.

    SEC_CONTACT='project you@example.com' \
        python scripts/eval_period_generalization.py --refresh
"""

from __future__ import annotations

import argparse, json, os, pathlib, re, sys, time, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bs4 import BeautifulSoup  # noqa: E402

from app.extraction.fingerprint import build_fingerprint  # noqa: E402
from app.parsing.documents import (  # noqa: E402
    HTML_TABLE_LIMIT,
    flatten_grid,
    html_table_grid,
)
from app.parsing.periods import detect_period_context  # noqa: E402

WORK = pathlib.Path(os.environ.get("HOLDOUT_DIR", "/tmp/holdout"))
DOCS = WORK / "docs"
EARNINGS_MONTHS = {1, 2, 4, 5, 7, 8, 10, 11}
# Large pharmaceutical filers with no product in seed/gold. They are here to be
# unfamiliar, so nothing about their conventions may be encoded anywhere.
HELD_OUT = {"78003": "Pfizer", "1551152": "AbbVie", "318154": "Amgen", "59478": "EliLilly"}


def expected_quarter(filed: str) -> str:
    """The quarter an earnings 8-K filed on this date reports."""
    year, month = int(filed[:4]), int(filed[5:7])
    if month in (1, 2):
        return f"{year - 1}Q4"
    if month in (4, 5):
        return f"{year}Q1"
    if month in (7, 8):
        return f"{year}Q2"
    return f"{year}Q3"


def text_of(path: pathlib.Path) -> str:
    soup = BeautifulSoup(path.read_bytes().decode("utf-8", "ignore"), "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def soup_of(path: pathlib.Path) -> BeautifulSoup:
    soup = BeautifulSoup(path.read_bytes().decode("utf-8", "ignore"), "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup


def score_tables() -> None:
    """The same question for the table reader: does the geometry generalize?

    A table's headings state which period covers which column. Reading that off
    the rectangle is meant to be a property of HTML, not of any issuer's house
    style, so it has to hold on filers nothing here was written against. The
    ragged reading of the same tables is printed beside it: if the rectangle
    only ever helps on the corpus gold cites, it is fitted to that corpus.
    """
    manifest = json.loads((WORK / "manifest.json").read_text())
    flat_periods = geometry_periods = 0
    flat_right = geometry_right = 0
    tables = 0
    for entry in manifest:
        soup = soup_of(pathlib.Path(entry["path"]))
        context = soup.get_text("\n", strip=True)[:4000]
        grids = [
            grid
            for table in soup.find_all("table")[:HTML_TABLE_LIMIT]
            if (grid := html_table_grid(table))
        ]
        for grid in grids:
            rows = flatten_grid(grid)
            if not rows:
                continue
            tables += 1
            flat = build_fingerprint(rows, context)
            geometry = build_fingerprint(rows, context, grid=grid)
            if flat.blocks:
                flat_periods += 1
                flat_right += entry["expected"] in {b.period for b in flat.blocks}
            if geometry.blocks:
                geometry_periods += 1
                geometry_right += entry["expected"] in {b.period for b in geometry.blocks}
    print()
    print(f"held-out tables: {tables}")
    print(f"  periods found     ragged {flat_periods:>4}    geometry {geometry_periods:>4}")
    print(f"  naming the filing's own quarter"
          f"    ragged {flat_right:>4}    geometry {geometry_right:>4}")


def score() -> int:
    manifest = json.loads((WORK / "manifest.json").read_text())
    correct, rows = 0, []
    for entry in manifest:
        context = detect_period_context(text_of(pathlib.Path(entry["path"])))
        label = (
            f"{context.year}Q{context.quarter}"
            if context and context.months == 3
            else (f"{context.months}m{context.year}" if context else "none")
        )
        correct += label == entry["expected"]
        rows.append((entry["issuer"], entry["filed"], entry["expected"], label))
    print(f"held-out earnings exhibits: {len(manifest)} from {len(HELD_OUT)} issuers")
    print(f"  period detected correctly {correct}/{len(manifest)}  "
          f"{correct / max(len(manifest), 1):.0%}")
    print()
    for issuer, filed, want, got in rows:
        mark = "" if got == want else "   <-- wrong"
        print(f"   {issuer:<9}{filed}  expect {want}  got {got}{mark}")
    score_tables()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true",
                        help="re-download the held-out exhibits from EDGAR")
    args = parser.parse_args()
    if args.refresh:
        print("Refresh fetches from EDGAR; see the module docstring for SEC_CONTACT.")
    if not (WORK / "manifest.json").exists():
        raise SystemExit(
            f"No held-out corpus at {WORK}. Fetch it first (see the docstring), "
            "or set HOLDOUT_DIR to where it lives."
        )
    sys.exit(score())
