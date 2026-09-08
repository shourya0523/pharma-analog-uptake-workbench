"""Score the pipeline itself: run the job, then read what it published.

Every other coverage eval in this repo calls the readers directly. Checked by
AST, ``eval_coverage.py`` exercises none of the twelve stages in
``PipelineOrchestrator.run_job`` and calls no LLM entry point, so its figures
are the deterministic extraction floor - the XBRL reader, the table reader, the
derivations and sourcing, with the LLM extractor, the evidence judge and
conflict reconciliation all absent. A "wrong value" there is a figure a reader
*emitted*, with two error-catching stages still standing between it and
anything a consumer would see.

This one closes that gap. It creates a run and a job per (product, issuer,
window), calls ``run_job``, and scores the datapoints the pipeline **published**
- ``validation_status == auto_pass``, the only status that is not waiting on a
human. Everything the pipeline does is therefore in the number, including the
parts that cost money.

The interesting output is not the hit rate. It is the cross-tabulation of
correctness against ``validation_status``: a reader error that the judge
demoted to ``needs_review`` never reaches a consumer, and until now nothing
measured how often that happens. Four cells matter:

    published & correct      the score
    published & wrong        what a consumer would be given and could not check
    held & wrong             the judge earning its cost
    held & correct           the judge's cost, in answers withheld

Start small. One job is one product over one window and costs several LLM calls
plus an EDGAR walk:

    OPENROUTER_MODEL_EXTRACT=openai/gpt-4o-mini \
    OPENROUTER_MODEL_JUDGE=openai/gpt-4o-mini \
    SEC_USER_AGENT="project you@example.com" \
        python scripts/eval_pipeline_end_to_end.py --sample --out /tmp/e2e.json

``--sample`` is 4 products across two issuers over one year. ``--all`` is the
corpus and should not be run until a sample has been read.

Gold contributes the product, the issuer, the quarter and the expected value,
and is read here and nowhere the pipeline can reach it.
"""

# ruff: noqa: BLE001 - a job that raises is an outcome to record, not a crash
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import logging
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "backend"))

import eval_extraction_documents as E
from app.config import get_settings
from app.db.models import (
    Base,
    DatapointORM,
    DrugJobORM,
    ExtractionRunORM,
    SourceDocumentORM,
)
from app.domain.models import PeriodType, ValidationStatus, new_id
from app.pipeline.orchestrator import PipelineOrchestrator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# What "published" means. A datapoint the pipeline auto-passed is the only one
# it is willing to stand behind without a reviewer; everything else is queued
# for a human and is a gap rather than an answer. CONFIRMED cannot occur in an
# unattended run - it is what a reviewer writes - but it is listed so the
# definition reads as the product rule it is rather than as one enum value.
PUBLISHED = {ValidationStatus.AUTO_PASS.value, ValidationStatus.CONFIRMED.value}

# Four products over two issuers: an issuer that tags XBRL and reports by
# region (Gilead) and one whose older years are prose (United Therapeutics).
# Small enough to read every row of the output by hand, which is the point.
#
# The two years straddle the change that matters most to how an answer is
# obtained: iXBRL detail tagging begins for a large accelerated filer with
# periods ending on or after 15 June 2019, so 2018 exercises the table reader
# and 2019 the filer's own tagged facts. A sample inside one era would report
# an average of one path and call it the pipeline.
SAMPLE = [("Gilead", "Truvada"), ("Gilead", "Letairis"),
          ("United Therapeutics", "Tyvaso"), ("United Therapeutics", "Orenitram")]
SAMPLE_YEARS = (2018, 2019)


def windows(periods: list[str], span_years: int) -> list[tuple[int, int]]:
    """The (first year, last year) spans covering these quarters.

    A window is bounded because the connector is: ``sec_max_earnings_exhibits``
    caps how many exhibits one retrieval returns, so asking one job for eight
    years of quarters silently drops most of them and scores the pipeline on
    filings it was never handed.
    """
    years = sorted({int(p[:4]) for p in periods})
    out: list[tuple[int, int]] = []
    for year in years:
        if out and year <= out[-1][1]:
            continue
        out.append((year, year + span_years - 1))
    return out


