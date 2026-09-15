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
    python scripts/eval.py --profiles seed/holdout_profiles2/product_profiles.json \
                           --labels   seed/holdout_profiles2/product_profiles.json

`--profiles` scores the analog attributes a run derives - mechanism class,
route, approval era, therapy area, the roster the product launched into -
instead of its revenue, against the answer key named by `--labels`. How each
attribute is compared is declared in `scripts/profile_contract.py` rather than
decided here: a route arrives from a label in the FDA's spelling and from a
curated file in a person's, and a grouping key's wording is arbitrary on both
sides while what it groups is the whole point. Peer counts are relative to the
catalogue the server holds, so a set whose answer key counts peers within
itself needs a server holding that set and nothing else.

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
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from profile_contract import (
    COMPARISON,
    PARTITION,
    PRODUCED_BY,
    PROSE,
    partition_agreement,
    values_agree,
)

REPO = pathlib.Path(__file__).resolve().parents[1]

# The attributes compared one product at a time, in the order they are printed.
# Taken from the contract so an attribute added there is scored here.
SCORED_ATTRIBUTES = tuple(
    name for name, how in COMPARISON.items() if how not in (PROSE, PARTITION)
)

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


# Which keys of a case identify the drug rather than hold the answer. The API
# publishes them - a run takes a list of these objects - so they are read from
# the server rather than written here, where a field added to the input would
# be dropped from every run this starts and nothing would say so. The name is
# the schema's, so it goes stale only if the route stops taking that type.
DRUG_INPUT_SCHEMA = "DrugInput"


def drug_fields(base: str) -> set[str]:
    """The identifying keys of a case, from the schema the server publishes."""
    schema = (
        get(base, "/openapi.json")
        .get("components", {})
        .get("schemas", {})
        .get(DRUG_INPUT_SCHEMA, {})
        .get("properties", {})
    )
    if not schema:
        raise RuntimeError(
            f"the server publishes no {DRUG_INPUT_SCHEMA} schema, so which case "
            f"keys describe the drug cannot be read from it. Fix the route or "
            f"the schema name rather than listing the fields here: a list "
            f"written here drops a field added to the input, silently."
        )
    return set(schema)


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
        spread = {round(d["value_normalized_usd_millions"], 3) for d in published}
        if len(spread) > 1:
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
                     "candidates": len(here)})
    return rows


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


def load_cases(path: pathlib.Path) -> list[dict]:
    """The cases in a file that is either a bare list or a set with a preamble."""
    payload = json.loads(path.read_text())
    return payload["cases"] if isinstance(payload, dict) else payload


def load_labels(path: pathlib.Path) -> dict[str, dict]:
    """The expected attributes for each product, keyed by the product's name.

    An answer key is either a row per product - which is the shape gold's
    profiles are in - or a set of cases each carrying an `expect`. Both are read
    here so a set built as one does not have to be rewritten as the other, and
    the `expect` is flattened so a caller compares attributes either way.
    """
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        payload = json.loads(path.read_text())
        rows = payload.get("profiles") or payload.get("cases") or payload
    return {
        str(row["drug_name"]): {**row, **(row.get("expect") or {})} for row in rows
    }


