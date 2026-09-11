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


def _call(request, *, timeout: int, attempts: int = 12) -> dict:
    """One HTTP call, patient with a server that is busy running jobs.

    The in-process queue runs pipeline stages on the same event loop that
    serves the API, so while several documents are being parsed a poll can go
    unanswered for longer than a socket timeout. That is the server working,
    not the server gone; a client that dies on it orphans every run it
    started.
    """
    delay = 5.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            if attempt == attempts - 1:
                raise
            print(f"    ({type(exc).__name__}; the server is busy - retrying in {delay:.0f}s)",
                  flush=True)
            time.sleep(delay)
            delay = min(delay * 1.6, 60.0)
    raise RuntimeError("unreachable")


def post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return _call(request, timeout=180)


def get(base: str, path: str, timeout: int = 180) -> dict:
    return _call(urllib.request.Request(f"{base}{path}"), timeout=timeout)


def existing_runs(base: str) -> dict[str, str]:
    """Runs already on the server, keyed the way batches are: by their options.

    So a client that died mid-way, or a second invocation, scores the runs it
    already started instead of starting them again.
    """
    found: dict[str, str] = {}
    offset = 0
    while True:
        page = get(base, f"/observability/db/extraction_runs?limit=500&offset={offset}")
        for row in page.get("rows", []):
            options = row.get("options_json") or {}
            key = json.dumps({k: options.get(k) for k in OPTION_KEYS if k in options},
                             sort_keys=True)
            found.setdefault(key, row["id"])
        if offset + 500 >= page.get("total", 0):
            return found
        offset += 500


# A run is matched back to its batch on the window alone. The server stores
# every option with its default filled in, so matching on the full set never
# matched anything and a re-attach started every run again.
OPTION_KEYS = ("earnings_since", "earnings_until")


def wait(base: str, run_id: str, *, timeout_s: int, quiet: bool) -> dict:
    deadline = time.time() + timeout_s
    seen = None
    run: dict = {}
    while time.time() < deadline:
        run = get(base, f"/runs/{run_id}")
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
    ap.add_argument("--attach", action="store_true",
                    help="score runs already on the server for these cases' windows "
                         "instead of starting them again")
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

    # Cases sharing the same options go in one run, the way a person would
    # paste a list of drugs for one window. Jobs are matched back to cases by
    # drug name, which the API returns on each job.
    DRUG_FIELDS = {"drug_name", "generic_name", "manufacturer", "ticker", "cik",
                   "indication", "known_source_url"}
    batches: dict[str, list[dict]] = {}
    for case in cases:
        batches.setdefault(json.dumps(case.get("options", {}), sort_keys=True), []).append(case)

    results = []
    already = existing_runs(args.base) if args.attach else {}
    for index, (options_key, batch) in enumerate(batches.items(), 1):
        options = json.loads(options_key)
        match_key = json.dumps({k: options.get(k) for k in OPTION_KEYS if k in options},
                               sort_keys=True)
        if match_key in already:
            run_id = already[match_key]
            print(f"  [{index}/{len(batches)}] {len(batch)} drug(s): attached to run {run_id[:8]}",
                  flush=True)
        else:
            print(f"  [{index}/{len(batches)}] {len(batch)} drug(s), options {options_key[:70]}",
                  flush=True)
            run_id = post(args.base, "/runs", {
                "drugs": [{k: v for k, v in case.items() if k in DRUG_FIELDS} for case in batch],
                "options": options,
            })["run_id"]
        created = {"run_id": run_id}
        run = wait(args.base, run_id, timeout_s=args.timeout, quiet=args.quiet)
        by_drug = {}
        for job in run["jobs"]:
            by_drug.setdefault(job["drug_name"].casefold(), []).append(job)
        for case in batch:
            job = (by_drug.get(case["drug_name"].casefold()) or [None]).pop(0)
            if job is None:
                results.append({"run_id": created["run_id"], "job_id": None,
                                "drug_name": case["drug_name"], "manufacturer": case.get("manufacturer"),
                                "job_status": "missing", "error": "no job for this drug",
                                "sources_found": None, "datapoints": 0,
                                "rows": score(case, []), "source": case.get("source")})
                continue
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