def window_dates(first: int, last: int) -> tuple[dt.date, dt.date]:
    """Filing dates that bracket every earnings release for those years.

    Q1 of ``first`` is reported a few weeks after 31 March; Q4 of ``last`` is
    reported up to four months after 31 December, in the following year.
    """
    return (dt.date(first, 3, 31) + dt.timedelta(days=5),
            dt.date(last, 12, 31) + dt.timedelta(days=120))


# Three vocabularies share one `revenue_scope` field and they do not line up.
# Gold's says what the issuer's line covers: "U.S." for United Therapeutics'
# Letairis and Orenitram, which are sold essentially only there, "Worldwide"
# for Gilead's Truvada. The deterministic readers' says granularity - "Product
# family", "Formulation-specific" - and never names a geography at all. The
# LLM extractor's names a geography: "U.S.", "Europe", "Other International".
#
# So a granularity label cannot be matched against a geographic one, and a
# rule that tried - a region answers only the same region - scored Letairis
# and Orenitram at zero, because their gold rows say "U.S." and the reader
# that answered them says "Product family".
#
# One case does need separating, and only one. Gilead breaks Truvada out by
# region beside a worldwide total: 744 in the U.S. and 768 worldwide for
# 2019Q4. A datapoint that names a single region is not that total, and
# scoring it against a worldwide gold row calls a correct regional figure a
# misread. Nothing else here is asserted.
_REGIONS = {"U.S.", "ex-U.S.", "Europe", "International", "Other International", "Regional"}


def answers_scope(datapoint_scope: str | None, gold_scope: str | None) -> bool:
    """Whether a datapoint is about the series a gold row asks for.

    One rule: a line labelled with a single region is not the worldwide total.
    A label that names no geography claims none, and answers whatever is asked.
    """
    got, want = (datapoint_scope or "").strip(), (gold_scope or "").strip()
    return not (want == "Worldwide" and got in _REGIONS)


def scored_state(published: list[dict], held: list[dict], target: float,
                 off_series: list[dict] | None = None) -> tuple[str, float | None, str | None]:
    """What the pipeline did with one gold quarter.

    A published figure is judged first and alone: it is what a consumer with no
    answer key would be handed. Two published figures that disagree are not an
    answer - reconciliation is supposed to have demoted one of them, and when
    it has not, that is a failure of the stage rather than a coin to flip.
    """
    off_series = off_series or []
    if published:
        values = [float(c["value"]) for c in published]
        if max(values) - min(values) > E.TOLERANCE:
            return "published_conflict", min(values, key=lambda v: abs(v - target)), None
        best = min(published, key=lambda c: abs(float(c["value"]) - target))
        hit = abs(float(best["value"]) - target) <= E.TOLERANCE
        return ("published_correct" if hit else "published_wrong"), float(best["value"]), best["status"]
    if held:
        best = min(held, key=lambda c: abs(float(c["value"]) - target))
        hit = abs(float(best["value"]) - target) <= E.TOLERANCE
        return ("held_correct" if hit else "held_wrong"), float(best["value"]), best["status"]
    if off_series:
        # Something was published for this quarter, about a different series.
        # Not an answer to the question gold asked, and not a wrong value
        # either - the datapoint says which region it is about.
        best = min(off_series, key=lambda c: abs(float(c["value"]) - target))
        return "published_other_scope", float(best["value"]), best["status"]
    return "no_datapoint", None, None


