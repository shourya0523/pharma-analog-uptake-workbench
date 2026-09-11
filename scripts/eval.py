"""Score the pipeline through the API, the way someone using it would.

There is one way in: POST a run, wait for the jobs, read the datapoints back.
This speaks HTTP and nothing else - it imports no orchestrator, no ORM and no
reader, so there is no second implementation of the pipeline that can drift
from the pipeline.

That drift is why this exists. Nineteen eval scripts called the readers
directly and two called `run_job`; between them they reported the tagged reader
working for the whole of a branch in which the pipeline published nothing it
produced. Every one of those scripts was right about the function it called and
wrong about the product.

    python scripts/eval.py --cases seed/cases/gold_sample.json

`--base` points at a running server. Each case is what a person would type -
the drug, who makes it, the ticker, the window - plus the figures the run
should come back with, and where they came from.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]

# What the API reports for a job that has stopped. Strings, because a caller
# sees JSON: nothing here may depend on the enum behind it.
TERMINAL = {"ready_for_review", "completed", "failed", "cancelled"}
# The only status the pipeline stands behind without a reviewer.
PUBLISHED = {"auto_pass", "confirmed"}


def post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def get(base: str, path: str, timeout: int = 120) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait(base: str, run_id: str, *, timeout_s: int, quiet: bool) -> dict:
    deadline = time.time() + timeout_s
    seen = None
    run: dict = {}
    while time.time() < deadline:
        run = get(base, f"/runs/{run_id}", timeout=60)
        states = [(j["drug_name"], j["status"], j["current_step"]) for j in run["jobs"]]
        if states != seen and not quiet:
            done = sum(1 for j in run["jobs"] if j["status"] in TERMINAL)
            print(f"    {time.strftime('%H:%M:%S')} {done}/{len(run['jobs'])} done"
                  + "".join(f"\n      {d}: {s} {step or ''}" for d, s, step in states),
                  flush=True)
            seen = states
        if all(j["status"] in TERMINAL for j in run["jobs"]):
            return run
        time.sleep(5)
    return run


def score(case: dict, datapoints: list[dict]) -> list[dict]:
    """One row per figure the case expects, and what came back for it."""
    rows = []
    for want in case["expect"]:
        here = [d for d in datapoints
                if d.get("period") == want["period"]
                and d.get("value_normalized_usd_millions") is not None]
        published = [d for d in here if d.get("validation_status") in PUBLISHED]
        target = want.get("value_normalized_usd_millions")

        def near(datapoint: dict, target: float | None = target) -> bool:
            return (target is not None
                    and abs(datapoint["value_normalized_usd_millions"] - target)
                    <= max(0.5, abs(target) * 0.01))

        # Two published figures that disagree is its own outcome, and the one
        # this must never do is pick between them: taking the first would score
        # a quarter correct whenever the right answer was among the answers,
        # and a caller given two contradictory figures has been given neither.
        spread = {round(d["value_normalized_usd_millions"], 3) for d in published}
        if len(spread) > 1:
            state = "published, conflicting"
            read = max(spread)
        elif target is None:
            state = "correctly silent" if not published else "answered anyway"
            read = published[0]["value_normalized_usd_millions"] if published else None
        elif any(near(d) for d in published):
            state, read = "published, correct", next(
                d["value_normalized_usd_millions"] for d in published if near(d))
        elif published:
            state, read = "published, WRONG", published[0]["value_normalized_usd_millions"]
        elif any(near(d) for d in here):
            state, read = "held for review", next(
                d["value_normalized_usd_millions"] for d in here if near(d))
        else:
            state, read = "no answer", None
        rows.append({"period": want["period"], "want": target, "read": read,
                     "state": state,
                     "method": (published or here or [{}])[0].get("extraction_method"),
                     "candidates": len(here)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True, help="a JSON case file under seed/cases")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--case", action="append", default=[],
                    help="DRUG, repeatable; default is every case in the file")
    ap.add_argument("--timeout", type=int, default=2400, help="seconds to wait per run")
    ap.add_argument("--out", default="/tmp/eval.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.cases)
    cases = json.loads((path if path.is_absolute() else REPO / path).read_text())
    if args.case:
        wanted = {c.strip().casefold() for c in args.case}
        cases = [c for c in cases if c["drug_name"].casefold() in wanted]
    if not cases:
        print("nothing selected")
        return 1

    try:
        get(args.base, "/health", timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        print(f"no API at {args.base} ({exc}). Start it with:\n"
              f"  cd backend && ./.venv/bin/uvicorn app.main:app --port 8000")
        return 2

    results = []
    for index, case in enumerate(cases, 1):
        print(f"  [{index}/{len(cases)}] {case['drug_name']} ({case.get('manufacturer','')})",
              flush=True)
        created = post(args.base, "/runs", {
            "drugs": [{k: v for k, v in case.items()
                       if k in {"drug_name", "generic_name", "manufacturer",
                                "ticker", "cik", "indication", "known_source_url"}}],
            "options": case.get("options", {}),
        })
        run = wait(args.base, created["run_id"], timeout_s=args.timeout, quiet=args.quiet)
        job = run["jobs"][0]
        detail = get(args.base, f"/jobs/{job['id']}")
        datapoints = detail.get("datapoints") or []
        results.append({
            "run_id": created["run_id"], "job_id": job["id"],
            "drug_name": case["drug_name"], "manufacturer": case.get("manufacturer"),
            "job_status": job["status"], "error": job.get("error"),
            "sources_found": job.get("sources_found"),
            "datapoints": len(datapoints),
            "rows": score(case, datapoints),
            "source": case.get("source"),
        })

    print(f"\n  {'drug':22} {'period':8} {'want':>10} {'read':>10}  state")
    tally: dict[str, int] = {}
    for result in results:
        for row in result["rows"]:
            tally[row["state"]] = tally.get(row["state"], 0) + 1
            print(f"  {result['drug_name'][:22]:22} {row['period']:8} "
                  f"{'' if row['want'] is None else format(row['want'], ',.1f'):>10} "
                  f"{'' if row['read'] is None else format(row['read'], ',.1f'):>10}"
                  f"  {row['state']}"
                  + (f" ({row['method']})" if row["method"] else ""))
    total = sum(tally.values())
    print(f"\n  {total} expected figures across {len(results)} runs")
    for state, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"    {state:22} {n:>4}  {100 * n / total:5.1f}%")
    good = tally.get("published, correct", 0) + tally.get("correctly silent", 0)
    print(f"  correct: {good}/{total}")
    methods: dict[str, int] = {}
    for result in results:
        for row in result["rows"]:
            if row["state"] == "published, correct" and row["method"]:
                methods[row["method"]] = methods.get(row["method"], 0) + 1
    if methods:
        print("  published-correct by method: "
              + ", ".join(f"{m} {n}" for m, n in sorted(methods.items(), key=lambda kv: -kv[1])))
    pathlib.Path(args.out).write_text(json.dumps(results, indent=1, default=str))
    print(f"  detail written to {args.out}")
    return 0 if good == total else 1


if __name__ == "__main__":
    sys.exit(main())
