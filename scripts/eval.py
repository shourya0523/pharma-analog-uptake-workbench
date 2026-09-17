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
    python scripts/eval.py --members seed/holdout_members/combined_name_members.json

`--base` points at a running server. `--members` scores the member resolver
instead of the revenue pipeline: each case is a filer's XBRL member with the
siblings tagged beside it and the products it may name, posted to the same
route a reviewer would ask, and scored on whether it resolved to the expected
product or refused when it should. Each case is what a person would type -
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


def window_key(options: dict) -> str:
    return json.dumps({k: options.get(k) for k in OPTION_KEYS if k in options}, sort_keys=True)


def runs_by_window(base: str) -> dict[str, list[dict]]:
    """Every run on the server, newest first, keyed the way batches are: by
    their window."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = get(base, f"/observability/db/extraction_runs?limit=500&offset={offset}")
        rows.extend(page.get("rows", []))
        if offset + 500 >= page.get("total", 0):
            break
        offset += 500
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    found: dict[str, list[dict]] = {}
    for row in rows:
        found.setdefault(window_key(row.get("options_json") or {}), []).append(row)
    return found


def existing_runs(base: str) -> dict[str, str]:
    """The newest run already on the server for each window.

    So a client that died mid-way, or a second invocation, scores the runs it
    already started instead of starting them again.
    """
    return {key: rows[0]["id"] for key, rows in runs_by_window(base).items()}


# A job in one of these states ran to the end and has an answer to score.
FINISHED = {"ready_for_review", "completed"}

# The two spellings of "the whole product": a sentence says Worldwide, a
# schedule's family line says Product family. The pipeline groups a period's
# figures on the scope and merges only these two, so a U.S. figure and a
# worldwide figure for one quarter are two answers to two questions rather
# than a disagreement about one. This is a snapshot of that rule, and goes
# stale if the pipeline changes which scopes it merges; it is reproduced
# rather than imported because an eval that imports the pipeline measures a
# composition it chose itself.
WHOLE_PRODUCT_SCOPES = ("Product family", "Worldwide")


def scope_key(scope: str | None) -> str:
    """What a figure is a figure for, as the pipeline groups it."""
    return WHOLE_PRODUCT_SCOPES[0] if (scope or "") in WHOLE_PRODUCT_SCOPES else (scope or "")


def later_finished_job(
    base: str, *, drug: str, key: str, scored_run: str, runs: dict[str, list[dict]]
) -> tuple[str, dict] | None:
    """The newest finished job for this drug and window in a run started after
    the one being scored.

    A job that failed - the server restarted under it, a fetch timed out - is
    ordinarily run again by hand. Its fresh run is the pipeline's answer for
    the case, and this is what folds it into the score. Only runs newer than
    the scored one qualify: an older run scored older code.
    """
    rows = runs.get(key, [])
    since = next((str(row.get("created_at") or "") for row in rows if row["id"] == scored_run), "")
    for row in rows:
        if row["id"] == scored_run or str(row.get("created_at") or "") <= since:
            continue
        run = get(base, f"/runs/{row['id']}")
        for job in run.get("jobs", []):
            if job["drug_name"].casefold() == drug.casefold() and job["status"] in FINISHED:
                return row["id"], job
    return None


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

        # What a figure is a figure for is part of the answer. Where a filer
        # prints one line for two products it sells together, the value is the
        # pair's, and returning it under the asked product's name alone is a
        # different claim from the one the filing makes. A case says which by
        # carrying `reported_as`; one that says nothing expects the product's
        # own figure, which is `reported_as` empty.
        wanted_as = want.get("reported_as") or None

        def covers(datapoint: dict, wanted_as: str | None = wanted_as) -> bool:
            return (datapoint.get("reported_as") or None) == wanted_as

        # Two published figures that disagree is its own outcome, and the one
        # this must never do is pick between them: taking the first would score
        # a quarter correct whenever the right answer was among the answers,
        # and a caller given two contradictory figures has been given neither.
        #
        # They disagree only if they answer the same question. A quarter
        # reported as Worldwide, U.S. and ex-U.S. is three figures and no
        # conflict; scored without the scope it reads as a defect, and the
        # defect belongs to the scorer.
        by_scope: dict[str, set[float]] = {}
        for datapoint in published:
            by_scope.setdefault(scope_key(datapoint.get("revenue_scope")), set()).add(
                round(datapoint["value_normalized_usd_millions"], 3))
        spread = {value for values in by_scope.values() if len(values) > 1 for value in values}
        if spread:
            state = "published, conflicting"
            read = max(spread)
        elif target is None:
            state = "correctly silent" if not published else "answered anyway"
            read = published[0]["value_normalized_usd_millions"] if published else None
        elif any(near(d) and covers(d) for d in published):
            state, read = "published, correct", next(
                d["value_normalized_usd_millions"] for d in published if near(d) and covers(d))
        elif any(near(d) for d in published):
            state, read = "published, wrong identity", next(
                d["value_normalized_usd_millions"] for d in published if near(d))
        elif published:
            state, read = "published, WRONG", published[0]["value_normalized_usd_millions"]
        elif any(near(d) for d in here):
            state, read = "held for review", next(
                d["value_normalized_usd_millions"] for d in here if near(d))
        else:
            state, read = "no answer", None
        rows.append({"period": want["period"], "want": target, "read": read,
                     "state": state, "reported_as": want.get("reported_as"),
                     "method": (published or here or [{}])[0].get("extraction_method"),
                     "scopes": sorted(by_scope), "candidates": len(here)})
    return rows


def unexamined(case: dict, datapoints: list[dict]) -> list[str]:
    """The periods the run published that the case says nothing about.

    `score` walks the case's expectations, so a figure published for a period
    no expectation names is neither right nor wrong - it is invisible. A case
    file covering part of what a run answers reports a score for the part it
    covers and says nothing about the rest, which reads the same as a clean
    sweep. This is the size of that silence.
    """
    asked = {want["period"] for want in case["expect"]}
    return sorted({
        str(d.get("period")) for d in datapoints
        if d.get("period") and d.get("period") not in asked
        and d.get("validation_status") in PUBLISHED
        and d.get("value_normalized_usd_millions") is not None
    })


def score_members(base: str, path: pathlib.Path, out: pathlib.Path) -> int:
    """Resolve every member in a holdout through the API and count the answers.

    Both answers are scored: a member that must resolve to its product, and a
    member that must be refused because the figure it carries covers more than
    one. A resolver that always refuses passes the second and fails the first.
    """
    payload = json.loads(path.read_text())
    cases = payload["cases"] if isinstance(payload, dict) else payload
    rows = []
    for case in cases:
        try:
            got = post(base, "/members/resolve", {
                "issuer": case["issuer"], "member": case["member"],
                "siblings": case.get("siblings") or [], "candidates": case["candidates"],
            })
        except (urllib.error.URLError, OSError) as exc:
            got = {"product": f"ERROR {type(exc).__name__}", "decided_by": "", "note": ""}
        rows.append({**{k: case.get(k) for k in ("issuer", "member", "expected", "why")},
                     "got": got.get("product"), "decided_by": got.get("decided_by"),
                     "note": got.get("note"), "ok": got.get("product") == case["expected"]})

    resolve = [r for r in rows if r["expected"]]
    refuse = [r for r in rows if not r["expected"]]
    print(f"\n  {path}\n")
    for label, group in (("must resolve", resolve), ("must refuse", refuse)):
        print(f"  -- {label} --")
        for r in group:
            short = r["member"].split(":")[-1].replace("Member", "")
            print(f"   {'ok ' if r['ok'] else 'BAD'} {short:40} -> {r['got']!s:16}"
                  f" (wanted {r['expected']}; {r['decided_by']})")
            if not r["ok"]:
                print(f"        {r['why']}")
                if r["note"]:
                    print(f"        said: {r['note']}")
        print()
    good = sum(1 for r in rows if r["ok"])
    print(f"  resolve {sum(1 for r in resolve if r['ok'])}/{len(resolve)}   "
          f"refuse {sum(1 for r in refuse if r['ok'])}/{len(refuse)}   "
          f"correct: {good}/{len(rows)}")
    out.write_text(json.dumps(rows, indent=1, default=str))
    print(f"  detail written to {out}")
    return 0 if good == len(rows) else 1


# A setting whose name carries this is part of what was asked even when it is
# the value the code declares - the same document read by a different model is
# a different question - so it is printed whether or not it was overridden.
# Everything else is printed when the server says it differs from its default,
# which is a rule over the fields the server reports rather than a list of
# settings kept here.
ALWAYS_REPORTED = "model"


def _try_get(base: str, path: str) -> dict | None:
    """One attempt, no retry: a server that lacks a route is not a busy one."""
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=30) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def configuration(base: str, cases: pathlib.Path, started: list[tuple[str, str, list[dict]]]) -> None:
    """Print what this run is before it prints what it scored.

    Two things decide what a run answers and neither is in the case file. The
    options are the server's, resolved: a case states the few a person types
    and the server fills the rest in, so only the server can say what ran. The
    settings are the shell's - they reach the server from the environment it
    was started in, a re-run from a fresh shell gets the declared defaults
    instead, and nothing a run stores says which it had.
    """
    print("\n  configuration")
    print(f"    cases                 {cases} ({sum(len(b) for _, _, b in started)} case(s), "
          f"{len(started)} run(s))")
    print(f"    server                {base}")
    resolved: dict[str, list[str]] = {}
    for run_id, _, _ in started:
        options = get(base, f"/runs/{run_id}").get("options") or {}
        key = json.dumps({k: v for k, v in sorted(options.items()) if k not in OPTION_KEYS})
        resolved.setdefault(key, []).append(run_id)
    for key, run_ids in resolved.items():
        print(f"    resolved options      {key}")
        print(f"                          over {len(run_ids)} run(s), one window each")

    reported = _try_get(base, "/config")
    fields = (reported or {}).get("settings") or []
    if not fields:
        print("    settings              this server does not report them; the settings "
              "behind this number are not recorded anywhere")
        return
    shown = [f for f in fields if f["overridden"] or ALWAYS_REPORTED in f["field"]]
    print(f"    settings              {len(shown)} of {len(fields)} reported: every one this "
          f"server does not take from the code, and the models")
    for field in shown:
        default = "" if not field["overridden"] else f"   (code declares {field['default']!r})"
        print(f"      {field['field']:28} {field['value']!r}{default}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", help="a JSON case file under seed/cases")
    ap.add_argument("--members", help="a member holdout under seed/holdout_members, "
                                      "scored through /members/resolve instead of a run")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--case", action="append", default=[],
                    help="DRUG, repeatable; default is every case in the file")
    ap.add_argument("--timeout", type=int, default=7200,
                    help="seconds to wait for the whole sweep; every run is started "
                         "first and they proceed together, so this is one clock, "
                         "not one per run")
    ap.add_argument("--out", default="/tmp/eval.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--attach", action="store_true",
                    help="score runs already on the server for these cases' windows "
                         "instead of starting them again")
    args = ap.parse_args()
    if bool(args.cases) == bool(args.members):
        ap.error("give exactly one of --cases or --members")

    if args.members:
        path = pathlib.Path(args.members)
        try:
            get(args.base, "/health", timeout=10)
        except (urllib.error.URLError, OSError) as exc:
            print(f"no API at {args.base} ({exc})")
            return 2
        return score_members(args.base, path if path.is_absolute() else REPO / path,
                             pathlib.Path(args.out))

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
    rescored = 0

    # Every run is started before any is waited for. The windows are
    # independent and the server decides its own concurrency, so waiting for
    # one batch before submitting the next left the pool running one or two
    # jobs for much of a sweep and paid the slowest job of every batch in
    # turn. Scoring still happens a batch at a time, in the order the cases
    # were given.
    started: list[tuple[str, str, list[dict]]] = []
    for index, (options_key, batch) in enumerate(batches.items(), 1):
        options = json.loads(options_key)
        match_key = window_key(options)
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
        started.append((run_id, match_key, batch))

    configuration(args.base, path, started)

    deadline = time.time() + args.timeout
    for run_id, match_key, batch in started:
        created = {"run_id": run_id}
        run = wait(args.base, run_id,
                   timeout_s=max(1, int(deadline - time.time())), quiet=args.quiet)
        by_drug = {}
        for job in run["jobs"]:
            by_drug.setdefault(job["drug_name"].casefold(), []).append(job)
        later_runs: dict[str, list[dict]] | None = None
        for case in batch:
            job = (by_drug.get(case["drug_name"].casefold()) or [None]).pop(0)
            if job is None:
                results.append({"run_id": created["run_id"], "job_id": None,
                                "drug_name": case["drug_name"], "manufacturer": case.get("manufacturer"),
                                "job_status": "missing", "current_step": None,
                                "error": "no job for this drug",
                                "sources_found": None, "datapoints": 0,
                                "scored": False, "rows": [], "unexamined": [],
                                "source": case.get("source")})
                continue
            scored_from = None
            if job["status"] not in FINISHED:
                # The job did not run to the end. If it was run again by hand
                # and that run finished, the case is scored from there.
                if later_runs is None:
                    later_runs = runs_by_window(args.base)
                found = later_finished_job(
                    args.base, drug=case["drug_name"], key=match_key,
                    scored_run=created["run_id"], runs=later_runs,
                )
                if found:
                    scored_from = {"run_id": found[0], "job_id": found[1]["id"],
                                   "instead_of": {"job_id": job["id"], "job_status": job["status"],
                                                  "error": job.get("error")}}
                    job = found[1]
                    rescored += 1
            # A job that has not finished has not answered. Scoring it counts
            # the stages it had reached by the time it was read as the
            # pipeline's answer, and a mid-reconcile job holds every candidate
            # for a quarter at once - which scores as the pipeline
            # contradicting itself over a figure it had not finished choosing.
            finished = job["status"] in FINISHED
            datapoints = []
            if finished:
                datapoints = get(args.base, f"/jobs/{job['id']}").get("datapoints") or []
            results.append({
                "run_id": scored_from["run_id"] if scored_from else created["run_id"],
                "job_id": job["id"],
                "drug_name": case["drug_name"], "manufacturer": case.get("manufacturer"),
                "job_status": job["status"], "current_step": job.get("current_step"),
                "error": job.get("error"),
                "scored_from_later_run": scored_from,
                "sources_found": job.get("sources_found"),
                "datapoints": len(datapoints),
                "scored": finished,
                "rows": score(case, datapoints) if finished else [],
                "unexamined": unexamined(case, datapoints) if finished else [],
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
    print(f"\n  {total} expected figures across {sum(1 for r in results if r['scored'])} "
          f"scored case(s)")
    if rescored:
        print(f"  {rescored} case(s) whose job did not finish were scored from a later run "
              f"of the same drug and window")
    unfinished = [r for r in results if not r["scored"]]
    if unfinished:
        print(f"  {len(unfinished)} case(s) with no finished job, not scored and absent "
              f"from every count below:")
        for result in unfinished:
            print(f"    {result['drug_name'][:22]:22} {result['job_status']:18} "
                  f"{result.get('current_step') or ''}")
    for state, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"    {state:22} {n:>4}  {100 * n / total:5.1f}%")
    good = tally.get("published, correct", 0) + tally.get("correctly silent", 0)
    print(f"  correct: {good}/{total}")
    # What the case file did not ask about. The score is a fraction of the
    # expectations, so a figure published for a period no case names is
    # neither right nor wrong, and a case file covering half of what a run
    # answers reads exactly like one covering all of it.
    blind: dict[str, int] = {}
    for result in results:
        if result["unexamined"]:
            blind[result["run_id"]] = blind.get(result["run_id"], 0) + len(result["unexamined"])
    print(f"  {sum(blind.values())} published (product, period) pair(s) no expectation "
          f"examined" + (":" if blind else ""))
    for run_id, n in sorted(blind.items(), key=lambda kv: -kv[1]):
        print(f"    run {run_id[:8]}  {n:>4}")
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
    return 0 if total and good == total else 1


if __name__ == "__main__":
    sys.exit(main())
