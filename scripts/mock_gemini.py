"""Run the pipeline with a stand-in for Gemini, answered by another model.

    cd backend && uv run python ../scripts/mock_gemini.py --cases cases.jsonl --cache /tmp/mock --out /tmp/m.jsonl

Every Gemini call the pipeline makes goes through `gemini_search.make_client`.
This replaces it with a client that answers each prompt from a reply file in
`<cache>/answers/<key>.txt`. On a miss it writes the prompt to
`<cache>/pending/<key>.md` (a PDF sent with it to `<cache>/docs/`) and waits
for the reply to appear; whoever answers - a person, or another model playing
Gemini - writes the reply text there, exactly as Gemini would return it.

A grounded call (one sending the google_search tool) is not stood in for: it
raises, so the route that needed it is recorded as an error and the
pipeline's other routes go on. Search is off by default for that reason.

Cases and scoring are the eval's own (`eval_pipeline.run`), so a run here
shows what the real run would, up to the stand-in's answers. Replies are kept,
so a second run replays them without asking again. Nothing here is a
pipeline input: the cache is answers to the pipeline's own questions about
documents it fetched, written by the stand-in, not read from gold.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

POLL_SECONDS = 2


class GroundedNotMocked(RuntimeError):
    """A grounded call: the stand-in cannot search the web on Gemini's behalf."""


class MockTimeout(BaseException):
    """No reply arrived in time. A BaseException, so no route's error handling
    swallows it and records a decline the real model would not have given."""


def _normalise(value):
    """The input with each document's bytes replaced by their hash, for keying."""
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, dict) and value.get("type") == "document":
        return {**value, "data": hashlib.sha256(value.get("data", "").encode()).hexdigest()}
    return value


class _Interactions:
    def __init__(self, cache: Path, wait: float):
        self.cache, self.wait = cache, wait
        self.lock = threading.Lock()

    def create(self, *, model=None, input=None, tools=None, **_):
        if tools and any(t.get("type") == "google_search" for t in tools):
            raise GroundedNotMocked("grounded search is not stood in for")
        key = hashlib.sha256(json.dumps(_normalise(input), sort_keys=True).encode()).hexdigest()[:20]
        answer = self.cache / "answers" / f"{key}.txt"
        if not answer.exists():
            self._ask(key, input)
            waited = 0.0
            while not answer.exists():
                if waited >= self.wait:
                    raise MockTimeout(f"no reply for {key} after {waited:.0f} s")
                time.sleep(POLL_SECONDS)
                waited += POLL_SECONDS
        text = answer.read_text()
        return {"id": f"mock-{key}", "status": "completed", "output_text": text,
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}]}

    def get(self, interaction_id):
        raise GroundedNotMocked("only background (grounded) calls are polled")

    def _ask(self, key: str, payload) -> None:
        pending = self.cache / "pending" / f"{key}.md"
        if pending.exists():
            return
        parts = payload if isinstance(payload, list) else [{"type": "text", "text": payload}]
        lines = []
        for part in parts:
            if part.get("type") == "document":
                doc = self.cache / "docs" / f"{key}.pdf"
                doc.write_bytes(base64.b64decode(part["data"]))
                lines.append(f"[A PDF document is attached to this prompt: {doc}]")
            else:
                lines.append(str(part.get("text", "")))
        with self.lock:
            pending.write_text("\n\n".join(lines))


class MockClient:
    def __init__(self, cache: Path, wait: float):
        self.interactions = _Interactions(cache, wait)


def install(cache: Path, wait: float) -> MockClient:
    """Point every module's Gemini client at the stand-in."""
    for sub in ("answers", "pending", "docs"):
        (cache / sub).mkdir(parents=True, exist_ok=True)
    client = MockClient(cache, wait)
    import importlib

    for name in ("gemini_search", "ir_archive"):
        module = importlib.import_module(f"app.retrieval.{name}")
        module.make_client = lambda client=client: client
    return client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--wait", type=float, default=1800, help="seconds to wait for one reply")
    parser.add_argument("--search", action="store_true", help="leave the search routes on (they will error)")
    args = parser.parse_args()
    install(args.cache, args.wait)
    from eval_pipeline import run

    cases = [json.loads(line) for line in args.cases.open() if line.strip()]
    print(f"cases={len(cases)} stand-in for Gemini, replies in {args.cache / 'answers'}", flush=True)

    def one(case):
        try:
            out = run(case, args.search)
        except MockTimeout as exc:
            out = {**case, "outcome": "no_reply", "route": None, "error": str(exc)}
        print(f"done {out['outcome']:<14} {case['product']} {case['period']}", flush=True)
        return out

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(one, cases))
    args.out.write_text("".join(json.dumps(r, default=str) + "\n" for r in results))
    print(f"totals: {dict(Counter(r['outcome'] for r in results))} of {len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
