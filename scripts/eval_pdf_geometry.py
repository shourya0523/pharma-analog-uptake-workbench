"""Does reading a PDF's geometry recover what the same document's markup states?

A PDF states no structure. Its columns exist because the numbers line up on the
page, and recovering them is a reconstruction - the one place in this pipeline
where a value's period rests on inference from coordinates rather than on
something the document declared.

So it needs a gate, and the gate cannot be gold: gold's PDFs are one issuer, and
a reader tuned until that issuer reads is a reader fitted to it. EDGAR carries
no PDF earnings exhibits to hold out - checked across sixteen filers, none files
one - so the held-out corpus is made instead: the four issuers' HTML exhibits,
which appear nowhere in gold, rendered to PDF by a browser and read back.

The invariant is exact and needs no answer key. One document, read two ways,
must say the same thing. Where the markup says a figure belongs to a period, the
geometry must put it there; where the two disagree, the geometry is wrong,
because the markup is the document's own account of itself.

    HOLDOUT_DIR=/tmp/holdout python scripts/eval_pdf_geometry.py --render
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bs4 import BeautifulSoup  # noqa: E402

from app.extraction.extract import (  # noqa: E402
    map_values_to_blocks,
    read_values_by_column,
    tokenize_row,
)
from app.extraction.fingerprint import build_fingerprint  # noqa: E402
from app.parsing.documents import (  # noqa: E402
    flatten_grid,
    html_table_grids,
    pdf_table_grids,
)
from app.parsing.tables import clean_label  # noqa: E402

WORK = pathlib.Path(os.environ.get("HOLDOUT_DIR", "/tmp/holdout"))
RENDERED = WORK / "rendered"
CHROME = pathlib.Path(
    os.environ.get("CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
)


def render(source: pathlib.Path, target: pathlib.Path) -> bool:
    """Print the document as a browser would, which is what a filer's PDF is."""
    if target.exists() and target.stat().st_size > 0:
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(CHROME), "--headless", "--disable-gpu", "--no-sandbox",
            "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000",
            "--no-pdf-header-footer", f"--print-to-pdf={target}", f"file://{source}",
        ],
        capture_output=True, timeout=180, check=False,
    )
    return result.returncode == 0 and target.exists()


def rows_of(grid: list[list[str | None]]) -> dict[str, dict[str, float]]:
    """Every labelled row's figures, by the period the table gives each column."""
    ragged = flatten_grid(grid)
    if not ragged:
        return {}
    fingerprint = build_fingerprint(ragged, "", grid=grid)
    if not fingerprint.blocks:
        return {}
    periods = {block.value_index: block.period for block in fingerprint.blocks}
    found: dict[str, dict[str, float]] = {}
    source = grid if fingerprint.by_column else [list(row) for row in ragged]
    for row in source:
        cells = [(index, cell) for index, cell in enumerate(row) if cell is not None]
        if not cells:
            continue
        label = clean_label(cells[0][1])
        if not label or len(label) < 3:
            continue
        if fingerprint.by_column:
            assigned, _reason = read_values_by_column(row, fingerprint.blocks)
        else:
            assigned, _reason = map_values_to_blocks(
                tokenize_row([cell for _, cell in cells[1:]]), fingerprint.blocks
            )
        if not assigned:
            continue
        by_period = {periods[index]: value for index, value in assigned.items()}
        found.setdefault(label.lower(), {}).update(by_period)
    return found


def reading(grids: list[list[list[str | None]]]) -> dict[str, dict[str, float]]:
    merged: dict[str, dict[str, float]] = {}
    for grid in grids:
        for label, values in rows_of(grid).items():
            merged.setdefault(label, {}).update(values)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true",
                        help="print the held-out exhibits to PDF first")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    manifest = json.loads((WORK / "manifest.json").read_text())
    if args.limit:
        manifest = manifest[: args.limit]

    tally = collections.Counter()
    disagreements: list[tuple[str, str, str, float, float]] = []
    for entry in manifest:
        source = pathlib.Path(entry["path"])
        target = RENDERED / (source.stem + ".pdf")
        if args.render and not render(source, target):
            tally["render_failed"] += 1
            continue
        if not target.exists():
            tally["not_rendered"] += 1
            continue
        soup = BeautifulSoup(source.read_bytes().decode("utf-8", "ignore"), "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        from_markup = reading(html_table_grids(soup))
        _blocks, grids = pdf_table_grids(target.read_bytes())
        from_geometry = reading(grids)
        tally["documents"] += 1

        shared = set(from_markup) & set(from_geometry)
        tally["labels_in_markup"] += len(from_markup)
        tally["labels_in_geometry"] += len(from_geometry)
        tally["labels_in_both"] += len(shared)
        for label in shared:
            for period, value in from_markup[label].items():
                if period not in from_geometry[label]:
                    tally["period_missing_from_geometry"] += 1
                    continue
                other = from_geometry[label][period]
                if abs(other - value) <= 0.005 * max(1.0, abs(value)):
                    tally["agree"] += 1
                else:
                    tally["disagree"] += 1
                    if len(disagreements) < 15:
                        disagreements.append(
                            (entry["issuer"], label, period, value, other)
                        )

    print(f"held-out documents rendered and read both ways: {tally['documents']}")
    print(f"  row labels   markup {tally['labels_in_markup']}"
          f"   geometry {tally['labels_in_geometry']}"
          f"   in both {tally['labels_in_both']}")
    checked = tally["agree"] + tally["disagree"]
    if checked:
        print(f"  figures the markup dates, read the same way from the page"
              f"   {tally['agree']}/{checked}  {tally['agree'] / checked:.1%}")
    print(f"  periods the geometry did not produce at all: "
          f"{tally['period_missing_from_geometry']}")
    for key in ("render_failed", "not_rendered"):
        if tally[key]:
            print(f"  {key}: {tally[key]}")
    if disagreements:
        print("\ndisagreements - the same row, dated the same, read differently:")
        for issuer, label, period, markup_value, geometry_value in disagreements:
            print(f"   {issuer:<9} {label[:34]:<34} {period}  "
                  f"markup {markup_value:<12g} geometry {geometry_value:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