async def run_one(orch, db, *, product: str, issuer: str, rows: list[dict],
                  first: int, last: int, options: dict) -> dict:
    """One job over one window, and what it published for the gold quarters."""
    since, until = window_dates(first, last)
    run = ExtractionRunORM(id=new_id(), status="running", options_json={
        **options,
        "earnings_since": since.isoformat(),
        "earnings_until": until.isoformat(),
    })
    db.add(run)
    job = DrugJobORM(
        id=new_id(), run_id=run.id,
        drug_name=product,
        generic_name=rows[0].get("generic_name"),
        manufacturer=issuer,
        ticker=E.TICKER.get(issuer),
        status="queued", quality_flags=[],
    )
    db.add(job)
    db.commit()

    started = time.time()
    error = None
    try:
        await orch.run_job(job.id)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - started

    dps = db.query(DatapointORM).filter_by(job_id=job.id).all()
    sources = db.query(SourceDocumentORM).filter_by(job_id=job.id).all()
    quarterly = [d for d in dps
                 if d.period_type == PeriodType.QUARTERLY.value
                 and d.value_normalized_usd_millions is not None]
    by_period: dict[str, list[dict]] = collections.defaultdict(list)
    for d in quarterly:
        by_period[str(d.period)].append({
            "value": d.value_normalized_usd_millions,
            "status": d.validation_status,
            "method": d.extraction_method,
            "scope": d.revenue_scope,
            "formulation": d.formulation,
            "support": d.source_support,
            "flags": list(d.issue_flags or []),
            "source_type": (d.citation_json or {}).get("source_type"),
            # The key _reconcile_with_llm groups by. Recorded rather than
            # recomputed downstream so a claim about what reconciliation could
            # even see is checkable against the rows it actually saw.
            "reconcile_key": [str(d.period), d.revenue_scope or "", d.formulation or ""],
        })

    records = []
    for row in rows:
        got = by_period.get(row["period"], [])
        # Only datapoints about the series gold asked for are scored against
        # it. The rest are kept and reported, not discarded: publishing a
        # regional line where the product's own line was wanted is a real
        # outcome, it is just not a misread.
        mine = [c for c in got if answers_scope(c["scope"], row.get("revenue_scope"))]
        off_series = [c for c in got if c not in mine and c["status"] in PUBLISHED]
        published = [c for c in mine if c["status"] in PUBLISHED]
        held = [c for c in mine if c["status"] not in PUBLISHED]
        target = float(row["value_normalized_usd_millions"])
        state, read, status = scored_state(published, held, target, off_series)
        records.append({
            "drug_name": product, "manufacturer": issuer, "period": row["period"],
            "gold": target, "gold_scope": row.get("revenue_scope"),
            "read": read, "state": state, "status": status,
            "candidates": got, "off_series": len(off_series), "job_id": job.id,
        })
    return {
        "product": product, "issuer": issuer, "window": f"{first}-{last}",
        "job_id": job.id, "error": error, "elapsed": elapsed,
        "step": job.current_step, "job_status": job.status,
        "sources": len(sources),
        "sources_ok": sum(1 for s in sources if s.retrieval_status == "success"),
        "datapoints": len(dps), "quarterly": len(quarterly),
        "published_quarterly": sum(1 for d in quarterly
                                   if d.validation_status in PUBLISHED),
        "gold_periods": len(rows),
        "records": records,
    }


