"""Derive the eval's gold case files from the answer key itself.

`seed/cases/gold_all.json` is what `scripts/eval.py` scores the pipeline on.
It used to be maintained by hand, so it held whatever gold held on the day
someone last edited it: gold grew, the case file did not, and the products
gold gained were scored by nothing. Nothing said so, because nothing produced
the file.

This produces it. Run it, commit what changes, and the case file is gold
restated in the shape a caller types:

    python scripts/build_gold_cases.py

The direction is the one CLAUDE.md rule 3 allows. Gold scores the pipeline;
this reads gold and writes the *eval's* input, which the pipeline never opens.
Delete both outputs and the pipeline is unchanged - only the score goes, and
this rebuilds it from gold in one command.

What a case is
--------------

One case is one product in one calendar year: the drug a person would name,
who makes it, and a window wide enough to hold every filing that reports that
year's quarters. The window is arithmetic on the year's own quarter ends -
it opens once the year's first quarter has ended and its report can exist, and
closes once the annual report covering the year's last quarter has been filed:

    a case for year Y  ->  earnings_since = end of Y-Q1 + REPORTABLE_AFTER_DAYS
                           earnings_until = end of Y-Q4 + REPORTED_WITHIN_DAYS

so a case never asks for a quarter no filing in its window could carry, and
never closes before the filing that states the quarter exists.

Expecting nothing is an answer
------------------------------

A case file in which every expectation is a figure is passed by a pipeline
that publishes something for every quarter it is asked about, so gold's two
records of *absence* become expectations too:

- `series_coverage.jsonl` says where a series ends and why. Where it ends
  because the issuer stopped reporting the product separately, the rest of
  that year's quarters must come back empty - the issuer still files, the
  product still sells, and the line is gone.
- `excluded_products.jsonl` is the products gold refused to build a series
  for. Each becomes a case whose every quarter must come back empty, carrying
  gold's own reason.

An empty expectation is `value_normalized_usd_millions: null` with a `why`,
which is how `shapes_holdout.json` and `unseen.json` already spell it.

What a case asks for
--------------------

Beyond its window a case carries the layer-two options - the openFDA lookup
and the product-metadata pass - and each is a flag:

    python scripts/build_gold_cases.py --openfda --product-metadata

Their defaults are a snapshot of the configuration this repository can be
measured under rather than a statement about the product, and they are stale
as soon as layer two is worth scoring: the change that makes it worth scoring
passes the flags and commits what they produce. Every case records what it
was built with, so a case file always says which configuration its figures
are an answer for.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import date, timedelta

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.domain.models import ExtractionOptions

GOLD = REPO / "seed" / "gold"
CASES = REPO / "seed" / "cases"

QUARTERLY = GOLD / "quarterly_revenue.jsonl"
COVERAGE = GOLD / "series_coverage.jsonl"
EXCLUDED = GOLD / "excluded_products.jsonl"

# A quarter is reportable from the day after it ends; the earnings release
# follows within days and the annual report covering the year's last quarter
# within about a fiscal quarter of it. These bound the window, and widening
# either one lets a filing that restates the year into the window beside the
# filing that reported it.
REPORTABLE_AFTER_DAYS = 5
REPORTED_WITHIN_DAYS = 125

# Layer 2 - the openFDA lookup and the product-metadata pass - is a
# configuration, not a property of the cases, so it is a flag here and the case
# file records what it was built with. Which two options are layer two is the
# snapshot; what they default to is not, and is taken from `ExtractionOptions`,
# the same defaults the UI gets when it sends `options: {}` - so a case cannot
# quietly measure a configuration no user runs. Passing a flag still overrides
# either one for a run that wants the other configuration.
LAYER_TWO_OPTIONS = ("openfda", "product_metadata")
LAYER_TWO_DEFAULTS = {
    name: ExtractionOptions.model_fields[name].get_default() for name in LAYER_TWO_OPTIONS
}

# Gold names its issuers but records no ticker, and the pipeline resolves an
# issuer by ticker first. This is a snapshot of the symbol the SEC's
# company_tickers.json lists for each issuer gold names; it goes stale when
# gold names an issuer that is not here, and `_ticker` stops rather than
# writing a case whose issuer cannot be resolved.
ISSUER_TICKERS = {
    "Actelion/J&J": "JNJ",
    "Eli Lilly": "LLY",
    "Gilead": "GILD",
    "Johnson & Johnson": "JNJ",
    "Liquidia": "LQDA",
    "Merck": "MRK",
    "United Therapeutics": "UTHR",
}

# The series ends because the issuer stopped printing the line, not because
# sourcing ran out. Only this basis makes the quarters after it an expectation
# of silence: a series that ends for any other reason ends where gold's
# evidence does, which says nothing about what the pipeline should find.
ISSUER_STOPPED = "issuer_stopped_reporting"


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def quarter_end(period: str) -> date:
    """The calendar day a period label ends on: 2024Q2 -> 30 June 2024."""
    year, quarter = int(period[:4]), int(period[-1])
    month = quarter * 3
    return date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)


def window(year: int) -> dict[str, str]:
    """The filing window a case for one calendar year is asked over."""
    return {
        "earnings_since": (quarter_end(f"{year}Q1")
                           + timedelta(days=REPORTABLE_AFTER_DAYS)).isoformat(),
        "earnings_until": (quarter_end(f"{year}Q4")
                           + timedelta(days=REPORTED_WITHIN_DAYS)).isoformat(),
    }


def _ticker(manufacturer: str) -> str:
    if manufacturer not in ISSUER_TICKERS:
        raise SystemExit(
            f"gold names an issuer this builder has no ticker for: {manufacturer!r}. "
            f"Add the symbol the SEC lists for it to ISSUER_TICKERS."
        )
    return ISSUER_TICKERS[manufacturer]


def latest_complete_year(as_of_quarter: str) -> int:
    """The last calendar year gold's own as-of quarter can speak for in full.

    A product gold excluded has no quarters to date a case from, so its case
    asks the most recent year gold could have held a full series for.
    """
    year, quarter = int(as_of_quarter[:4]), int(as_of_quarter[-1])
    return year if quarter == 4 else year - 1


def _silent_quarters(coverage: dict, year: int) -> list[str]:
    """The quarters of one year that fall after the issuer stopped reporting."""
    if coverage.get("series_end_basis") != ISSUER_STOPPED:
        return []
    end = coverage["series_end_quarter"]
    if int(end[:4]) != year:
        return []
    return [f"{year}Q{q}" for q in range(int(end[-1]) + 1, 5)]


def reported_cases(quarterly: list[dict], coverage: dict[str, dict],
                   options: dict) -> list[dict]:
    """One case per product-year gold holds quarters for."""
    grouped: dict[tuple[str, int], list[dict]] = {}
    for row in quarterly:
        grouped.setdefault((row["drug_name"], int(row["period"][:4])), []).append(row)

    cases = []
    for (drug, year), rows in grouped.items():
        first = rows[0]
        expect = [
            {"period": row["period"],
             "value_normalized_usd_millions": row["value_normalized_usd_millions"],
             "gold_id": row["gold_id"]}
            for row in sorted(rows, key=lambda r: r["period"])
        ]
        series = coverage[first["benchmark_identity"]]
        for period in _silent_quarters(series, year):
            expect.append({
                "period": period,
                "value_normalized_usd_millions": None,
                "why": f"The issuer reports no line for this product after "
                       f"{series['series_end_quarter']}. {series['series_end_reason']}",
            })
        cases.append({
            "drug_name": drug,
            "manufacturer": first["manufacturer"],
            "ticker": _ticker(first["manufacturer"]),
            "generic_name": first["generic_name"],
            "options": {**window(year), **options},
            "source": str(QUARTERLY.relative_to(REPO)),
            "expect": sorted(expect, key=lambda e: e["period"]),
        })
    return cases


def excluded_cases(excluded: list[dict], year: int, options: dict) -> list[dict]:
    """One case per product gold refused to build a series for.

    Gold records no issuer for these, so neither does the case: an excluded
    product is a name a person has and nothing else, and the answer to it is
    silence whoever is asked.
    """
    cases = []
    for row in sorted(excluded, key=lambda r: r["drug_name"]):
        why = f"{row['reason_code']}: {row['details']}"
        cases.append({
            "drug_name": row["drug_name"],
            "manufacturer": None,
            "ticker": None,
            "generic_name": None,
            "options": {**window(year), **options},
            "source": str(EXCLUDED.relative_to(REPO)),
            "expect": [{"period": f"{year}Q{q}",
                        "value_normalized_usd_millions": None,
                        "gold_id": row["gold_id"],
                        "why": why} for q in range(1, 5)],
        })
    return cases


def _order(case: dict) -> tuple:
    """Issuer, then product, then year - and the issuerless cases last."""
    return (case["manufacturer"] is None, case["manufacturer"] or "",
            case["drug_name"], case["expect"][0]["period"])


def build(options: dict | None = None) -> list[dict]:
    """Every case gold licenses, in a stable order.

    ``options`` is what each case asks the pipeline for beyond its window;
    leaving it out builds the file as it is committed.
    """
    options = LAYER_TWO_DEFAULTS if options is None else options
    quarterly = _rows(QUARTERLY)
    coverage = {row["benchmark_identity"]: row for row in _rows(COVERAGE)}
    excluded = _rows(EXCLUDED)
    as_of = max(row["as_of_quarter"] for row in coverage.values())
    cases = reported_cases(quarterly, coverage, options)
    cases += excluded_cases(excluded, latest_complete_year(as_of), options)
    return sorted(cases, key=_order)


def _states_a_figure(case: dict) -> bool:
    return any(e["value_normalized_usd_millions"] is not None for e in case["expect"])


def _expects_only_silence(case: dict) -> bool:
    return not _states_a_figure(case)


def _mixes_both(case: dict) -> bool:
    return _states_a_figure(case) and any(
        e["value_normalized_usd_millions"] is None for e in case["expect"])


def sample(cases: list[dict]) -> list[dict]:
    """The smoke test: the same run, small enough to watch.

    One case per issuer - the most recent product-year that issuer has, its
    product name breaking a tie - and then the first case, in the file's own
    order, of each way a case can expect nothing: a window that runs past the
    quarter an issuer stopped reporting a product, and a product gold excluded
    outright. Picking the most recent year asks each issuer for the filings it
    publishes today rather than the ones it published first.
    """
    newest: dict[str, dict] = {}
    for case in cases:
        if not _states_a_figure(case):
            continue
        issuer = case["manufacturer"] or ""
        rank = (case["expect"][-1]["period"], case["drug_name"])
        best = newest.get(issuer)
        if best is None or rank > (best["expect"][-1]["period"], best["drug_name"]):
            newest[issuer] = case
    chosen = list(newest.values())
    for shape in (_mixes_both, _expects_only_silence):
        if not any(shape(case) for case in chosen):
            chosen += [next(case for case in cases if shape(case))]
    return sorted(chosen, key=_order)


def _write(path: pathlib.Path, cases: list[dict]) -> None:
    path.write_text(json.dumps(cases, indent=1) + "\n")


def counts(cases: list[dict]) -> dict[str, int]:
    """What a caller has to report having built: the shape of the file."""
    expectations = [e for case in cases for e in case["expect"]]
    return {
        "products": len({case["drug_name"] for case in cases}),
        "cases": len(cases),
        "expectations": len(expectations),
        "figures": sum(1 for e in expectations
                       if e["value_normalized_usd_millions"] is not None),
        "empty": sum(1 for e in expectations
                     if e["value_normalized_usd_millions"] is None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="say whether the committed files match what gold produces "
                         "today, and write nothing")
    # One pair of flags per layer-two option, from the defaults themselves, so
    # an option added there is a flag here without this being touched.
    for name, default in LAYER_TWO_DEFAULTS.items():
        flag = name.replace("_", "-")
        ap.add_argument(f"--{flag}", dest=name, action="store_true", default=default,
                        help=f"ask each case for {name} (default {default})")
        ap.add_argument(f"--no-{flag}", dest=name, action="store_false")
    args = ap.parse_args()

    options = {name: getattr(args, name) for name in LAYER_TWO_DEFAULTS}
    cases = build(options)
    small = sample(cases)
    stale = 0
    for path, built in ((CASES / "gold_all.json", cases), (CASES / "gold_sample.json", small)):
        if args.check:
            committed = json.loads(path.read_text()) if path.exists() else None
            matches = committed == built
            stale += not matches
            print(f"  {path.relative_to(REPO)}: "
                  + ("matches gold" if matches else "DIFFERS from gold"))
            continue
        _write(path, built)
        print(f"  {path.relative_to(REPO)}  "
              + "  ".join(f"{k} {v}" for k, v in counts(built).items())
              + "  options " + json.dumps(options, sort_keys=True))
    # A check that always succeeds is a report for a reader, not an answer for
    # a caller: the exit status is how anything but a person finds out.
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
