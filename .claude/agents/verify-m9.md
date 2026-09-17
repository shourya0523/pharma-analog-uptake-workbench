---
name: verify-m9
description: Verifies the claims about the project's own documentation, tests and evals - stale docs, false docstrings, the answer-key glob, the SCRIPT_ONLY reasons.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M9: `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/`, `backend/tests/`.
Items 9a-9i of the register.

- **9a** `extraction/adjudicate.py:29-32` cites `test_no_real_gold_row_needs_review`,
  which the register says exists only in that docstring.
- **9b** `backend/tests/answer_keys.py:22` globs `seed/*/*.json{,l}` and so
  misses `seed/holdout_foreign_xbrl.json`; `products_in()` reads `drug_name`,
  `member`, `expected` but that file keys its product as `product`. Verify both
  halves, and check whether its 8 issuers overlap any other key.
- **9c** belongs to M6; skip it.
- **9d** four `SCRIPT_ONLY` entries carry false reasons: `reading_rank` is
  already referenced at `orchestrator.py:1582`; `html_tables`,
  `extract_revenue_rows`, `read_positional_block` are kept for tests not evals
  and no eval could import them; `read_peak_sales_csv` has no caller anywhere
  and no route. Check every one of the ~30 entries, not only these four.
- **9e** the stale-doc table: the "five evals", the README's "without the flag"
  paragraph, AGENTS.md's 23 tests, `excluded-products.md`'s arithmetic, the two
  live docs disagreeing on the score, `CLAUDE.md:195`'s `scripts/eval_*.py`.
  Also `docs/pipeline.md` vs the stages `run_job` actually calls, and its
  self-contradiction on the bulk-tagged reader at `:141` and `:163`.
- **9f** tests that pass on data the pipeline cannot produce - `test_export.py`
  and the four analytics test modules.
- **9g** which holdout sets are referenced by anything; whether
  `holdout_labels` is loaded into the suite and so permanently tuned against;
  whether `test_gold_is_not_an_input.py` reads only one of gold's files.
- **9h** `check_by_hand.py`: does it read `source_quote` at all, does it pass
  `period_start=None`, does it skip annual rows, does it hardcode a contact
  address.
- **9i** `pdfs`, `random_validation_sampling`, `use_uploaded_template` have
  zero reads in `app/` yet are persisted into every run's `options_json`.

Run the suite yourself rather than assuming it passes, and report the count.