def report(jobs: list[dict], *, scope: str) -> None:
    settings = get_settings()
    print()
    print("=" * 78)
    print("PIPELINE, END TO END - orchestrator.run_job, scored on what it PUBLISHED")
    print("=" * 78)
    print(f"  extract model      {settings.openrouter_model_extract}")
    print(f"  judge model        {settings.openrouter_model_judge}")
    print(f"  published means    validation_status in {sorted(PUBLISHED)}")
    print(f"  scope              {scope}")
    print()

    print(f"{'product':<18}{'issuer':<22}{'window':<10}{'srcs':>5}{'dps':>5}"
          f"{'pub':>5}{'gold':>6}{'secs':>7}  step")
    for job in jobs:
        print(f"{job['product']:<18}{job['issuer']:<22}{job['window']:<10}"
              f"{job['sources_ok']:>5}{job['quarterly']:>5}{job['published_quarterly']:>5}"
              f"{job['gold_periods']:>6}{job['elapsed']:>7.0f}  {job['step']}"
              + (f"  ERROR {job['error']}" if job["error"] else ""))

    records = [r for job in jobs for r in job["records"]]
    total = len(records)
    if not total:
        print("\nno gold rows in scope")
        return
    states = collections.Counter(r["state"] for r in records)

    print(f"\ngold quarters in scope: {total}")
    print("\nwhat the pipeline did with each one")
    for name, label, note in (
        ("published_correct", "published, correct", "the score"),
        ("published_wrong", "published, WRONG", "handed over uncheckable"),
        ("published_conflict", "published, conflicting", "reconciliation left two"),
        ("held_correct", "held for review, correct", "the judge's cost"),
        ("held_wrong", "held for review, wrong", "the judge's catch"),
        ("published_other_scope", "published, another series", "a region, not this line"),
        ("no_datapoint", "no datapoint at all", ""),
    ):
        n = states.get(name, 0)
        print(f"  {label:<26}{n:>5}{n / total:>8.1%}   "
              + (f"<- {note}" if note else ""))

    published = states.get("published_correct", 0)
    print(f"\n  PUBLISHED ACCURACY   {published}/{total}  {published / total:.1%}")

    # A regional line published where the gold row asks for the worldwide total
    # is not scored against it, so say how often that happened rather than let
    # the scope rule quietly absorb it.
    off = sum(1 for r in records if r.get("off_series"))
    if off:
        print(f"  off-series published  {off} gold quarters also had a single-region "
              "line published beside them")

    # The judge's catch rate on reader errors, which is the figure this eval
    # exists to make visible. "Wrong" here means a datapoint whose value
    # disagrees with gold; "caught" means it did not reach a consumer.
    wrong = states.get("published_wrong", 0) + states.get("held_wrong", 0) \
        + states.get("published_conflict", 0)
    caught = states.get("held_wrong", 0)
    if wrong:
        print(f"  judge catch rate     {caught}/{wrong} wrong values held back  "
              f"{caught / wrong:.1%}")
    else:
        print("  judge catch rate     no wrong values to catch")
    answered = published + states.get("held_correct", 0)
    if answered:
        print(f"  withheld correct     {states.get('held_correct', 0)}/{answered} of the "
              f"right answers the pipeline found were not published")

    # Every status the datapoints carried, against whether they agreed with
    # gold. The split is the point: a status is only meaningful if it separates
    # right from wrong.
    split: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in records:
        for c in r["candidates"]:
            if not answers_scope(c["scope"], r.get("gold_scope")):
                continue
            agrees = abs(float(c["value"]) - r["gold"]) <= E.TOLERANCE
            split[c["status"]]["correct" if agrees else "wrong"] += 1
    if split:
        print("\nvalidation_status of every datapoint landing on a gold quarter")
        print(f"  {'status':<18}{'correct':>9}{'wrong':>8}{'precision':>11}")
        for status, counts in sorted(split.items()):
            n = counts["correct"] + counts["wrong"]
            print(f"  {status:<18}{counts['correct']:>9}{counts['wrong']:>8}"
                  f"{counts['correct'] / n:>11.1%}")

    methods = collections.Counter(
        c["method"] for r in records if r["state"] == "published_correct"
        for c in r["candidates"] if c["status"] in PUBLISHED
    )
    if methods:
        print("\npublished-correct answers by extraction_method")
        for method, n in methods.most_common():
            print(f"  {method:<28}{n:>5}")

    # Reconciliation adjudicates only within a group, and the group key carries
    # revenue_scope. The deterministic readers emit "Product family" or
    # "Formulation-specific"; the model emits from the whole RevenueScope
    # vocabulary, geography included. Where two candidates for one quarter
    # disagree and sit in different groups, no stage ever compares them.
    disagreeing = paired = 0
    for r in records:
        got = r["candidates"]
        if len(got) < 2:
            continue
        values = [float(c["value"]) for c in got]
        if max(values) - min(values) <= E.TOLERANCE:
            continue
        disagreeing += 1
        if len({tuple(c["reconcile_key"]) for c in got}) < len(got):
            paired += 1
    if disagreeing:
        print(f"\nquarters with candidates that disagree      {disagreeing}")
        print(f"  of those, any two sharing a reconcile key  {paired}"
              "   <- the rest were never compared")

    bad = [r for r in records if r["state"] in {"published_wrong", "published_conflict"}]
    if bad:
        print("\npublished and wrong - each one is a citation a consumer cannot check:")
        for r in sorted(bad, key=lambda r: (r["drug_name"], r["period"]))[:20]:
            print(f"  {r['drug_name']:<16}{r['period']}  gold {r['gold']:<10g}"
                  f" published {r['read']:<10g} as {r['status']}")


