# Audit: `744864d..830aad2`, scope `backend/tests/`, `scripts/`, `seed/cases/`, `seed/holdout_members/`, `docs/`, `.claude/agents/`

Snapshot: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/wt-audit` (detached at `830aad2`). Nothing edited anywhere.

**Baseline I measured once.** `cd <snap>/backend && ./.venv/bin/pytest -q --durations=25 -p no:cacheprovider` → **916 passed, 7 errors, 55.90s, 67,682 warnings**. The 7 errors are an environment artifact, not a defect — see F2. New test files: **24 files, 3,630 lines, 590 of them docstring (16%)**, measured with an `ast.get_docstring` walk over `git diff --diff-filter=A`.

**One procedural note.** `_product-brief.md` says not to read `docs/plans/…-006-*`; your brief puts plans 006 and 007 in scope and asks what could be cut. I read 006's front matter, its heading list and item-status markers — enough to judge length and staleness — and did not take any of its findings as my own. Every code finding below came from running something against the snapshot.

---

## A. Verified defects — ranked by what they cost the next person

### F1. Both plan documents say `status: not-started`, and ~30 of 68 items have shipped
**Wrong, not stale.** `docs/plans/2026-09-17-006-what-twelve-reviews-found.md:5` and `docs/plans/2026-09-17-007-verify-then-fix.md:5` both carry `status: not-started`.

```
$ grep -c '^### ' docs/plans/2026-09-17-006-...md          → 68
$ grep -n '^### .*fixed' docs/plans/...006...md            → 1   (only 0e)
$ git log --format='%s' 744864d..830aad2 | grep -oE '^[0-9]+[a-z]' | sort -u
  0d 0e 1c 3a 3b 3c 3d 3e 3f 3g 3h 3i 3j 5a 5b 5c 5d 5f 6a 6b 6c 6d 6e 6f 6g 6h 7a 7b 7c 7d
```
plus `bba9ba6 "Fold M10 verification into the register; the verification pass is complete"`. So the register's own procedure document is marked not-started *after* it declares itself complete, and 68 items carry one "fixed" marker between them. A reader picking up 006 today cannot tell which items are open. **Cost:** re-doing finished work, or re-verifying 68 claims to find out. This is the single most expensive doc finding.

**Do this:** set `status: complete` on 007 and either `status: in-progress` on 006 with a per-item `landed <sha>` marker, or cut 006 to the open items and move the closed ones to a one-line index (`item → commit`). 2,944 lines of narrative whose conclusions are now in 30 commit messages is the definition of a document that restates what the code enforces. **Risk:** the verification narrative (`[V]`/`[I]`/`[U]` provenance) is genuinely useful history — keep 007 whole (149 lines, tight, no findings of its own), keep 006's sections 4 (analytics), 10, 11, 12 which are still unshipped, and index the rest.

### F2. `test_review_moves_completeness.py:95` is the only test that runs the app lifespan, so it migrates the real database
```
$ ./.venv/bin/pytest -q tests/test_review_moves_completeness.py         → 7 errors
    alembic.util.exc.CommandError: Can't locate revision identified by '008'
