## Audit: judging (`llm/`, `prompts/`, `quality/` minus sentences/candidate_filters/profile, `extraction/prose.py`), `744864d..830aad2`

Snapshot root (all paths below are under it):
`/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/wt-audit`
Scratch scripts: `.../scratchpad/audit-judging/replay.py`, `.../scratchpad/audit-judging/run13.db` (read-only copy of `run13/workbench.db`). Nothing edited.

**Suite status.** `cd <SNAP>/backend && env -u DATABASE_URL ./.venv/bin/pytest -q -p no:cacheprovider` → **923 passed** in 170s. Run *without* `env -u DATABASE_URL` it gives 7 errors in `test_review_moves_completeness.py` — those are an environment artifact, not a regression: `DATABASE_URL=sqlite+aiosqlite:////Users/shouryayadav/.pharma-workbench-rerun/workbench.db` exists on this box and is stamped at alembic revision `008`, which this snapshot (head `007`, `ls <SNAP>/backend/alembic/versions`) cannot resolve. But it exposes a real fragility: `TestClient(main.app)` runs the lifespan → `app/main.py:87 init_db()` → `app/db/models.py:614 upgrade_database(engine)` against the **ambient** `DATABASE_URL`, even though the test monkeypatched `SessionLocal`. Every API test in this repo migrates whatever database the environment points at. (Verified: `env -u DATABASE_URL pytest tests/test_review_moves_completeness.py tests/test_completeness.py` → 12 passed.)

---

## 1. The headline measurement: does the judge ever overrule a deterministic reader?

Replay of the current `apply_judge_hard_vetoes` over all 549 run13 datapoints, candidate rebuilt from the stored columns, product+generic from `drug_jobs`:

```
rows: 549   vetoed: 134
veto                                              fires  alone
value_and_product_in_different_sentences            124    118
milestone_or_license_revenue                          8      5
product_missing_from_quote                            8      1
quote_states_a_different_period                       3      1
company_total_without_product                         0      0
change_not_level                                      0      0
ytd_language_as_quarterly                             0      0
other_brand                                           0      0   (needs peers; 0 per 05498fc too)
```

And the deterministic judge's own outcome distribution (`llm_skip_judge_when_deterministic` defaults **True**, `app/config.py`):

```
293  deterministic:product_quote_value_ok       (auto_pass, no model)
124  hard_veto:value_and_product_in_different_sentences
 86  deterministic:non_quarterly_period_type
 36  <None — the LLM judge actually runs>
  8  hard_veto:milestone_or_license_revenue
  1  hard_veto:quote_states_a_different_period
  1  hard_veto:product_missing_from_quote
```

**513 of 549 rows (93%) never reach a model.** The LLM judge runs on 36.

Deterministic readers (`xbrl_fact`/`table`/`derived_from_period_total`) that did not end published: 12 rows. Of those, **exactly one** was held by the judge — AGAMREE 2025Q1 `table`, `hard_veto:quote_states_a_different_period`. The other eleven were held by `filing_contradicts_itself` (comparative.py), `label:combined_line` / `label_not_understood` (labels), or the search validator's `contradicted`. On this database the judge overrules a deterministic reader **once in 549**.

That is the answer to the central question, and it is not an argument for deleting the judge — it is an argument that the vetoes that never fire are carrying complexity for nothing, and that the one gate that decides 93% of rows (`product_quote_value_ok`) is under-specified.

---

## 2. Per change

### `ff97066` — "6b/6c: one period namespace on both sides of the judge's period checks"
Files: `<SNAP>/backend/app/extraction/prose.py:116-245`, `<SNAP>/backend/app/llm/client.py:790-926`, `<SNAP>/backend/app/quality/fast_judge.py:7,85`.

