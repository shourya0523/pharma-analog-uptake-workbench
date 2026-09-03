"""Independent audit of seed/gold, written to distrust the rest of the repo.

The test suite checks the properties someone thought to assert. This checks a
different list, chosen from the defects that have actually shipped into this
dataset and survived a green test run:

* a row labelled ``direct_reported`` whose citation is a different quarter's
  document (Opsumit 2020Q2 carried a stale label for three commits)
* a quote that restates the row's own value instead of quoting a document
  (six Remodulin fourth quarters did, for months)
* a value recorded to more decimals than its inputs justify
* a year whose quarters do not reconcile against a published total
* two products sharing a benchmark identity, or one product silently changing
  scope, currency or geography mid-series
* a manifest row that never became a gold row, or the reverse

Nothing here imports the builder or the test helpers: it reads the published
artifacts and the manifests as a stranger would. Every finding prints the
evidence, because an audit that says only "3 problems" cannot be acted on.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD = REPO_ROOT / "seed" / "gold"
MANIFESTS = GOLD / "source_manifests"

# A quarter's own document should mention that quarter's year. A citation to a
# later filing is legitimate (prior-year columns, retrospective tables) but has
# to say so in the derivation rather than claiming to be a direct read.
_DIRECT_READ = {"direct_reported", "direct_reported_rounded"}
_LATER_FILING_OK = {
    "direct_prior_year_column",
    "direct_prior_year_schedule",
    "direct_retrospective_table",
    "direct_jnj_retrospective_table",
    "annual_less_reported_first_nine_months",
    "full_year_less_other_reported_quarters",
    "year_to_date_less_reported_quarters",
    "identity_normalization_pre_dpi",
    "acquisition_bridge_sum",
}


_YEAR_PATTERNS = (
    # A path segment that is exactly a year: .../2019/q4/...
    re.compile(r"/((?:19|20)\d{2})/"),
    # A year glued to a quarter marker: 4Q2018, 2Q25, mrk-20240630, -1q19-
    re.compile(r"[qQ][1-4][-_]?((?:19|20)\d{2})"),
    re.compile(r"((?:19|20)\d{2})[-_]?[qQ][1-4]"),
    # A full date stamp: mrk-20251231, gild-20180630
    re.compile(r"-((?:19|20)\d{2})(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\b"),
    # A spelled-out year in a slug: ...-full-year-2018-financial-results
    re.compile(r"[-]((?:19|20)\d{2})[-.]"),
)


def years_named_by(url: str) -> set[int]:
    """Years the URL actually names, not digit runs that happen to look like one.

    SEC accession numbers are eighteen digits and contain four-digit runs by
    the dozen: 000110465917 alone yields 1046 and 6591. A naive scan reports
    every United Therapeutics row as citing a document from 1902. Only
    date-shaped positions count - a path segment, a quarter marker, a date
    stamp, or a year in a slug.
    """
    found: set[int] = set()
    for pattern in _YEAR_PATTERNS:
        found.update(int(match) for match in pattern.findall(url))
    return {year for year in found if 1990 <= year <= 2035}


def load(name: str) -> list[dict]:
    path = GOLD / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def finding(bucket: list[str], message: str) -> None:
    bucket.append(message)


def audit_derivation_labels(quarterly: list[dict]) -> list[str]:
    """A direct read must cite a document from its own period.

    The check is deliberately crude - it looks for the row's year in the URL -
    because the failure it catches is crude: a row that was re-sourced to a new
    document and kept the old label, or kept the old URL. It skips issuers
    whose URLs carry no year at all.
    """
    out: list[str] = []
    for row in quarterly:
        if row["derivation"] not in _DIRECT_READ:
            if row["derivation"] not in _LATER_FILING_OK:
                finding(out, f"{row['gold_id']}: unknown derivation {row['derivation']!r}")
            continue
        url, year = row["source_url"], row["calendar_year"]
        years_in_url = years_named_by(url)
        if not years_in_url:
            continue
        # A quarter is cited by its own filing, or by the release that reports
        # it, which for a fourth quarter is published the following year.
        if not years_in_url & {year, year + 1}:
            finding(
                out,
                f"{row['gold_id']}: labelled {row['derivation']} but cites a "
                f"document for {sorted(years_in_url)}, not {year}",
            )
    return out


_DERIVATION_LANGUAGE = re.compile(
    r"\b(yields?|less the|after subtracting|gives|equals|derived)\b", re.I
)


def audit_self_referential_quotes(quarterly: list[dict]) -> list[str]:
    """A derived row must show its inputs, not restate its own answer.

    The defect this catches, verbatim from a row that shipped: "Remodulin
    annual sales less first-nine-month sales yields fourth quarter 2008
    $73.137 million." It reads like a citation, names an operation, and
    contains exactly one number - the one being asserted. Nothing in it can be
    checked.

    A *directly read* row quoting a one-figure sentence is fine and common
    ("Remodulin net product sales: first quarter 2003 $8.546 million"): the
    document says that, and the row says what the document says. Only derived
    rows are held to showing their arithmetic.
    """
    out: list[str] = []
    for row in quarterly:
        if row["derivation"] in _DIRECT_READ:
            continue
        quote = row.get("source_quote") or ""
        if not _DERIVATION_LANGUAGE.search(quote):
            continue
        numbers = {
            float(n.replace(",", ""))
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", quote)
        }
        numbers -= {float(y) for y in range(1990, 2031)}
        if numbers <= {round(float(row["value_reported"]), 6)}:
            finding(
                out,
                f"{row['gold_id']}: derived quote states an operation but shows no "
                f"inputs - {quote[:100]!r}",
            )
    return out


def audit_precision(quarterly: list[dict]) -> list[str]:
    """A value cannot be more precise than the inputs it came from."""
    out: list[str] = []
    for row in quarterly:
        if row.get("precision") != "exact":
            # An "approximate" row may still carry decimals: Remodulin's first
            # quarter on sale is 0.205 because the filing prints "$205,000" and
            # the series is denominated in millions. The decimals are a unit
            # conversion, not a precision claim.
            continue
        value = float(row["value_reported"])
        quote = row.get("source_quote") or ""
        quoted = [
            float(n.replace(",", ""))
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", quote)
        ]
        decimals = len(str(value).split(".")[1].rstrip("0")) if "." in str(value) else 0
        if decimals == 0:
            continue
        best = max((len(str(q).split(".")[1].rstrip("0")) for q in quoted if "." in str(q)), default=0)
        if decimals > best:
            finding(
                out,
                f"{row['gold_id']}: claims exact to {decimals} decimals but its "
                f"quote carries at most {best} - {value}",
            )
    return out


# A fourth-quarter row whose quote carries four value columns states, in its
# third column, the full year the issuer published - that is what a quarterly
# exhibit's year-to-date block is in Q4. So the total a year has to reconcile
# to is already inside the citation, and does not need a separate annual row.
_LEGEND = re.compile(r"\([^)]*(?:Q[1-4]\s*\d{4}|\d{4}\s*Q[1-4]|columns are)[^)]*\)")


def stated_full_year_from_q4_quote(row: dict) -> float | None:
    """The issuer's own full-year figure, read out of a Q4 row's own quote.

    Only for rows whose columns are known to be
    [quarter, prior-year quarter, year-to-date, prior-year year-to-date]:
    a direct fourth-quarter reading with exactly four values and no column
    legend redefining what the columns are. Anything else returns None rather
    than guessing, because reading the wrong column would invent a total and
    then check the year against it.

    The quote is in whatever unit the document used - United Therapeutics
    states thousands, J&J and Gilead state millions - so the scale is learned
    from the row itself: the first column is this row's own quarter, and the
    row records that quarter in USD millions. If the ratio between them is not
    a clean power of ten, the first column is not the quarter this row claims
    and nothing further should be read off the line.
    """
    if row.get("derivation") != "direct_reported" or row.get("calendar_quarter") != 4:
        return None
    quote = row.get("source_quote") or ""
    if _LEGEND.search(quote):
        return None
    cells = [cell.strip() for cell in quote.split("|")]
    values = [cell for cell in cells[1:] if re.fullmatch(r"[\d,]+", cell)]
    if len(values) != 4 or len(cells) - 1 != 4:
        return None
    quarter, _prior, year_to_date, _prior_ytd = (float(v.replace(",", "")) for v in values)
    reported = row.get("value_normalized_usd_millions")
    if not quarter or reported is None:
        return None
    scale = reported / quarter
    if not any(abs(scale - power) < power * 1e-6 for power in (1e-6, 1e-3, 1.0, 1e3)):
        return None
    # The year-to-date column must at least contain the quarter it ends with.
    # If it does not, the columns are not what this function assumes.
    if year_to_date < quarter:
        return None
    return year_to_date * scale


# Periods a source URL announces about itself. Issuers name the quarter in the
# path or the filename, which makes a citation checkable against the row it is
# attached to without opening it.
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_URL_PERIOD_PATTERNS = (
    # J&J: .../doc_financials/2019/q2/Sales-of-Key-Products-...
    re.compile(r"/doc_financials/(?P<year>\d{4})/q(?P<quarter>[1-4])/"),
    # Gilead: .../2020/gilead-sciences-announces-fourth-quarter-and-full-year-2019-...
    re.compile(
        r"announces-(?P<ordinal>first|second|third|fourth)-quarter-and-full-year-"
        r"(?P<year>\d{4})-"
    ),
    # Gilead: .../2019/gilead-sciences-announces-second-quarter-2019-financial-results
    re.compile(
        r"announces-(?P<ordinal>first|second|third|fourth)-quarter-(?P<year>\d{4})-"
    ),
    # Merck: .../4Q25-Merck-Other-Financial-Disclosures.pdf
    re.compile(r"/(?P<quarter>[1-4])Q(?P<yy>\d{2})-"),
)


def period_a_url_announces(url: str) -> str | None:
    """The quarter a source URL says it covers, or None if it does not say."""
    for pattern in _URL_PERIOD_PATTERNS:
        match = pattern.search(url)
        if not match:
            continue
        parts = match.groupdict()
        quarter = (
            int(parts["quarter"])
            if parts.get("quarter")
            else _ORDINALS[parts["ordinal"]]
        )
        year = int(parts["year"]) if parts.get("year") else 2000 + int(parts["yy"])
        return f"{year}Q{quarter}"
    return None


def audit_citation_period(rows: list[dict]) -> list[str]:
    """A directly-read row has to cite the document for its own quarter.

    Only for ``direct_reported``: a figure taken from a later release's
    prior-year column cites that later release on purpose, and a derived figure
    cites whichever document states the total it was derived from. Both say so
    in their derivation, and both are excluded here rather than explained away.

    This exists because manifests are generated from a period-to-URL mapping.
    A mistake there is not a typo in one row - it is the same mistake in every
    row of a series, which is exactly the kind of error that looks like data.
    """
    out: list[str] = []
    checked = 0
    for row in rows:
        if row.get("derivation") != "direct_reported":
            continue
        announced = period_a_url_announces(row.get("source_url") or "")
        if announced is None:
            continue
        checked += 1
        if announced != row["period"]:
            finding(
                out,
                f"{row['drug_name']} {row['period']}: cited document announces "
                f"{announced}",
            )
    if not checked:
        finding(out, "no citation announced a period; this check is doing nothing")
    return out


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def audit_value_appears_in_its_quote(rows: list[dict]) -> list[str]:
    """The figure a row records has to be a figure its citation contains.

    The most basic property provenance has, and the audit was not checking it:
    a row could claim 2,701 while quoting a line that says 2,071 and every
    check here would pass. The builder's own test asserts this, but the point
    of this script is to read the published artifacts as a stranger who does
    not have the builder - so it has to be asserted twice, independently.

    Compared in the unit the document used (source_unit), because that is the
    unit the quote is written in.
    """
    out: list[str] = []
    for row in rows:
        expected = row.get("source_value_reported")
        quote = row.get("source_quote") or ""
        if expected is None or not quote:
            finding(out, f"{row['drug_name']} {row['period']}: no citation to check")
            continue
        found = [
            float(token.replace(",", "")) for token in _NUMBER.findall(quote)
        ]
        if not any(abs(value - float(expected)) < 1e-6 for value in found):
            finding(
                out,
                f"{row['drug_name']} {row['period']}: records {expected:g} "
                f"({row.get('source_unit')}), which does not appear in its own quote",
            )
    return out


def audit_year_reconciliation(quarterly: list[dict], annual: list[dict]) -> list[str]:
    """Every complete year against a published total for the same product.

    Two sources of "published total", because relying on the annual file alone
    made this check skip every product that has no annual row - which was 373
    of 919 quarters, silently. The second source is the Q4 citation itself: a
    quarterly exhibit's fourth-quarter year-to-date column *is* the issuer's
    stated full year, so the total is already inside the evidence.

    Normalised USD on both sides. Comparing as-reported figures across a
    currency boundary is the false positive this check exists to avoid.
    """
    out: list[str] = []
    totals = {
        (row["drug_name"], str(row["period"])): row["value_normalized_usd_millions"]
        for row in annual
        if row.get("value_normalized_usd_millions") is not None
    }
    from_quote: dict[tuple[str, int], float] = {}
    by_year: dict[tuple[str, int], dict[str, float]] = defaultdict(dict)
    for row in quarterly:
        usd = row.get("value_normalized_usd_millions")
        if usd is not None:
            by_year[(row["drug_name"], row["calendar_year"])][row["period"]] = usd
        if row.get("currency") == "USD" and row.get("unit") == "millions":
            stated = stated_full_year_from_q4_quote(row)
            if stated is not None:
                from_quote[(row["drug_name"], row["calendar_year"])] = stated

    for (drug, year), quarters in sorted(by_year.items()):
        if len(quarters) != 4:
            continue
        stated = totals.get((drug, str(year)))
        source = "a published annual figure of"
        if stated is None:
            stated = from_quote.get((drug, year))
            source = (
                "the fourth-quarter year-to-date column of its own citation, which "
                "states"
            )
        if stated is None:
            continue
        gap = abs(sum(quarters.values()) - stated)
        if gap > 2.5:
            finding(
                out,
                f"{drug} {year}: quarters sum to {sum(quarters.values()):g} against "
                f"{source} {stated:g} (off by {gap:g})",
            )
    return out


def audit_series_consistency(quarterly: list[dict]) -> list[str]:
    """One product, one identity, one scope, one currency - or say why."""
    out: list[str] = []
    attributes = defaultdict(lambda: defaultdict(set))
    for row in quarterly:
        for field in ("benchmark_identity", "revenue_scope", "geography", "currency", "unit"):
            attributes[row["drug_name"]][field].add(row[field])
    for drug, fields in sorted(attributes.items()):
        for field, values in fields.items():
            if len(values) > 1:
                finding(out, f"{drug}: {field} varies within the series {sorted(values)}")

    identities: dict[str, set[str]] = defaultdict(set)
    for row in quarterly:
        identities[row["benchmark_identity"]].add(row["drug_name"])
    for identity, drugs in sorted(identities.items()):
        if len(drugs) > 1:
            finding(out, f"benchmark identity {identity} shared by {sorted(drugs)}")
    return out


def audit_manifest_round_trip(quarterly: list[dict]) -> list[str]:
    """Every manifest row should have become exactly one gold row."""
    out: list[str] = []
    gold_periods: dict[str, set[str]] = defaultdict(set)
    for row in quarterly:
        gold_periods[row["drug_name"]].add(row["period"])

    for path in sorted(MANIFESTS.glob("*_quarterly.csv")):
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        periods = [row["period"] for row in rows if row.get("period")]
        if len(periods) != len(set(periods)):
            duplicates = sorted({p for p in periods if periods.count(p) > 1})
            finding(out, f"{path.name}: duplicate periods {duplicates}")
        # Match the manifest to its product by name, not by period overlap.
        # Overlap looks reasonable and is wrong: Opsumit's 2016-2024 span
        # contains Letairis's 2016-2019 entirely, so a Letairis row deleted from
        # gold still "matched" Opsumit and the loss went unreported. A mutation
        # test caught that; the filename was the answer all along.
        stem = path.stem.lower()
        owners = [drug for drug in gold_periods if drug.lower().replace(" ", "_") in stem]
        if not owners:
            finding(out, f"{path.name}: no product in gold matches this manifest's name")
            continue
        if len(owners) > 1:
            finding(out, f"{path.name}: name matches several products {sorted(owners)}")
            continue
        missing = sorted(set(periods) - gold_periods[owners[0]])
        if missing:
            finding(
                out,
                f"{path.name}: {len(missing)} manifest rows absent from {owners[0]} "
                f"in gold {missing[:6]}",
            )
    return out


def audit_sources(quarterly: list[dict], annual: list[dict]) -> list[str]:
    """Provenance hygiene: https only, and no known-bad domain."""
    out: list[str] = []
    blocked = ("drugpatentwatch.com", "wikipedia.org", "statista.com", "drugs.com")
    for row in quarterly + annual:
        url = row.get("source_url", "")
        if not url.startswith("https://"):
            finding(out, f"{row['gold_id']}: non-https source {url!r}")
        for bad in blocked:
            if bad in url:
                finding(out, f"{row['gold_id']}: cites blocked domain {bad}")
    return out


def audit_values(quarterly: list[dict]) -> list[str]:
    """Values that are not plausible revenue for a quarter."""
    out: list[str] = []
    for row in quarterly:
        value = row["value_reported"]
        if value < 0:
            finding(out, f"{row['gold_id']}: negative value {value}")
        if value == 0:
            finding(out, f"{row['gold_id']}: zero value - absent or genuinely nil?")
    by_drug: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in quarterly:
        by_drug[row["drug_name"]].append((row["period"], row["value_reported"]))
    for drug, series in sorted(by_drug.items()):
        series.sort()
        # The first year of a series is a launch ramp, where a tenfold step is
        # the product working rather than the data being wrong: Remodulin goes
        # from $205k to $8.7m across its FDA approval, and Tyvaso DPI from 3 to
        # 63 in its first two quarters on sale.
        for (prev_period, prev), (period, current) in zip(series[4:], series[5:]):
            if prev > 0 and (current / prev > 10 or current / prev < 0.1):
                finding(
                    out,
                    f"{drug}: {prev_period}={prev:g} to {period}={current:g} is a "
                    "tenfold step - check for a unit or column error",
                )
    return out


def main() -> int:
    quarterly = load("quarterly_revenue.jsonl")
    annual = load("annual_revenue.jsonl")
    print(f"auditing {len(quarterly)} quarterly and {len(annual)} annual rows\n")

    checks = (
        ("derivation labels match the cited document", audit_derivation_labels(quarterly)),
        ("quotes carry corroborating evidence", audit_self_referential_quotes(quarterly)),
        ("precision claims are supported", audit_precision(quarterly)),
        ("values appear in their own citations", audit_value_appears_in_its_quote(quarterly + annual)),
        ("citations cover the quarter they are cited for", audit_citation_period(quarterly)),
        ("complete years reconcile to published totals", audit_year_reconciliation(quarterly, annual)),
        ("series attributes are stable", audit_series_consistency(quarterly)),
        ("manifests round-trip into gold", audit_manifest_round_trip(quarterly)),
        ("sources are https and not blocklisted", audit_sources(quarterly, annual)),
        ("values are plausible", audit_values(quarterly)),
    )

    total = 0
    for label, findings in checks:
        total += len(findings)
        mark = "ok  " if not findings else "FAIL"
        print(f"[{mark}] {label}: {len(findings)}")
        for line in findings:
            print(f"         {line}")
    print(f"\n{total} finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