$ env -u DATABASE_URL ./.venv/bin/pytest -q tests/test_review_moves_completeness.py → 7 passed
$ grep -n 'TestClient(' tests/*.py    → only this file uses `with TestClient(main.app)`
$ python -c "sqlite3.connect('$DATABASE_URL path').execute('select * from alembic_version')" → [('008',)]
```
The fixture builds an in-memory engine and monkeypatches `SessionLocal`, but `with TestClient(main.app)` runs startup, which calls `upgrade_database` against whatever `DATABASE_URL` points at. Here that is a database another worktree stamped at `008`; the snapshot's head is `007`. Four sibling files (`test_api_contract.py:30`, `test_members_api.py:36`, `test_products_api.py:159`, `test_the_server_says_what_it_is_running_with.py:25`) use bare `TestClient(...)` and are unaffected.

**Do this:** drop the `with` (the tests do not need lifespan) or monkeypatch `main.settings.database_url` to the tmp path. **Preserves:** everything. **Loses:** nothing. **Risk:** none.

### F3. `test_capabilities_are_wired.py` counts a reference from inside the defining module, so a whole unwired module passes
`app/connectors/coverage.py` is 321 lines, new in this range, with 310 lines of tests (`test_a_document_says_what_it_covers.py`, 16 tests). An AST walk of every `Name`/`Attribute`/`ImportFrom` in `app/`:
```
coverage          -> ['app/connectors/coverage.py']
DocumentCoverage  -> ['app/connectors/coverage.py']
record_coverage   -> []
```
`coverage.py:219` is the only call site of `coverage()`, and it sits inside `record_coverage`, which `SCRIPT_ONLY` at `test_capabilities_are_wired.py:73` itself declares `NOT_WIRED`. The guard's one-hop check (`test_capabilities_are_wired.py:125 if in_app.get(name)`) sees that edge and stops. So the docstring claim at lines 4-6 — "any public function or class in `app/` that nothing in `app/` references is either a capability nobody wired, or dead code" — is not what the code does: a chain of unwired functions calling each other is invisible, and so is a function referenced only in its own file.

**Do this:** in `_references`, skip the file the definition came from, and/or compute reachability from the framework roots (`main.py`, `api/`, `orchestrator`) rather than a flat reference set. **Preserves:** the derived-not-listed property the file is built on. **Loses:** nothing. **Risk:** it will surface more unwired names on the first run — which is the point.

Secondary: `_references` matches on bare attribute name, so an unrelated `.rank_analogs` attribute anywhere in `app/` would also count as wiring.

### F4. `test_gold_is_not_an_input.py`'s new-input detector matches only one spelling of a seed path, and has no vacuity floor
```
$ python -c "<the regex at test_gold_is_not_an_input.py:235 against four spellings>"
'SEED / "xbrl_members.csv"'                 -> []        # no match
'SEED / "reference" / "xbrl_members.csv"'   -> []
'path="seed/reference/xbrl_members.csv"'    -> []
'REPO / "seed" / "ref" / "members.csv"'     -> [('ref', None)]   # directory, no "." → dropped
```
It works today only because `app/extraction/members.py:47-48` and `app/extraction/elements.py:22` happen to spell the path inline as `... / "seed" / "file.csv"`. Hoisting a module-level `SEED = ... / "seed"` — the obvious tidy-up — blinds `_seed_files_the_app_reads()` to the empty set, and `test_a_new_file_the_pipeline_reads_has_to_be_declared` (`:244`) passes vacuously forever. Its four sibling assertions all carry a floor (`:214 assert evidence, "…would pass vacuously"`); this one does not.

**Do this:** add `assert _seed_files_the_app_reads(), "the detector found nothing; it has gone blind"`, and resolve the path by walking assignments rather than by matching the literal `"seed"`. **Risk:** none; one line.

### F5. `holdout_2026_09.json` hands the pipeline the CIK, and its own guard says it does not
`test_holdout_2026_09_is_held_out.py:192` — `test_nothing_hands_the_pipeline_a_document_or_a_figure`, docstring "The eval posts what a person types; the evidence stays on this side". It reads `DRUG_FIELDS` out of `eval.py` (good, derived) and forbids `sec.gov`, `api.fda.gov` and an accession pattern in those fields. `cik` is in `DRUG_FIELDS` (`eval.py:422`) and a bare CIK matches none of the three rules.
```
$ python -c "<count cik/ticker per case file>"
foreign_xbrl.json    cik: 0  ticker: 7  of 7
gold_all.json        cik: 0  ticker: 587 of 593
gold_sample.json     cik: 0  ticker: 8  of 9
holdout_2026_09.json cik: 12 ticker: 12 of 12
shapes_holdout.json  cik: 24 ticker: 24 of 24
unseen.json          cik: 0  ticker: 15 of 15
$ head -1 seed/example_drugs.csv
drug_name,generic_name,manufacturer,ticker,indication          # no cik column
```
A real upload has no CIK. So the two held-out sets score the pipeline with issuer resolution already answered — and issuer resolution is precisely what `2a1cf0d` ("read the label before asking EDGAR whose filings to download") rewrote and what `test_who_sells_it_is_asked_before_who_filed.py` defends. The set drawn to score section 8 skips section 8's own step. gold_all does not, so the two numbers are not comparable.

**Do this:** drop `cik` from the 12 cases (they all carry `ticker`, which a user does supply), or add `assert "cik" not in case` to the guard and say in the note that the CIK is deliberately withheld. **Loses:** a few cases may then fail on identity instead of on retrieval — which is the honest result. **Risk:** the score will drop; report it with the configuration.

### F6. `eval.py --attach` matches a run on the window alone, so it can score a different configuration
`eval.py:155 OPTION_KEYS = ("earnings_since", "earnings_until")`; `window_key` (`:79`) keeps only those; `--attach` (`:429, :441-442`) attaches on that key.
```
$ python -c "<load eval.py; compare two option dicts differing only in openfda/product_metadata>"
window_key equal? True   {"earnings_since": "2024-04-05", "earnings_until": "2025-05-05"}
```
So `--attach` will score a run started with `openfda: false` against cases asking `openfda: true`. The comment at `:152-154` explains why full-option matching was dropped (the server fills defaults in, so nothing ever matched) — the fix is to compare the *resolved* options of the candidate run against the case's options with defaults filled, not to drop every non-window key. The configuration header does print the attached run's resolved options, so a careful reader can see it; the score does not.

**Do this:** after attaching, compare `get(base, f"/runs/{run_id}")["options"]` against `{**ExtractionOptions().model_dump(), **case_options}` and refuse (or loudly warn) on a mismatch. eval.py may not import `app`, so pass the expected resolved options in from the case file, or compare only the keys the case sets. **Risk:** low; `--attach` starts failing on sweeps that were silently mismatched, which is the point.

### F7. `check_by_hand.py --period-type` is undocumented, unused, and silently shrinks the denominator
Added in `35c0ad9`. Used at `check_by_hand.py:292`, never printed. The tally at `:367` says `"{checked} published figures checked"` with no mention of the filter, so `--period-type quarterly` produces a smaller, better-looking number with nothing on the page saying so. Referenced in no doc (`docs/evaluation.md:84` shows only `--run`); no test opens `check_by_hand.py` at all (`grep -rln check_by_hand backend/tests/ scripts/` → only the script itself).

**Do this:** print the filter in the summary line, or delete the flag until something uses it. **Risk:** none.

### F8. `docs/pipeline.md`'s stage table runs the pipeline backwards
`docs/pipeline.md:15-16` lists `_identity` then `_retrieve`. An AST walk of `run_job` gives the real order:
```
537 _expand_aliases  541 _retrieve  542 _parse  543 _extract_metadata
544 _identity        545 _retrieve  547 _parse  549 _judge_profile  550 _extract_revenue …
```
Commit `2a1cf0d` reversed exactly this, and `test_who_sells_it_is_asked_before_who_filed.py:111` asserts `label < metadata < identity < filings`. The table also omits `_expand_aliases` entirely. The rest of the table (the four search-fallback rows, `_record_*`) was correctly updated in this range; the first two rows were not.

Also `docs/pipeline.md:21` cites `config.py:53` for `enable_llm_search`; `grep -n` puts it at `app/config.py:55`. (`config.py:42` for `notes_dataset_dirs` is correct.)

### F9. `eval.py --members` prints no configuration header at all
`main()` returns at `:395` before `configuration()` is called at `:455`. But `/members/resolve` (`app/api/members.py:57`) calls `LLMModules().resolve_xbrl_member` — a model. So the one eval path that is *purely* a prompt score prints no model, no date, no case count, which is exactly what `CLAUDE.md:195-198` requires beside a number. The docstring at `eval.py:308-312` describes `ALWAYS_REPORTED = "model"` as the rule and it never fires for this path.

**Do this:** call a slimmed `configuration()` (date, path, case count, `/config` settings) from `score_members` before the tally. ~5 lines.

### F10. `docs/evaluation.md` omits a case file it makes a claim about, and names one published status when the code counts two
- The table at `:36-42` lists 5 files. `seed/cases/` holds 6; `holdout_2026_09.json` (12 cases, 96 expectations) is missing, while line 47 claims the property "in every file here" — and `test_the_eval_runs_the_pipeline.py:104` globs the directory, so the test enforces the claim on a file the table does not mention.
- `:9` "scores what the pipeline **published** — `auto_pass`, the only status…"; `eval.py:41` `PUBLISHED = {"auto_pass", "confirmed"}`.
- `:40` "7 runs, 7 quarters" for `foreign_xbrl.json` — happens to be right today (7 cases, 7 expectations), but it is a hand-kept count in a table whose two gold rows were deliberately rewritten in this range to stop quoting counts. Make it consistent.

### F11. Two of the case files' option fix is unprotected
`87c9d47` flipped `openfda`/`product_metadata` to the shipped defaults across all six case files (verified: every case in all six now sets `openfda: true, product_metadata: true`, matching `ExtractionOptions()`). But only `test_holdout_2026_09_is_held_out.py:173` asserts option *values*. `test_gold_cases_are_derived.py:110` and `test_shapes_holdout_is_held_out.py:129` check option *names* only, and nothing at all pins `seed/cases/unseen.json` or `seed/cases/foreign_xbrl.json`. (`test_foreign_xbrl_holdout_is_held_out.py` guards `seed/holdout_foreign_xbrl.json`, a different file.) The defect 0d fixed can walk straight back into two of six files.

**Do this:** fold the value check into the directory-globbing test in `test_the_eval_runs_the_pipeline.py`, where it covers every case file by construction. 6 lines, removes 2 per-file copies.

---

## B. Duplication — the largest simplification available

### D1. Five guard files assert the same four properties
| property | files |
|---|---|
| `test_no_case_comes_from_a_scored_issuer` | `test_combined_name…:25`, `test_shapes…:36`, `test_foreign_xbrl…:48`, `test_holdout_2026_09…:52`, `test_product_disambiguation_holdout.py:23` |
| `test_both_answers_are_represented` | the same four, plus `test_gold_cases_are_derived.py:83` |
| `test_windows_reach_every_expected_quarter` | `test_shapes…:76`, `test_holdout_2026_09…:153`, `test_gold_cases_are_derived.py:142` — with the quarter-end arithmetic written out longhand twice (`test_shapes…:88-90`, `test_holdout_2026_09…:165-167`) and imported once |
| options-are-declared | `test_gold_cases_are_derived.py:110`, `test_shapes…:129`, `test_holdout_2026_09…:173` |
| `_ACCESSION` / `_PERIOD` regexes | `answer_keys.py:96`, `test_shapes…:28-29`, `test_holdout_2026_09…:32-33` |

`wc -l` on the eight answer-key files: **1,040 lines** (`answer_keys.py` 169, `test_holdout_2026_09` 285, `test_gold_cases_are_derived` 154, `test_shapes_holdout` 145, `test_combined_name` 83, `test_product_disambiguation` 79, `test_foreign_xbrl` 68, `test_every_answer_key_is_read` 57).

Also duplicated: `test_foreign_xbrl_holdout_is_held_out.py:41 test_the_holdout_is_discovered_at_all` and `test_every_answer_key_is_read.py:28 test_the_glob_reaches_a_key_directly_under_seed` are the same assertion about the same glob.

**Do this:** one `test_answer_keys_are_held_out.py` that parametrizes over `answer_keys.answer_key_paths()` (the discovery function already exists and is already guarded) and applies the four shared properties; leave a short per-set file for what is genuinely unique — foreign_xbrl's `"a figure"`/`"nothing"` string expectations, the member holdout's candidate/roll-up rules, the `_REQUIRED_SHAPES` tag lists. **Saves:** ~350-400 lines and four copies of a property. **Preserves:** every property currently asserted, on *more* files than today (a new case file is covered the day it lands rather than when someone copies a guard). **Loses:** the per-file docstrings that argue why each set exists — keep those as the set's own `note` field in the JSON, which `holdout_2026_09.json` already does well. **Risk:** low; run once and check the failure count is zero.

### D2. A byte-identical `_job()` in seven test files, and five spellings of `_reconcile`
```
$ for f in *.py; do md5sum of the `def _job()` block; done | group
  test_a_corroborator_is_looked_at_again.py      (new)
  test_a_nine_month_figure_is_not_the_quarter.py (new)
  test_a_tagged_fact_is_not_a_worse_source.py    (new)
  test_a_refusal_is_not_an_absence.py
  test_a_reply_of_bare_strings_is_not_a_job_ending.py
  test_a_verdict_names_a_candidate.py
  test_one_figure_one_publication.py
$ grep -ln "def _reconcile" *.py   → the same set, 5 files, 5 signatures
```
Near-identical variants in `test_a_label_flag_survives_the_gate.py:35`, `test_the_judge_sees_the_row.py:44`, `test_who_sells_it_is_asked_before_who_filed.py:48`. On top of that, `create_engine("sqlite:...")` appears in 38 files and there is **no `conftest.py` anywhere** (`ls backend/tests/conftest.py` → not found; `backend/conftest.py` → not found), so there is no place for a shared fixture to live. `tests/answer_keys.py` is the precedent that a shared helper module is acceptable here.

**Do this:** add `backend/tests/orchestrator_fixtures.py` (or a `conftest.py`) with `job_db()`, `datapoint_row(**overrides)` and one `reconcile()`. **Saves:** ~150-200 lines and, more importantly, stops the next agent adding an eighth copy. **Risk:** low.

### D3. Eight new files that are one concern each, with an existing home
Mapped by shared `app.*` imports:

| new file (lines) | fold into |
|---|---|
| `test_a_nine_month_figure_is_not_the_quarter.py` (77) | `test_one_figure_one_publication.py` — same `_job`, same `_reconcile`, same module |
| `test_a_tagged_fact_is_not_a_worse_source.py` (129) | same |
| `test_a_corroborator_is_looked_at_again.py` (212) | same |
| `test_a_label_flag_survives_the_gate.py` (96) | `test_publication_gate.py` |
| `test_a_slash_joins_one_product_or_two.py` (64) | `test_a_pair_is_published_as_the_pair.py` |
| `test_a_finding_names_a_cell.py` (128) | `test_extraction_stack.py` / `test_quality_flags.py` |
| `test_every_answer_key_is_read.py` (57) | `answer_keys`' own guard set (D1) |
| `test_foreign_xbrl_holdout_is_held_out.py` (68) | D1 |

I would fold the first four and D1's two; the rest are marginal. **Risk:** merge conflicts against the live tree — do this after the current fix pass lands, not during.

### D4. Docstring narrative in the new tests restates the commit messages
590 of 3,630 lines (16%) in the 24 new files are docstring, and nearly every module docstring is a before-and-after: `test_a_corroborator…:3-12`, `test_a_derivation…:3-10`, `test_a_label_flag…:3-6`, `test_a_nine_month…:3-6`, `test_a_slash…:3-9`, `test_coverage_counts…:3-9`, `test_powerbi_csv…:3-9`. CLAUDE.md rule 5 puts a before-and-after in the commit message, and those commit messages exist and are good. Three carry measured results outright:
- `test_a_tagged_fact_is_not_a_worse_source.py:7-8` — "Every tagged fact **in the run databases** sits in the lower band" (falsifiable only by re-running run7/run13).
- `test_a_document_says_what_it_covers.py:4` — "a run that fetched **28** documents".
- `test_powerbi_csv_is_what_it_is_named.py:7-9` — "The **eight** columns it wrote … they dropped `period_type`, `reported_as` and **ten more**".

Commit `fd153ad` ("Rule 5: take the counts and the histories out of the docstrings") cleaned `scripts/` and `app/` and stopped at `backend/tests/` — where the density is highest. Pre-existing and worse in `test_gold_dataset.py` (UTHR 37.1%, "Winrevair read 0%", "dataset was 67% one issuer"), which is out of this range but is the same class.

**Do this:** in the new files, cut each module docstring to the *shape* it defends (2-4 lines) and delete the three measured numbers. **Saves:** ~250 lines. **Loses:** nothing a reader cannot get from `git log -S`.

---

## C. Tests that hold a number rather than a shape

Only one cluster, all in the new holdout guards:
```
test_holdout_2026_09_is_held_out.py:66   assert len(stated) >= 40
                                  :67   assert len(empty)  >= 20
                                  :75   assert len(mixed)  >= 3
                                  :282  assert 3 <= len(issuers) <= 5      # file has exactly 5
                                  :283  assert 8 <= len(cases)   <= 12     # file has exactly 12
                                  :285  assert 6 <= len(expect)  <= 10
test_shapes_holdout_is_held_out.py:50-51 assert len(stated) >= 60; len(empty) >= 10
test_combined_name_…:41-42               >= 8; >= 5
test_foreign_xbrl_…:59-60                >= 3; >= 3
```
The *property* is "both answers are represented, and at least one case mixes them". The floors add nothing to that; the **ceilings** actively prevent the set growing — `holdout_2026_09.json` sits at exactly 5 issuers and exactly 12 cases, so adding a thirteenth case fails a test that has no stated reason to care. **Do this:** keep `assert stated and empty and mixed`, drop the six magic numbers, and let the file's `note` (which is excellent — it names what spends the set) carry the intended size.

Everything else numeric I checked is derived from the test's own fixture (`test_coverage_counts_published_quarters.py:94-213`, `test_powerbi_csv…:105`) — fine.

## D. Tests pinning implementation detail rather than behaviour
- `test_the_eval_scores_one_question_at_a_time.py:97` — `assert '"scored": finished' in source`. An exact source substring; `ruff` reformatting or renaming a local breaks it, and it asserts nothing about behaviour. The behavioural version is one line: call `ev.score()` / check the result record. Note commit `830aad2` is itself "Apply ruff's safe fixes" — this test is one reformat away from a false failure.
- `test_the_eval_runs_the_pipeline.py:61-68` — `"/runs" in source`, `"/jobs/" in source`, `"auto_pass" in source`. Passes if the strings appear in a comment.
- `test_the_server_says_what_it_is_running_with.py:106-109` — asserts on `main._reportable`, a private name.
- `test_product_disambiguation_holdout.py:77-79` pins three literal fixture labels (`"Epidiolex/Epidyolex"`, `"Rylaze/Enrylaze"`, `"Defitelio/defibrotide"`) — the exact practice `test_combined_name_holdout_is_held_out.py:60-62` says broke "the moment the set was rebuilt". Make it a property: any label whose two sides are aliases of one product resolves `own`.

## E. Tests that cannot fail, or fail for the wrong reason
- `test_capabilities_are_wired.py:174 test_the_list_says_which_kind_each_entry_is` — `SCRIPT_ONLY`'s values are the two module constants defined twelve lines above. It can only fail on a typed literal. Cheap (5 lines) but it is a tautology; the same check belongs as an `else: wrong.append(...)` in `test_every_exception_is_what_it_says`.
- `test_run_options_are_read.py:47` — the "is read" predicate (`:24-44`) collects **every attribute name and every string literal in `app/`**. Measured:
```
quarterly_revenue   attribute-access in 0 file(s), string-literal in 1
product_metadata    attribute-access in 0, string-literal in 1
openfda             attribute-access in 0, string-literal in 2
… 9 of 10 options are "read" only because their name appears as a string somewhere
```
The pipeline does read options by string key, so these are not false positives today — but the predicate cannot tell `options.get("openfda")` from `"openfda"` in a prompt, a log line or a docstring constant. It will catch a newly-added dead option (whose name appears nowhere); it will not catch an option that *stops* being read. Narrow the constant match to strings used as a `.get()` argument or subscript.
- `test_who_sells_it_is_asked_before_who_filed.py:65` — seven monkeypatches, including a replacement `_extract_metadata` that itself sets `job_.manufacturer = "Acme Pharma"`. The assertion `asked == [(None, "Acme Pharma")]` at `:113` is then guaranteed by the test's own stub. The *ordering* claim (`:111-112`) is real and worth keeping; the "the sponsor comes from the label" claim in the docstring is not exercised.

## F. Runtime
```
9.88s  test_the_judge_sees_the_row.py::test_a_quote_that_is_only_the_sibling_row_is_vetoed   (new)
7.88s  test_a_label_flag_survives_the_gate.py::test_every_label_flag_holds_the_row           (new)
7.65s  test_jobs_share_one_database.py::test_a_job_that_fails_inside_a_commit_is_recorded_as_failed
3.98s  test_earnings_sources.py::test_the_window_reaches_one_reporting_lag_back_and_no_further
3.19s  test_no_answer_key_reaches_the_prompts.py::…
```
The two slowest tests in the whole 55.9s suite are new, and together are 32% of it. `test_every_label_flag_holds_the_row` rebuilds engine + schema once per member of `LABEL_FLAGS` (`:89-95`) — a module-scoped engine fixture fixes it. I did not isolate the 9.88s one; worth one profile. Separately: **67,682 warnings** and no `filterwarnings` in `backend/pyproject.toml` — one `[tool.pytest.ini_options] filterwarnings` line would stop 67k lines of `datetime.utcnow()` noise burying the 7 real errors.

## G. Scripts — flags and the configuration header
`eval.py` flags: `--cases`, `--members`, `--base`, `--case`, `--timeout`, `--out`, `--quiet`, `--attach`. `--attach` and `--case` are documented (`docs/evaluation.md:70,75`); `--timeout`, `--out`, `--quiet` are not, but are ordinary and harmless. `--case` shrinks the scored set; the header does print the reduced case count (`eval.py:339-341`) so it is detectable, though not labelled as filtered. `--attach` is F6.

`build_gold_cases.py` flags: `--check` (used by `test_gold_cases_are_derived.py:131`, documented at `docs/evaluation.md:54`) and the `--openfda/--no-openfda`, `--product-metadata/--no-product-metadata` pairs, which are **generated from `LAYER_TWO_DEFAULTS` rather than written down** (`:328-332`) and whose defaults come from `ExtractionOptions.model_fields` (`:104-106`). That is the derived form rule 1 asks for, and `test_the_check_says_so_in_its_exit_status` (`:123`) exercises the negation without naming a flag. Nothing to simplify.

**Does `configuration()` do what its docstring says?** Yes, for `--cases`: it prints the date (`:338`), the case file with case/expectation/run counts (`:339`), the server, the resolved options per window read back from `/runs/{id}` (`:343-350`), and the settings from `/config` filtered to overridden-or-model (`:352-363`) — and `test_the_server_says_what_it_is_running_with.py` holds `/config` to reporting every declared field and redacting credentials two different ways. It is the strongest thing in this diff. The hole is F9 (`--members` never reaches it).

`ISSUER_TICKERS` (`build_gold_cases.py:113-121`) is a hand list, but `seed/product_attributes.csv` has no ticker column (`head -1` → `drug_name,moa,moa_class,route_of_administration,first_approval_year,indication_area,attribute_provenance,peer_universe_role`), so there is no in-repo producer, and `_ticker` raises `SystemExit` on an unknown issuer rather than writing a bad case. That is rule 1's permitted form. Leave it.

## H. Agent definitions — 11 of 19 are history
`.claude/agents/verify-m0..m10.md` (11 files, 377 lines) are one-shot: each names 006 item numbers and pins line references that have already moved.
```
verify-m3.md:17  "client.py:828 calls re_ytd_language(q)"  →  app/llm/client.py:824 is `def apply_judge_hard_vetoes(`
verify-m3.md:22  "test_one_figure_one_publication.py:172"  →  line 172 is now blank
```
The pass is declared complete (`bba9ba6`). Invoking `verify-m3` today points an agent at line numbers that no longer exist and at a register whose items are fixed. **Do this:** move the 11 `verify-m*.md` into `docs/plans/` beside 007 (they are the procedure's record) or delete them; keep `_verification-protocol.md` (88 lines — it is a reusable method, not a one-shot) and the 8 reviewer definitions, which are reusable by module. **Saves:** 377 lines out of the agent directory, and stops the next person invoking a stale agent.

---

## Ranked: do these, in this order

1. **Fix the two `status: not-started` headers and index 006's closed items** (F1). Removes the trap that costs most; ~2,000 lines could go to an index. Risk: losing verification provenance — keep sections 4/10/11/12 and 007 whole.
2. **Drop the `with` from `test_review_moves_completeness.py:95`** (F2). 1 line; 7 tests stop depending on the ambient `DATABASE_URL`. No risk.
3. **Close the `test_capabilities_are_wired.py` self-reference hole** (F3). ~4 lines; makes a 321-line unwired module visible. Risk: it will fail on landing — that is the finding.
4. **Merge the five holdout guards into one parametrized file** (D1). −350-400 lines, four fewer copies, new case files covered automatically. Risk: low, one run confirms.
5. **Add `backend/tests/conftest.py` with `job_db` / `datapoint_row` / `reconcile`** (D2). −150-200 lines, stops the eighth copy. Risk: low; do it after the fix pass lands.
6. **Drop `cik` from `holdout_2026_09.json` and add `assert "cik" not in case`** (F5). The set stops skipping the step it was drawn to score. Risk: the number drops; report it.
7. **Make `--attach` compare resolved options** (F6) and **give `--members` a configuration header** (F9). ~15 lines between them; both guard a number someone will quote.
8. **Fix `docs/pipeline.md:15-16` order, add `_expand_aliases`, correct `config.py:53`→`:55`** (F8), and **add `holdout_2026_09.json` to the `docs/evaluation.md` table and fix the `auto_pass` sentence** (F10). ~10 lines.
9. **Delete the six magic size bounds in the holdout guards** (§C) and **cut the three measured numbers out of the new test docstrings** (D4). ~260 lines of prose, six brittle assertions.
10. **Move the 11 `verify-m*.md` to `docs/plans/`** (H), **print the `--period-type` filter in `check_by_hand.py`** (F7), **add `filterwarnings`** (§F).

## Leave exactly as they are

- **`scripts/build_gold_cases.py`** and `test_gold_cases_are_derived.py`. `test_every_quarter_gold_holds_is_expected_exactly_once` (`:62`) is the right property, the flag pairs are generated from the options model, and the `--check` exit status is tested by negation without naming a flag. Already as simple as the value allows. (The 32,729-line `gold_all.json` diff is a real cost — but generating it at eval time would lose the ability to see which product-years changed, and the `--check` exit status already catches drift. I would not change it.)
- **`backend/tests/answer_keys.py`** and `test_every_answer_key_is_read.py`. Discovery by glob, guarded against yielding nothing, with the key-name snapshot explicitly marked and watched. This is rule 1 done properly; it should be the *home* for D1's merge, not a merge target.
- **`seed/holdout_members/combined_name_members.json`**. The diff replaced a written-down list of 25 excluded issuers with a pointer to `spent_issuers()`. Exactly right.
- **`docs/sourcing/excluded-products.md`**. Dates itself, prints the commands, and says which of its own old figures were wrong and why the ratio is not recomputed. (Verified: `wc -l seed/gold/excluded_products.jsonl` → 6, `quarterly_revenue.jsonl` → 2203, as the document states.) This is the model the other docs should copy.
- **`eval.py`'s `configuration()` and `unexamined()`**, and `test_the_server_says_what_it_is_running_with.py`. The header, the "N published (product, period) pairs no expectation examined" line, and the `/config` redaction tests are the best work in this range.
- **`README.md`'s removal of the quoted score.** Replacing "1,070 of 1,415 (75.6%)" with "the score is not written down here, because it is not a number on its own" is the right call and should not be reverted under pressure to have a headline.