**Value — measured, and I reproduced it.** The commit claims `quote_states_a_different_period` 142→1 and `ytd_language_as_quarterly` 52→0. My independent replay gives 3 and 0 (the 3 = the commit's 1 survivor + its 2 newly-vetoed FYCOMPA stubs; consistent). The year-cross in `periods_named_in` is doing the work and is doing it correctly: **76 rows are saved from a spurious veto only by the cross**, and sampling them shows they are all comparative-year columns of real product-revenue schedules (FIRDAPSE 2023Q1 quoted from a "Three Months Ended March 31, / 2024 / 2023" table). Real value, honestly measured.

**Complexity cost.** `_Period` loses a field and gains a property (`prose.py:118-126`); two new regexes `_YEAR_TOKEN_RE`, `_QUARTER_OF_KEY_RE` (`prose.py:131-136`); two new public functions `spans_named_in`, `_spans_named_in` (`prose.py:166-187`); three module constants in `client.py:794-804` (`_YEAR_TO_DATE_SPANS`, `_QUARTER_SPAN`, `_SPAN_OF_PERIOD_TYPE`) and one new function `_period_claimed_by` (`client.py:807-821`). Held by 13 tests in `tests/test_prose_grounding.py` and `test_judge_hard_veto_ytd_as_quarterly` in `tests/test_quality.py`.

**Wrong / fragile — one real defect.** `prose.py:182` filters `if period.key`, which discards exactly the `NamedPeriod` that `spans_named_in`'s own docstring (`prose.py:166-171`: "a heading can answer the second without answering the first") exists for:

```
spans_named_in("For the Six Months Ended June 30,")  -> set()   # should be {6}
periods_named("For the Six Months Ended June 30,")   -> [(6, None)]
```

Reachable consequence: `periods._YEAR_LOOKAHEAD` is 120 chars (`parsing/periods.py:133`). With the heading and its column years more than 120 chars apart, `names_a_year_to_date_span(quote)` returns `False`, and `fast_judge.py:85` then lets a six-month figure auto-pass as quarterly with no model asked. Demonstrated:
```
heading + 20 rows + "2024\n2023"  ->  spans_named_in = set(), names_a_year_to_date_span = False
```
Fix is one line — drop `if period.key` in `_spans_named_in` and let `periods_named_in` filter on the key instead. Simulated over all 549 run13 quotes: **period vetoes 3→3, auto-pass blocks 121→121** — i.e. zero measured effect on this corpus, and the documented behaviour restored. Honest framing: a correctness fix with no score attached.

**Duplication — the big one.** The *namespace* is now one. The *grammar* is three. `prose._periods_with_positions` (5 regexes, `prose.py:60-107` + `190-250`), `periods.periods_named`, and a third at `extraction/fingerprint.py:388 _periods_named_in`. Measured on the 735 sentences that make up run13's stored quotes:

```
prose grammar finds a key periods.periods_named misses:   1   (('2025', 12) — one annual)
periods.periods_named finds keys prose misses:          158
```
Three of prose's five patterns are strict subsets of `periods._QUARTER_FORMS` / `_NAMED_PHRASE_RE`: `_PERIOD_ENDED_RE`, `_ORDINAL_QUARTER_RE`, `_COMPACT_QUARTER_RE`. Only `_ANNUAL_WORD_RE` ("full-year 2002", "FY2002") and `_QUARTER_AND_YTD_RE` ("the second quarter and first six months of 2025") are genuinely prose's — confirmed synthetically (`periods_named("full-year 2002 revenue") == []`).

**Simpler alternative.** Make `_periods_with_positions` = `periods_named(sentence)` (which already returns `position`, `months`, `key`) plus the two prose-unique patterns, and delete the other three regexes and the `consumed`/`claimed` overlap machinery that exists to arbitrate between them. Removes ~45 lines and the `MONTHS`/`fiscal_period_end`/`quarter_of_month` imports from `prose.py`. **Preserves**: every judge-path behaviour measured above (the union already contains `periods_named`'s answers). **Loses**: `_periods_with_positions` is also the prose *extractor*'s ambiguity test (`prose.py:496`), and `periods_named` finds more periods per sentence, so some sentences the extractor reads today would become "ambiguous → refused". Prose is worth −2 correct answers in the leave-one-out, so the downside is small but it is a behaviour change that needs its own held-out set (rule 4). A zero-risk half-measure: change only `_spans_named_in` (`prose.py:175-187`) to call `periods_named` + the two unique patterns, leaving the extractor path untouched. That is where the duplication actually costs, and it is the one function the judge reads.

**Also duplication:** `client.py:802-804` builds `_SPAN_OF_PERIOD_TYPE = {period_type: months …}`. `extraction/check.py:77` already has `_MONTHS_BY_PERIOD_TYPE = {name: months for months, name in MONTHS_TO_PERIOD_TYPE.items()}` — byte-for-byte the same inverse, built twice in two modules in the same pass. The cleanest fix is to give `periods.period_key` an overload that takes a `period_type`, or export the inverse from `parsing/periods.py` beside `MONTHS_TO_PERIOD_TYPE:364`; then `client.py:802-821` collapses to one call and `check.py:77` loses its copy.

**Dead-by-construction: `hard_veto:ytd_language_as_quarterly`.** This is the clearest "two agents' fixes cancelled" finding in my scope. `ff97066` made the veto read `read` (the carrying sentence); `cbc7ed6` (6a) made `sentences()` treat a cell-per-line table row as one unit, so `read` is the row and the row never carries the heading. Result:

```
quote="For the Six Months Ended June 30,\nCalderon XR $ 61,183", period_type=quarterly
  -> issues == []                                   # heading is a different unit
quote="Calderon XR net sales were $61.2m for the six months ended June 30, 2024"
  -> ['hard_veto:ytd_language_as_quarterly', 'hard_veto:quote_states_a_different_period']
```
So it fires only where `quote_states_a_different_period` fires too — **never alone on any input where the candidate carries a `period`**. The only reason `tests/test_quality.py::test_judge_hard_veto_ytd_as_quarterly` shows it alone is that its candidate is `{"period_type": "quarterly", "revenue_scope": "U.S.", "value_reported": 9.6}` — no `period` key, so `_period_claimed_by` returns `None`. A production candidate always has one (`orchestrator.py:2443`). The veto branch (`client.py:866-878`) and `_QUARTER_SPAN` (`client.py:799-801`) can go; `names_a_year_to_date_span` and `_YEAR_TO_DATE_SPANS` must stay because `fast_judge.py:85` is load-bearing (it blocks auto-pass on a 121-of-549 two-span table quote).

### `05498fc` — "7d: the judge is shown the row, the filing's own product list, and the footnote"

**Value — partly measured, partly overstated.** The candidate dict really does grow from 7 keys to 12 and `evidence_judge.yaml` really is told what they mean; `tests/test_the_judge_sees_the_row.py` (6 tests) locks it. But the commit title says "the judge is shown … the filing's own product list", and it is not. `judge()` at `<SNAP>/backend/app/llm/client.py:431-469` accepts `peer_names` and passes it **only** to `apply_judge_hard_vetoes` at line 469; `prompt["user_template"].format(...)` at `client.py:443-448` takes `product`, `candidate`, `quote`, `context` and nothing else. The prompt's own veto 3 ("Quote is only about a different brand/product", `evidence_judge.yaml:10`) still has nothing to check against. Same for `judge_with_search` (`client.py:728-764`). The value delivered is a veto *supplier*, not a better-informed model.

**Wrong — a real bug in the seam I own.** `peer_names` is threaded to five call sites, and one of them reads the wrong document's peers:

```
orchestrator.py:1994   for src in selected_sources:        # prepare loop
orchestrator.py:2079       peers = peer_product_names(...) # per-document
orchestrator.py:2127       prepared[src.source_id] = {...} # peers NOT stored
orchestrator.py:2140   llm_source_ids = self._model_budget(...)
orchestrator.py:2142   for src in selected_sources:        # second loop
orchestrator.py:2200       peer_names=peers,               # last document's peers
```
Verified: `sed -n '2142,2200p' … | grep peers` matches only line 2200 — `peers` is never reassigned in the second loop, so `filter_revenue_candidates` on the LLM candidates of every source uses the peer list of whichever source happened to be prepared last. For any job with ≥2 parseable sources this drops correct candidates whose quote happens to name a brand printed in a *different* filing. Introduced by this commit (`git log -S "peer_names=peers" 744864d..830aad2 -- backend/app/pipeline/orchestrator.py` → `05498fc`). Fix: put `peers` in `prepared[src.source_id]` and read it back at 2200, like every other value in that dict. (File is M4's, but the defect is this commit's.)

**Prompt drift the commit introduced and did not notice.** `evidence_judge.yaml:11` still instructs: *"period_type is quarterly but quote/header says six months … → you MUST return misclassified"*. That is precisely the rule `ff97066` removed from the code because a two-column heading states both spans and settles neither, and lines 30-34 of the same file now tell the model the opposite. One prompt, two contradictory instructions about the same input, and the code sides with the second. More generally, `evidence_judge.yaml:7-12` restates four rules the code applies deterministically *after* the model answers (`client.py:462`), so the model's opinion on them is discarded either way — 6 lines of prompt buying nothing.

**Duplication inside one call.** `label_residue` reaches the model twice: as a key in the `{candidate}` JSON (`orchestrator.py:2454`) and again prepended to `{context}` (`orchestrator.py:2094-2096`). Pick one. Meanwhile `label_flags` and `label_residue` are the two candidate keys the new prompt section (`evidence_judge.yaml:14-28`) does *not* explain, which is the wrong way round: they are the two the model has no other way to interpret.

**The footnote finding — confirmed, and the cause is one line.** `<SNAP>/backend/app/extraction/extract.py:727-728` computes `about = [... if reading_.applies_to(block.months, block.period)]`, and that single list feeds **both** the `partial_period` flag (`extract.py:741-744`) **and** `suffix`, the note text that travels on the quote (`extract.py:746`). So when `applies_to` (`parsing/labels.py:377-385`) mis-parses, the figure is neither flagged nor accompanied by the note, and the judge sees a clean quote — blinded at exactly the moment it could have caught the error, as the brief says. Measured by the commit itself: 2 of 549 quotes carry a note. Minimal fix, one line: `suffix = "".join(cite_footnote(mark, note) for mark, note, _ in cited)` — the note always travels, only the *flag* stays gated. Cost: a quote may carry a note about a neighbouring column, which is a question for the judge and not a claim by the pipeline. I have not measured how many rows would gain a note (it needs a full re-extraction); this is an inferred improvement with a verified mechanism.

**What else the judge is not shown that the pipeline already knows.** Beyond the footnote: the filing's **sibling rows** (computed, never sent to the model — above); the row's **`label_flags`/`label_residue`** (sent, never explained); the **filing's own tagged value for the same period** — `tagged_periods` is assembled at `orchestrator.py:2103` and used only to *suppress* LLM rows for answered quarters, never handed to the judge as "the filer's XBRL says X for this period"; and `filing_contradicts_itself`, which `comparative.py` computes *after* judging, so the judge is never told the document disagrees with itself (6 of run13's 12 held deterministic rows carry that flag).

### `345835f` + `2a1cf0d` — coverage counts published quarters
File: `<SNAP>/backend/app/quality/completeness.py`.

**Value — measured, and I reproduced the commit's table exactly** (AGAMREE 60.0, AYVAKIT 50.0, DAYBUE 100.0, EXONDYS 51 0.0, FIRDAPSE 66.7, ILUVIEN 100.0→0.0, ORLADEYO 47.6→80.0 …). The old reading really did mean "of the gaps we noticed, none are missing". `resolve_completeness_pct` is gone and `llm_pct` was removed by the follow-up `2a1cf0d`, so the same number on the same card is no longer sometimes a count and sometimes an opinion. Good change, honestly reported, and the commit states the residual cross-run incomparability rather than papering over it.

**Complexity cost — modest.** Three new functions (`quarter_labels:34`, `quarters_the_run_asked_for:47`, `coverage_pct:63`) beside the existing `names_a_quarter:24`. `coverage_pct` has exactly **one** caller (`completeness.py:126`) and no test calls it by name except `tests/test_completeness.py`; it is a 3-line helper whose body would read fine inline. 9 tests in `tests/test_coverage_counts_published_quarters.py` hold the property both ways (0 when nothing published, 100 when everything), which is the right guard shape.

**Fragile — the numerator throws away a third of the work.** `expected = quarters_the_run_asked_for(job) or (held | missing)` (`completeness.py:124`) and `coverage_pct` intersects on both sides. Measured over run13: **40 published quarters across 24 jobs fall outside their run's window and count for nothing.** Livmarli holds 9 published quarters, 5 outside the window, reads 80.0. DAYBUE holds 8, 3 outside, reads 100.0. And the card prints "9 qtrs" beside "80%" from `api/products.py:156 _published_quarters`, which counts *all* held quarters — two numbers side by side over different universes.

**Simpler alternative that removes a concept rather than adding one:** `expected = quarters_the_run_asked_for(job) | held | missing`. One word changed. The ratio still cannot exceed 1, still reads 0 with nothing published and 100 with everything (so the existing guard test still passes), the card's two numbers come from the same set, and `coverage_pct`'s "only the quarters in expected are counted on either side" comment collapses into `len(held) / len(expected)`. Loses: a run that answered *more* than it asked no longer reads 100 — arguably the point.

**Rule 2 note:** `scripts/build_independent_gold.py:2743` computes `round(100 * len(observed & set(expected)) / len(expected), 1)` — the same formula as `coverage_pct`, independently. Rule 3 forbids sharing it, so this is deliberate, not a defect; worth a comment saying so.

### `cbc7ed6` (6a) and `b69741e` (3i)
Out of my file scope (`sentences.py`, `profile.py`) but both land on my seams. 6a is measured and correct in shape (the 326→136 replay is reproducible from the stored flags; `value_and_product_in_different_sentences` is still 124 of 134 vetoes, i.e. the judge's whole output is essentially this one rule). 3i's drift test (`tests/test_profile_judge.py`, 17 tests) is exactly the rule-1 shape the repo asks for. No complexity objection to either.

---

## 3. The "also examine" list, answered with commands

- **8 hard vetoes / 4 deterministic judgments.** Table in §1. Four vetoes fire zero times on 549 real rows: `company_total_without_product`, `change_not_level`, `ytd_language_as_quarterly`, `other_brand`. Three are reachable synthetically; `ytd_language_as_quarterly` is *never reachable alone* (§2). **Two are the same rule twice:** `company_total_without_product` (`client.py:860-862`) fires iff `TOTAL_REVENUE_RE` matches and the product is absent; `product_missing_from_quote` (`client.py:885-890`) fires on "product absent" for any scope but `Company total`/`""`. Since run13 has **0 rows with scope `""`** and **0 rows with scope `Company total`**, and `TOTAL_REVENUE_RE` (`parsing/evidence.py:23`, literally `\btotal\s+revenues?\b`) matches **0 of 549 quotes**, the first is wholly inside the second on real data; its only unique domain is a `Company total` row, which `fast_judge.py:71` already refuses before the vetoes on the LLM path and which `apply_judge_hard_vetoes` then labels identically. Delete `client.py:860-862`.
- **`deterministic:product_quote_value_ok`.** It auto-passes 293 of 549 with no model. What else it can pass, demonstrated on invented names:
  ```
  quote = "…(in thousands):\nFor the Three Months Ended\nMarch 31,\n2024\n2023\nCalderon XR\n$\n66,842\n$\n57,526"
  candidate period=2024Q1 value=66,842  -> auto_pass   (correct)
  candidate period=2024Q1 value=57,526  -> auto_pass   (the comparative column, claimed as the current quarter)
  ```
  Because `quote_contains_value` (`quality/checks.py:50-71`) asks only whether the number appears *anywhere* in the quote and `quote_mentions_product` asks only whether the name appears *anywhere*. **210 of the 293 auto-passes have a carrying unit holding more than two numeric tokens** — i.e. a multi-column row where nothing checks which column the figure came from. The two-*span* case is caught (`names_a_year_to_date_span` blocks auto-pass on 121 quotes); the two-*year* case is not, and the year-cross in `periods_named_in` deliberately stops the period veto from catching it. A sibling row's figure *is* caught, but by `value_and_product_in_different_sentences`, not by peers. Smallest closure: require the value to sit in the same unit as the product *and* that the unit hold no other money figure, else fall through to the model.
- **The 17 prompts.** 15 are loaded by literal name, 1 (`competitive_intensity_assessor`) via `analytics/competitive_intensity_llm.py:29 PROMPT_NAME`. **`app/prompts/lot_extractor.yaml` is loaded by nothing** — `grep -rn "lot_extractor" backend --include=*.py --include=*.yaml` returns only the file itself. Dead, 19 lines. Fields nothing consumes: `completeness_analyzer.yaml:25 completeness_pct` (the prompt spends `:11-13` insisting the model compute it; no code reads it since `345835f`/`2a1cf0d`), `:34 limitations`, `:35 recommended_next_steps` (grep finds only the `{}` defaults written at `client.py:603-604`), and `judge_search_validator.yaml:38 search_results`. `judge_search_validator.yaml:20` offers `contradicted` as a support label while `:35`'s own enum omits it and `orchestrator.py:2521` branches on a set that excludes it — two run13 rows are stored `source_support='contradicted'`.
- **`enrichment.py`.** Nothing checks a suggestion against the document — `apply_field_enrichment` (`quality/enrichment.py:77-143`) fills any blank field from `suggested_*` with no grounding test at all. The mitigation is real: every fill forces `needs_review` and caps confidence at 0.55 (`:130-132`), so nothing auto-publishes on it. The exposure is that `DATAPOINT_ENRICH_FIELDS` includes `value_reported` and `value_normalized_usd_millions` (`:15-16`) and `period` (`:12`) — a model's suggested *figure*, unquoted, written onto a row a reviewer may then confirm. Measured: in run13 it filled `geography` 109, `route_of_administration` 63, `citation.page_or_section` 53, calendar/fiscal parts 16, `formulation` 2, and **never `value_reported` or `period`**. So the hole is real and unexercised; the cheap fix is to drop the two value fields from the tuple rather than to add a checker.
- **`completeness.py` — one LLM call per job at the last stage. What does it change?** On run13: **nothing.** Its only surviving consumer is `missing_periods` → `UnresolvedQuarterORM(reason_unresolved=f"[{code}] {reason}")` (`orchestrator.py:3164`), and the three codes it can write are `not_disclosed`/`need_filing`/`gap`. Across all four run databases:
  ```
  run13: 35 unresolved rows, 0 from the completeness model
  run12: 21, 0     run10: 4, 0     run8: 4, 0
  ```
  Zero of 64. Its `completeness_pct` is now unread, its `limitations`/`recommended_next_steps` were always unread. A call per job for no observed output.
- **Model choice.** `app/config.py:25-26`: `openrouter_model_extract = "google/gemini-3.8-flash"`, `openrouter_model_judge = "openai/gpt-4o-mini"`. Nine extract-model call sites and five judge-model ones (`grep -n "model=self.settings\." app/llm/client.py`). So yes — the judge is an older, weaker model than the extractor it is checking, and it is also the model for `judge_profile_field`, `conflict_reconciler` and `judge_with_search`. Two settings for fourteen call sites of very different difficulty: `xbrl_element_judge` and `evidence_judge` share nothing but the word "judge".
- **Free prose in a codes column.** `orchestrator.py:2667-2669` writes the model's `issues` list straight to `row.issue_flags`. run13 stores e.g. `'The reported revenue of $952 million for AYVAKIT in Q1 2025 is contradicted by…'` as a flag. `orchestrator.py:3031` then does `"conflict" in " ".join(d.issue_flags or [])` — a model sentence containing the word "conflict" silently marks the datapoint as a conflict.

---

## 4. Ranked: top simplifications (complexity removed × value preserved)

1. **Store `peers` in `prepared[src.source_id]` and read it at `orchestrator.py:2200`.** One dict key. Fixes a wrong-document peer list that can drop correct candidates on any job with ≥2 sources. *Risk: none — it is the bug fix, and `test_the_judge_sees_the_row.py` already covers the single-source case.*
2. **Delete the `ytd_language_as_quarterly` veto branch (`client.py:866-878`) and `_QUARTER_SPAN` (`client.py:799-801`); keep `names_a_year_to_date_span` for `fast_judge.py:85`.** Removes one of eight vetoes, 13 lines and a constant, for a rule that cannot fire alone on any production candidate. *Risk: `tests/test_quality.py::test_judge_hard_veto_ytd_as_quarterly` must be rewritten to assert the surviving `quote_states_a_different_period`; do not just delete the assertion.*
3. **Delete `hard_veto:company_total_without_product` (`client.py:860-862`).** Strictly inside `product_missing_from_quote` on every real row; its only unique domain is already refused at `fast_judge.py:71`. *Risk: low; confirm no scope-`""` rows can reach the judge first.*
4. **Make `_spans_named_in` (`prose.py:175-187`) call `periods.periods_named` plus the two prose-unique patterns, and drop `if period.key`.** Removes three duplicated regexes from the judge's path and closes the 120-char-lookahead hole in one change. *Risk: low if confined to `_spans_named_in`; measured zero change on 549 quotes. Do **not** also rewire `_periods_with_positions` at `prose.py:496` without a new held-out set — that changes the prose extractor's ambiguity refusal.*
5. **`expected = quarters_the_run_asked_for(job) | held | missing` (`completeness.py:124`), and inline `coverage_pct`.** One word plus one deleted single-caller helper; stops 40 published quarters counting for nothing and makes "9 qtrs / 80%" consistent. *Risk: the 9 tests in `test_coverage_counts_published_quarters.py` assert both endpoints and still pass; a couple of mid-range expectations will need re-deriving.*
6. **Delete `app/prompts/lot_extractor.yaml`; delete `completeness_pct`, `limitations`, `recommended_next_steps` from `completeness_analyzer.yaml`, and `search_results` from `judge_search_validator.yaml`.** ~30 lines of prompt nothing reads. *Risk: none; `test_capabilities_are_wired` rglobs, so confirm it does not require every yaml to be loaded.*
7. **Reconcile `evidence_judge.yaml:7-12` with the code.** Either delete the four hard vetoes from the prompt (the code applies them after the model regardless, `client.py:462`) or fix line 11, which now contradicts lines 30-34 and the veto's actual semantics. Also drop the duplicated `label_residue` from `{context}` (`orchestrator.py:2094`) and name `label_flags`/`label_residue` in the prompt's candidate section. *Risk: prompt change → rule 4 says re-run `scripts/eval.py` on a fresh held-out set before claiming the number.*
8. **Move the `period_type → months` inverse into `parsing/periods.py` beside `MONTHS_TO_PERIOD_TYPE:364`.** Kills the duplicate pair `client.py:802-804` / `extraction/check.py:77`. *Risk: none.*
9. **Un-gate the footnote text: `extract.py:746` reads `cited`, not `about`.** One line; stops `applies_to` blinding the judge at the moment it matters. *Risk: more notes on quotes → possibly more model attention on correct rows; measure the `partial_period` count before and after on the AGAMREE document.*
10. **Drop `value_reported`/`value_normalized_usd_millions` from `DATAPOINT_ENRICH_FIELDS` (`enrichment.py:15-16`).** Removes the one path by which an unquoted model number can become a datapoint's figure. Never exercised in run13, so no capability is lost that anything has used. *Risk: low.*
11. **Decide what `llm.completeness()` is for, or delete the stage.** One model call per job, zero rows produced across four runs, three of four response fields unread. If it stays, it should propose gaps against `quarters_the_run_asked_for` — the denominator that now exists and that it does not see. *Risk: it is the only producer of `[not_disclosed]`/`[need_filing]` reasons in the review queue's vocabulary; confirm the UI does not key off them.*
12. **Sanitise `row.issue_flags` (`orchestrator.py:2667-2669`) to codes, and put the model's prose in `reviewer_notes`.** Fixes the substring match at `orchestrator.py:3031`. *Risk: low; check the frontend does not render flags as sentences.*

---

## 5. Leave exactly as is

- **The year-cross in `periods_named_in` (`prose.py:138-164`).** It is the whole value of `ff97066`: 76 correct comparative-column rows on run13 owe their survival to it, and because it only crosses *years* (the quarter is preserved out of the key by `_QUARTER_OF_KEY_RE`), a quarter mismatch still vetoes. As simple as the value allows.
- **`_period_claimed_by` (`client.py:807-821`).** Re-keying the candidate's label for its declared span is the minimum that makes the two sides comparable; returning `None` for a span the grammar cannot name ("ytd", "guidance") is the right refusal, and it accounts for 75 of 549 rows.
- **`fast_judge.py:85` reading the whole quote rather than `read`.** Deliberate and correct: it gates skipping the model, not vetoing, and it is what blocks auto-pass on 121 two-span table quotes.
- **`tests/test_coverage_counts_published_quarters.py`.** Asserts the property both ways (0 when nothing is published, 100 when everything is), which is exactly rule 4's "a set that only refuses is passed by a system that always refuses".
- **`enrichment.py`'s confidence cap and forced `needs_review` (`:130-132`).** The right contract for an ungrounded suggestion, and the reason the missing document check has not yet cost anything.
- **`llm/aliases.py` (20 lines) and `llm/grounding.py` (88).** Unchanged since `744864d`, three and four real callers respectively, no duplication found. `merge_aliases` is a thin wrapper over `product_aliases` but it is the one place the three alias sources are joined, and it has three production callers.

**Verified vs inferred.** Verified by command: every count in §1 and §3, the grammar-overlap numbers, the `spans_named_in` defect and its simulated fix, the `peers` loop-variable leak, the coverage recomputation (reproduces the commit's table), the completeness-model's zero rows across four databases, the prompt loader census, the model settings, the `DATABASE_URL` test artifact. Inferred: how many rows the footnote un-gating would newly annotate (needs a re-extraction), and the user-facing impact of the `peers` leak (the mechanism is verified, the incidence is not).
