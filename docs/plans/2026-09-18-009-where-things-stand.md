---
title: Where things stand - start here in a fresh session
date: 2026-09-18
status: current
---

# Where things stand

This is the one document to read first. It says what is on the branch, what
is scored and what is not, which decisions are open, and how to run the
things that produce a number. Everything it cites is in the repository; the
session that wrote it ran in an ephemeral container, and nothing it produced
outside this repository survives.

## The branch

All work is on `local/youthful-pascal-f1g68g`, pushed. `main` is still at
`744864d` and has none of it; merging to `main` is a decision nobody has
taken. The suite on the branch head: `cd backend && ./.venv/bin/pytest -q`
passes (1,029 tests at the time of writing; run it rather than trust the
number). If `DATABASE_URL` is set in your shell to a database this branch's
migrations have not seen, run with `env -u DATABASE_URL`.

## What was done, in order, and where it is written up

1. **Twelve reviews** of the product produced the register,
   `2026-09-17-006-what-twelve-reviews-found.md`: every defect found, with
   the command that showed it. Items that shipped are indexed there to their
   commit; sections 4, 10, 11 and 12 hold what is still open.
2. **The procedure**, `2026-09-17-007-verify-then-fix.md` (complete): eleven
   modules M0-M10, each verified by a read-only agent before an implementer
   touched it. One agent per module; verification reports were folded into
   the register before implementation began.
3. **M0-M10 implemented**, roughly 160 commits. Read `git log
   744864d..HEAD` - each commit message carries what it measured and how,
   because CLAUDE.md rule 5 keeps numbers out of the code.
4. **An audit of all of it**, `2026-09-17-008-what-the-audit-found.md`
   (complete): six read-only reviews asking of every change whether it was
   the simplest code for its value. Their full reports are under
   `docs/reviews/2026-09-17-audit/`. The audit's accepted items were
   implemented as M10's three tracks; its two behaviour changes were not
   (see "open" below).
5. **Two smoke runs** as a user would run them: the server on the code's
   declared settings plus the API key, the SEC contact string and the two
   model names; the held-out set and the gold sample. Outputs with their
   configuration headers are under `docs/evals/2026-09-17-smoke/`; the
   diagnosis is plan 008 sections 7 and 8. Three verification reports on that
   diagnosis are under `docs/reviews/2026-09-17-verification/`; two of them
   overturned the first diagnosis, and the corrected account is what plan
   008 now says.
6. **Fixes from the smoke diagnosis**, verified first, then implemented:
   the earnings exhibit read by its declared type instead of its filename;
   the coverage predicate given the extractor's guards (both merged, scored
   nowhere yet); the reader's metric gate, the footnote on the quote and the
   reconciled judge prompt (`d51e3bf`, `6ceee15`, `a22c8e5`); filing
   selection by the quarters asked (`a805d22`, `b366210`). Register 12e is
   the design behind the last two.

## What is merged but not yet scored (rule 4)

Two sets were drawn for this, from issuers no answer key uses, each with a
guard test that says so, each unspent until an eval is run against it:

| change | scored on | command |
|---|---|---|
| reader metric gate, footnote, judge prompt (`d51e3bf..a22c8e5`) | `seed/cases/holdout_2026_09_judging.json` | `scripts/smoke/smoke.sh seed/cases/holdout_2026_09_judging.json` |
| exhibit type, coverage guards, filing selection (`3c16d2b`, `6485077`, `a805d22`, `b366210`) | `seed/cases/holdout_2026_09_retrieval.json` | `scripts/smoke/smoke.sh seed/cases/holdout_2026_09_retrieval.json` |

Each needs a before number and an after number. "Before" is the parent of
the first commit in the change (`git worktree add --detach /tmp/before
<sha>^` and run the same command there); "after" is the branch head. Report
both with the configuration header the eval prints, and the two 12e numbers
for the retrieval set (quarters answered per document fetched, documents
fetched per quarter answered, as totals). Do not tune on the set; leave a
failure documented. The implementers that landed these had their evals
running when the session ended and reported no numbers.

## Open decisions - a person's, because they edit a scored key

Both are corrections to `seed/cases/holdout_2026_09.json` in the permitted
direction (reference data to answer key), each on a stated property, each
verified from the filing (plan 008 section 7; `docs/reviews/2026-09-17-verification/`):