def score_profiles(base: str, cases: list[dict], labels: dict[str, dict],
                   out: pathlib.Path, *, timeout_s: int, quiet: bool) -> int:
    """Run the profile stage for every case and compare what it derived.

    The pipeline is asked the way a person asks - a run, then the product page -
    so what is scored is what a reader would see, not what a reader could have
    got by calling the derivation directly with fields chosen by the test.
    """
    identifying = drug_fields(base)
    batches: dict[str, list[dict]] = {}
    for case in cases:
        options = dict(case.get("options", {}))
        batches.setdefault(json.dumps(options, sort_keys=True), []).append(case)

    started = []
    for index, (options_key, batch) in enumerate(batches.items(), 1):
        print(f"  [{index}/{len(batches)}] {len(batch)} drug(s), options {options_key[:70]}",
              flush=True)
        run_id = post(base, "/runs", {
            "drugs": [{k: v for k, v in case.items() if k in identifying}
                      for case in batch],
            "options": json.loads(options_key),
        })["run_id"]
        started.append((run_id, batch))

    deadline = time.time() + timeout_s
    status: dict[str, tuple[str, str]] = {}
    for run_id, _ in started:
        run = wait(base, run_id, timeout_s=max(1, int(deadline - time.time())), quiet=quiet)
        for job in run.get("jobs", []):
            status[job["drug_name"].casefold()] = (
                job["status"], str(job.get("current_step") or ""),
            )

    # A product is named by the label it was found under, which is the brand as
    # the FDA spells it and not as the case was typed. Matching exactly reported
    # every derived attribute as missing while the pipeline had produced it.
    catalog = {row["name"].casefold(): row["id"] for row in get(base, "/products")["products"]}
    rows = []
    for case in cases:
        name = case["drug_name"]
        want = labels.get(name) or {}
        product_id = catalog.get(name.casefold())
        observed = {}
        if product_id:
            observed = get(base, f"/products/{urllib.parse.quote(product_id, safe='')}").get(
                "analog_profile"
            ) or {}
        state, step = status.get(name.casefold(), ("no job", ""))
        rows.append({"drug_name": name, "product_id": product_id,
                     "job_status": state, "died_at": step,
                     "expected": want, "observed": observed})

    # A job that died fetching is not a derivation that refused. What separates
    # them is how far the job got: past the stage that derives these attributes
    # and they exist, short of it and there was never an answer to score.
    # Excluding on job status instead dropped every run that fell over later -
    # and those are not a random sample, so the score moved with them.
    order = {name: index for index, name in enumerate(get(base, "/pipeline/steps")["steps"])}
    floor = order.get(PRODUCED_BY, 0)
    scorable, short = [], []
    for row in rows:
        reached = order.get(row["died_at"], -1)
        (scorable if row["job_status"] in FINISHED or reached > floor else short).append(row)

    if short:
        print(f"\n  {len(short)} case(s) that never reached {PRODUCED_BY}, nothing to score:")
        for row in short:
            print(f"    {row['drug_name']:24} {row['job_status']:10} at {row['died_at'] or 'no step'}")
    late = [row for row in scorable if row["job_status"] not in FINISHED]
    if late:
        print(f"\n  {len(late)} case(s) whose job died after {PRODUCED_BY}, scored anyway:")
        for row in late:
            print(f"    {row['drug_name']:24} {row['job_status']:10} at {row['died_at']}")
    rows = scorable
    if not rows:
        print("\n  no case reached the stage being scored; nothing to score")
        return 2

    # Attributes compared one product at a time.
    tally: dict[str, dict[str, int]] = {}
    for row in rows:
        for attribute, how in COMPARISON.items():
            if how in (PROSE, PARTITION):
                continue
            want = row["expected"].get(attribute)
            got = row["observed"].get(attribute)
            if attribute not in row["expected"]:
                state = "no expectation"
            elif values_agree(attribute, want, got):
                # Two absences agree: the key expected a refusal and got one.
                state = "correctly silent" if want in (None, "") else "agreed"
            elif want in (None, ""):
                state = "ANSWERED ANYWAY"
            elif got in (None, ""):
                state = "not derived"
            else:
                state = "DISAGREED"
            tally.setdefault(attribute, {})[state] = tally.setdefault(attribute, {}).get(state, 0) + 1
            row.setdefault("verdicts", {})[attribute] = state

    print(f"\n  {'drug':24} " + " ".join(f"{a[:14]:>14}" for a in SCORED_ATTRIBUTES))
    for row in rows:
        print(f"  {row['drug_name'][:24]:24} "
              + " ".join(f"{row.get('verdicts', {}).get(a, '-')[:14]:>14}"
                         for a in SCORED_ATTRIBUTES))

    print("\n  per attribute:")
    for attribute in SCORED_ATTRIBUTES:
        counts = tally.get(attribute, {})
        print(f"    {attribute:34} " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    print("\n  grouping keys, scored on which products they put together:")
    partitions = {}
    for attribute, how in COMPARISON.items():
        if how != PARTITION:
            continue
        result = partition_agreement(
            {row["drug_name"]: row["expected"].get(attribute) for row in rows},
            {row["drug_name"]: row["observed"].get(attribute) for row in rows},
        )
        partitions[attribute] = result
        print(f"    {attribute:20} products={result['products_compared']:3} "
              f"pairs={result['pairs']:4} agreed={result['agreed']:4} "
              f"split={result['split']:4} merged={result['merged']:4} "
              f"agreement={result['agreement']}")

    out.write_text(json.dumps({"rows": rows, "attributes": tally,
                               "partitions": partitions}, indent=1, default=str))
    print(f"\n  detail written to {out}")
    wrong = sum(
        counts.get("DISAGREED", 0) + counts.get("ANSWERED ANYWAY", 0)
        for counts in tally.values()
    )
    return 0 if wrong == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", help="a JSON case file under seed/cases")
    ap.add_argument("--members", help="a member holdout under seed/holdout_members, "
                                      "scored through /members/resolve instead of a run")
    ap.add_argument("--profiles", help="a JSON case file whose products are scored on the "
                                       "analog attributes the run derives, not on revenue")
    ap.add_argument("--labels", help="the answer key --profiles is scored against: a jsonl "
                                     "of profile rows, or a json file with a `profiles` list")
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
    chosen = [name for name in ("cases", "members", "profiles") if getattr(args, name)]
    if len(chosen) != 1:
        ap.error("give exactly one of --cases, --members or --profiles")
    if args.profiles and not args.labels:
        ap.error("--profiles needs --labels: the attributes it derives are scored "
                 "against an answer key, and which one must be said out loud")

    if args.members:
        path = pathlib.Path(args.members)
        try:
            get(args.base, "/health", timeout=10)
        except (urllib.error.URLError, OSError) as exc:
            print(f"no API at {args.base} ({exc})")
            return 2
        return score_members(args.base, path if path.is_absolute() else REPO / path,
                             pathlib.Path(args.out))

    def resolved(value: str) -> pathlib.Path:
        given = pathlib.Path(value)
        return given if given.is_absolute() else REPO / given

    if args.profiles:
        cases = load_cases(resolved(args.profiles))
        if args.case:
            wanted = {c.strip().casefold() for c in args.case}
            cases = [c for c in cases if c["drug_name"].casefold() in wanted]
        if not cases:
            print("nothing selected")
            return 1
        try:
            get(args.base, "/health", timeout=10)
        except (urllib.error.URLError, OSError) as exc:
            print(f"no API at {args.base} ({exc})")
            return 2
        return score_profiles(args.base, cases, load_labels(resolved(args.labels)),
                              pathlib.Path(args.out), timeout_s=args.timeout,
                              quiet=args.quiet)

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
    DRUG_FIELDS = drug_fields(args.base)
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
                                "job_status": "missing", "error": "no job for this drug",
                                "sources_found": None, "datapoints": 0,
                                "rows": score(case, []), "source": case.get("source")})
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
            detail = get(args.base, f"/jobs/{job['id']}")
            datapoints = detail.get("datapoints") or []
            results.append({
                "run_id": scored_from["run_id"] if scored_from else created["run_id"],
                "job_id": job["id"],
                "drug_name": case["drug_name"], "manufacturer": case.get("manufacturer"),
                "job_status": job["status"], "error": job.get("error"),
                "scored_from_later_run": scored_from,
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
    if rescored:
        print(f"  {rescored} case(s) whose job did not finish were scored from a later run "
              f"of the same drug and window")
    unfinished = [r["drug_name"] for r in results if r["job_status"] not in FINISHED]
    if unfinished:
        print(f"  {len(unfinished)} case(s) with no finished job, scored as empty: "
              + ", ".join(unfinished))
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