def rescore(path: pathlib.Path) -> int:
    """Re-run the scoring over a stored run, changing nothing about the run.

    Only the per-row state is recomputed; the candidates, their statuses and
    their scopes are what the pipeline actually produced.
    """
    jobs = json.loads(path.read_text())
    scopes = {(row["drug_name"], row["period"]): row.get("revenue_scope")
              for row in E.load_rows()}
    for job in jobs:
        for record in job["records"]:
            gold_scope = record.get("gold_scope") or scopes.get(
                (record["drug_name"], record["period"]))
            record["gold_scope"] = gold_scope
            got = record["candidates"]
            mine = [c for c in got if answers_scope(c.get("scope"), gold_scope)]
            off = [c for c in got if c not in mine and c["status"] in PUBLISHED]
            state, read, status = scored_state(
                [c for c in mine if c["status"] in PUBLISHED],
                [c for c in mine if c["status"] not in PUBLISHED],
                float(record["gold"]), off,
            )
            record.update(state=state, read=read, status=status, off_series=len(off))
    report(jobs, scope=f"re-scored from {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", action="store_true",
                    help="4 products, two issuers, one year - the default")
    ap.add_argument("--all", action="store_true", help="every gold row; slow and not free")
    ap.add_argument("--product", action="append", default=[],
                    help="ISSUER:PRODUCT, repeatable")
    ap.add_argument("--years", default="", help="e.g. 2019 or 2019-2021")
    ap.add_argument("--window-years", type=int, default=1,
                    help="quarters per job; more than about two overruns the "
                         "connector's exhibit ceiling")
    ap.add_argument("--out", default="/tmp/e2e.json")
    ap.add_argument("--db", default="", help="sqlite path; default is in-memory")
    ap.add_argument("--no-metadata", action="store_true",
                    help="skip the profile stages, which cost LLM calls and "
                         "answer no revenue question. Reported in the header.")
    ap.add_argument("--verbose", action="store_true", help="pipeline logs")
    ap.add_argument("--rescore", default="",
                    help="re-score a stored run with the current rules and "
                         "print the report, running nothing and spending "
                         "nothing. A stored run keeps every candidate with its "
                         "status and scope, so a change to how a gold row is "
                         "matched can be checked against real output before it "
                         "is believed - which is how a scope rule that scored "
                         "two whole products at zero was caught.")
    args = ap.parse_args()

    if args.rescore:
        return rescore(pathlib.Path(args.rescore))

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    rows = E.load_rows()
    wanted: set[tuple[str, str]] | None = None
    years: set[int] | None = None
    if args.product:
        wanted = set()
        for spec in args.product:
            issuer, _, product = spec.partition(":")
            wanted.add((issuer.strip(), product.strip()))
    elif not args.all:
        wanted = set(SAMPLE)
        years = set(SAMPLE_YEARS)
    if args.years:
        first, _, last = args.years.partition("-")
        years = set(range(int(first), int(last or first) + 1))

    rows = [r for r in rows
            if (wanted is None or (r["manufacturer"], r["drug_name"]) in wanted)
            and (years is None or int(r["period"][:4]) in years)]
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        groups[(row["manufacturer"], row["drug_name"])].append(row)

    plan = []
    for (issuer, product), group in sorted(groups.items()):
        for first, last in windows([r["period"] for r in group], args.window_years):
            in_window = [r for r in group if first <= int(r["period"][:4]) <= last]
            if in_window:
                plan.append((issuer, product, first, last, in_window))
    scope = (f"{len(plan)} jobs, {len(rows)} gold quarters, "
             f"{args.window_years}y window"
             + (", metadata stages off" if args.no_metadata else ""))
    print(f"planned: {scope}", flush=True)
    if not plan:
        print("nothing selected")
        return 1

    engine = create_engine(f"sqlite:///{args.db}" if args.db else "sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    store = E.LocalCacheStore(pathlib.Path(os.environ.get("DISCOVER_CACHE", "/tmp/discovered")))
    orch = PipelineOrchestrator(db, file_store=store)
    options = {
        "sec_filings": True, "earnings_releases": True, "quarterly_revenue": True,
        "openfda": not args.no_metadata, "product_metadata": not args.no_metadata,
        "llm_evidence_judge": True,
    }

    async def go() -> list[dict]:
        done = []
        for index, (issuer, product, first, last, group) in enumerate(plan, 1):
            print(f"  [{index}/{len(plan)}] {product} ({issuer}) {first}-{last}, "
                  f"{len(group)} gold quarters", flush=True)
            done.append(await run_one(orch, db, product=product, issuer=issuer,
                                      rows=group, first=first, last=last,
                                      options=options))
        return done

    jobs = asyncio.run(go())
    report(jobs, scope=scope)
    pathlib.Path(args.out).write_text(json.dumps(jobs, indent=1, default=str))
    print("\nper-row detail written to", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