1. **Translarna 2024Q4**: the key's 74.854 is a mixed-basis derivation no
   filing states; the issuer's as-reported figure is 93.7 (restated: 89.1,
   with the basis change at 2025Q4). The pipeline published 93.7.
2. **Upstaza**: the key expects `reported_as="Upstaza/Kebilidi"` on every
   valued quarter; the filer's 10-K calls it one product under two regional
   brands, and a pair label would put the whole series in the review queue.
   The pipeline published the figures under the product's own name.

With both corrected the held-out set reads 79 of 96 with no code change.
That set is spent for diagnosis and must not score anything further.

## Open items, ranked by what they cost the analyst

- **The two rule-4 scorings above.** Nothing else moves until they are run.
- **The auto-pass gate** (`deterministic:product_quote_value_ok`) accepts a
  number and a name anywhere in a two-column row. Verified in
  `docs/reviews/2026-09-17-verification/judging.md` B1: the unit rule costs
  ~19 model calls per job; the metric gate (landed) is the cheaper instrument.
  Whether anything more is needed is answered by the judging-set score.
- **The chart** (`frontend/src/pages/dashboardModel.ts`) still arbitrates
  readings with the old corroboration marker beside the backend's
  `series_selection`; plan 008 item 3.18, medium risk, needs its fixtures
  rebuilt. **The review queue** does not render the five fields it is sent
  (3.19).
- **Reconcile grouping vs series identity** (plan 008 item 2.6): 137 rows
  on run13 sit in groups that hold more than one identity; unifying is a
  behaviour change needing its own set.
- **M11 step 3, the cascade** (register 12e): the coverage verdict now has
  guards and still no consumer.
- **Layer 3 wiring** (analytics): 12 entry points `NOT_WIRED`; before wiring,
  `peak_sales.scope_key` must normalise geography and the dataclasses need
  the series identity (plan 008 item 3.20).
- **Test hygiene** deferred until the fix pass was over: one parametrised
  holdout guard over `answer_keys.answer_key_paths()` instead of six files;
  a `conftest.py` for the seven identical `_job()` fixtures; folding four
  one-concern test files (plan 008 items 21-23). Also
  `backend/tests/answer_keys.py::COMPANY_WORDS` is a hand list that falsely
  rejects an unspent issuer named "X Pharmaceutical Inc." (verified).
- **Register section 11 leftovers**: write amplification on the sync engine;
  the review page's one click per row; the export page's hand-typed UUID.
  `JOB_DEADLINE_SECONDS` is not in the env examples.
- **Register index**: items 0a-0c, 1a, 1b, 1d-1f and section 9 shipped by
  commits whose subjects name them in parentheses (`345835f`, `dd0df4d`,
  `299384d`, `1c65fdd`) and are not yet indexed.

## Sets: spent and unspent

`backend/tests/answer_keys.py::spent_issuers()` derives the list; do not
write it down. Spent for scoring: gold, `holdout`, `holdout2`,
`holdout_labels`, `holdout_members`, `holdout_foreign_xbrl`,
`shapes_holdout`, `holdout_2026_09` (read against two diagnoses). Unspent:
`holdout_2026_09_judging`, `holdout_2026_09_retrieval` - each for the change
it was drawn for, and only until its first eval.

## How to run a smoke test

`scripts/smoke/smoke.sh <case file>...` starts a server on the code's
declared settings (it unsets every `Settings` field the shell overrides,
keeping the API key, the SEC contact string and the two model names -
derived from `Settings.model_fields`, not listed), deletes
`backend/storage/workbench.db`, and runs `scripts/eval.py` per case file,
printing the configuration header above each score. `resume.sh` /
`resume2.sh` restart a server on an existing database and score with
`--attach`; the smoke server died twice in this session behind an OpenRouter
connection error, so expect to need them. Run detached (`setsid nohup`): a
tool timeout kills a foreground run. `seed/cases/gold_all.json` is 593
windows, one job each - two days at the shipped one-job-at-a-time default -
so the gold *sample* is the smoke-sized run; the full file is the oracle.

## What the smoke runs said

Held-out set, CIKs withheld, on `dde7fb0` (pre-M10): 72 of 96. Gold sample:
20 of 20. The loss classes and their verified causes are plan 008 sections
7-8; two of the four classes were the key, not the code. Every issuer
resolved from the label and the name alone.
