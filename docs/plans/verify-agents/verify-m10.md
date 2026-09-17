---
name: verify-m10
description: Verifies the deletion candidates and the infrastructure claims - dead code with no caller, the job deadline, startup recovery and write amplification.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M10: items 10 and 11. Runs last, because a deletion is only safe once
every module above has reported what it depends on.

For each deletion candidate the question is the same and must be answered by a
search, not by memory: **does anything call this, in `app/`, in `scripts/`, in
`backend/tests/`, or through a route?** Report the three separately - a reader
kept only for a test is a different case from one kept for a script.

- **10** `_search_revenue_fallback` and `_search_quarters_fallback` and the
  code around them; `positional.py`; the 10 of 22 hold mechanisms that never
  fire and the 4 unreachable ones; `ValidationTaskORM.issues / judge_status /
  deterministic_results` empty across 18 databases; `lot_extractor.yaml`;
  `llm_search_max_queries`; `FileStore.public_uri`; the three unread
  `ExtractionOptions` fields; `tables.py`'s `extract_revenue_rows` half;
  `seed/holdout/` and `seed/holdout2/`.
  And the two marked "decide rather than leave": `adjudicate.py`, which has
  `seed/gold/adjudication_cases.jsonl` behind it, and `analytics/`.
- **11** no `wait_for` anywhere in `pipeline/`, `jobs/` or `main.py`; startup
  recovery marking a job failed at stage 12 of 13; one datapoint written and
  committed four times; `fetch_page` reaching sec.gov with no throttle and the
  guard test iterating a hand-written list of four names; the review queue
  having no bulk endpoint and 374 open tasks; the Export page needing a UUID.
