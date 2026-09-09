"""Tagged facts read in bulk, from the Commission's own quarterly extracts.

`xbrl.py` reads one instance at a time, which is what the pipeline can do for a
filing it has already fetched. The same facts are also published in bulk: the
**Financial Statement and Notes Data Sets** carry "the text and detailed numeric
information from all financial statements *and their notes*", monthly, for every
filer since 2009, free.

The distinction between the two DERA products is the whole reason this module
exists. The plain Financial Statement Data Sets carry only the face financials -
balance sheet, income statement, cash flows - and a product's revenue is not
there. It is a note disclosure, tagged under `srt:ProductOrServiceAxis`, and the
notes sets are where the notes are. They ship a `DIM` table that is exactly the
missing half: "all of the combinations of XBRL axis and member used to tag any
submission".

What this does *not* replace is the document readers. An 8-K earnings exhibit
carries no XBRL at all - United Therapeutics' 8-K of 31 July 2026 appears in
these files with zero numeric facts - so a quarter whose only source is an
earnings release is still a reading problem. And the sets begin in 2009, so
nothing before that is here at any price.

The rows are turned back into `xbrl.Fact` objects rather than into a parallel
representation, so every rule that already decides what a product fact means -
the standard-taxonomy element list, the worldwide-is-the-absence-of-geography
rule, the least-qualified-statement rule, the midpoint period label - applies
here unchanged and cannot drift from the instance path.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

from app.parsing.xbrl import Fact

# DIM.segments drops namespaces and truncates the leading "Statement" and the
# trailing "Axis"/"Member"/"Domain", so "srt:StatementGeographicalAxis" arrives
# as "Geographical". Mapped back to the spellings xbrl.py matches on, so the
# geography rule there reads these facts the same way it reads an instance.
_AXIS_SPELLINGS = {
    "ProductOrService": "srt:ProductOrServiceAxis",
    "Geographical": "srt:StatementGeographicalAxis",
}

# num.tsv is hundreds of megabytes and a single cell holds a footnote, so the
# reader needs a field size well above the csv default.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


@dataclass(frozen=True)
class Submission:
    """One filing, as the submissions table describes it."""

    adsh: str
    cik: int
    name: str
    form: str
    period: str
    filed: str

    @property
    def url(self) -> str:
        """Where the filing this fact was tagged in can be read."""
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/"
            f"{self.adsh.replace('-', '')}/"
        )


def _segments_to_members(segments: str) -> dict[str, str]:
    """The `{axis}={member};` pairs DIM stores, as xbrl.py spells them."""
    members: dict[str, str] = {}
    for pair in (segments or "").split(";"):
        if "=" not in pair:
            continue
        axis, _, member = pair.partition("=")
        axis, member = axis.strip(), member.strip()
        if not axis or not member:
            continue
        members[_AXIS_SPELLINGS.get(axis, f"{axis}Axis")] = member
    return members


def _period_bounds(ddate: str, qtrs: int) -> tuple[date | None, date | None]:
    """Start and end dates for a duration stated as a count of quarters.

    `ddate` is the period end rounded to the nearest month end, which is the
    Commission doing the 52/53-week correction for us: Johnson & Johnson's
    fiscal year ending 1 January arrives here as 31 December, on the year it
    belongs to.
    """
    try:
        end = date(int(ddate[0:4]), int(ddate[4:6]), int(ddate[6:8]))
    except (ValueError, IndexError):
        return None, None
    if qtrs <= 0:
        return None, end
    months = qtrs * 3
    # `ddate` is always a month end, so the period starts on the first of the
    # month `months - 1` earlier: a quarter ending 30 June starts 1 April.
    year, month = end.year, end.month - (months - 1)
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1), end


def load_dimensions(root: Path) -> dict[str, str]:
    """`dimhash` to the raw segments string, for the whole quarter's filings."""
    dims: dict[str, str] = {}
    with (root / "dim.tsv").open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            dims[row["dimhash"]] = row.get("segments") or ""
    return dims


def load_submissions(
    root: Path, *, ciks: set[int] | None = None, forms: set[str] | None = None
) -> dict[str, Submission]:
    """The filings in this extract, optionally narrowed to some filers."""
    out: dict[str, Submission] = {}
    with (root / "sub.tsv").open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                cik = int(row["cik"])
            except (KeyError, ValueError):
                continue
            if ciks is not None and cik not in ciks:
                continue
            form = (row.get("form") or "").strip()
            if forms is not None and form not in forms:
                continue
            out[row["adsh"]] = Submission(
                adsh=row["adsh"],
                cik=cik,
                name=(row.get("name") or "").strip(),
                form=form,
                period=(row.get("period") or "").strip(),
                filed=(row.get("filed") or "").strip(),
            )
    return out


def iter_facts(
    root: Path,
    *,
    submissions: dict[str, Submission],
    dimensions: dict[str, str] | None = None,
) -> Iterator[tuple[str, Fact]]:
    """(accession, fact) for every numeric row belonging to `submissions`.

    The element keeps the filer's own namespace, so an extension the taxonomy
    has no name for stays visibly an extension and is refused downstream by the
    same list that refuses it when read from an instance.
    """
    dims = dimensions if dimensions is not None else load_dimensions(root)
    with (root / "num.tsv").open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            adsh = row.get("adsh") or ""
            if adsh not in submissions:
                continue
            raw_value = row.get("value")
            if raw_value is None or raw_value == "":
                continue
            try:
                value = float(raw_value)
                qtrs = int(row.get("qtrs") or 0)
            except ValueError:
                continue
            version = (row.get("version") or "").strip()
            tag = (row.get("tag") or "").strip()
            # A standard tag's version is the taxonomy ("us-gaap/2025"); an
            # extension's is the accession that defined it.
            namespace = version.split("/")[0] if "/" in version else version
            start, end = _period_bounds(row.get("ddate") or "", qtrs)
            yield adsh, Fact(
                element=f"{namespace}:{tag}" if namespace else tag,
                value=value,
                members=_segments_to_members(dims.get(row.get("dimh") or "", "")),
                start=start,
                end=end,
                unit=(row.get("uom") or "").strip() or None,
                decimals=None,
                context_id=f"{adsh}:{row.get('dimh') or ''}",
            )
