## Orchestrator / pipeline audit — `744864d..dde7fb0`

Snapshot read: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/wt-audit2` (nothing edited anywhere). Scratch scripts and diffs: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/audit-orchestrator/` (`orch.diff`, `main.diff`, `rest.diff`, `probe_metadata.py`, `test_promote_then_demote.py`, `run13copy.db`).

Verdict up front: the series-identity/selection layer is **one mechanism, well factored** — one module, one key, one selection, one enum, one migration. The complexity that is not paying for itself is elsewhere: a stage reorder that silently unplugged an LLM branch, an identity stamped three times where once would do, a derivation lineage recorded twice, and one selection rule that never fires.

### Function lengths, before → after (`orchestrator.py`; whole file 2729 → 3270 lines, plus new `series_identity.py`, 369 lines)

```
function                                    before   after   delta  after@line
class PipelineOrchestrator                    2354    2681    +327        590
_extract_metadata                              324     394     +70        981
_record_derivation_lineage                       -      67     +67       1741
claim_ranking                                    -      57     +57        476
select_job_series                                -      55     +55        534
_carry_to_winner                                 -      35     +35        248
_judge                                         212     245     +33       2548
_extract_revenue                               480     504     +24       2043
_candidate_of                                   11      35     +24       1622
stamp_series_identity                            -      23     +23        179
run_job                                         75      93     +18        617
_datapoint_from_candidate                       64      82     +18       1658
_reconcile_with_llm                            231     248     +17       2794
_retrieve                                       80      95     +15        857
_read_from                                       -      13     +13        397
_parse                                          14      27     +13        953
_quality_and_validation                         86      98     +12       3043
_identity                                       21      30      +9        826
_unquestioned / _publishes                       -   11 / 3     +14   235 / 230
claim_rank, _scope_key                       3 / 3       -    moved to series_identity.py
```
(produced with `funclen.py` over `git show 744864d:backend/app/pipeline/orchestrator.py` and the snapshot copy.)

---

## 1. The stage reorder (`2a1cf0d`) left half of `_extract_metadata` unreachable — **verified**

**Value claimed:** `_identity` had only what the caller typed, so a drug name went to the model and the wrong issuer's filings were downloaded. Real defect, asserted rather than measured (no count in the message). The fix is right in shape.

**What it did to the rest.** `run_job` now calls `_retrieve` twice: a characterisation pass with `sec_filings/earnings_releases/company_ir/transcripts` forced False (`orchestrator.py:642-645`), then the filings pass (`:649`). `_extract_metadata` is called between them (`:647`) — so it only ever sees openFDA sources. `OpenFDAConnector` emits nothing else (`grep -n "source_type=" app/connectors/openfda.py` → four hits, all `SourceType.OPENFDA`), and the second half of `_extract_metadata` is `# LLM metadata from first successful narrative source` … `if src.source_type == SourceType.OPENFDA: continue` (`orchestrator.py:1289-1362`).

Command and output (`probe_metadata.py`, run_job with a stubbed SEC connector returning one successful filing):
```
sources handed to _extract_metadata: [['openfda']]
llm.extract_metadata called with: []
```
Before the reorder, `sources` at that call was the full retrieval (`orch.diff` hunk at `run_job`, lines 448-471: `sources = await self._retrieve(job, options)` then `_extract_metadata(job, sources, parsed, options)`).

**Cost.** ~95 lines that can no longer run, one LLM call per job that no longer happens, and the fields only that branch could fill: `metadata_extractor.yaml` names `generic_name|manufacturer|fda_approval_date|therapeutic_area|indication|moa|pharmacologic_class|roa|dosage_form|formulation|ticker|cik`; the openFDA path (`profile_fields`) produces no `formulation`, `ticker` or `cik`. `_judge_profile` (`orchestrator.py:1376`) only judges rows that already exist — it creates none — so a field openFDA is silent about is now never written at all. Cross-source conflict detection (`values_conflict`, `conflicts[...]`) is now openFDA-vs-openFDA only. This is layer 2, which `_product-brief.md` calls the product.

**Do this (pick one, both are simplifications):**
- *Restore the value:* split the function — `_label_metadata(job, label_sources)` before `_identity`, `_narrative_metadata(job, filings, parsed)` after the filings pass. Cost: one extra call site; removes the 394-line function's double life.
- *Or delete the branch* (`orchestrator.py:1289-1362`) and say the profile comes from the label. Removes ~95 lines, one prompt, one LLM call per job. Risk: `formulation`/`ticker`/`cik` are never populated again, and nothing measures whether they were being used.

Either way this must not be left as-is: it is dead code that reads as live, and the guard that covers the reorder (`tests/test_who_sells_it_is_asked_before_who_filed.py:97`) monkeypatches `_extract_metadata` away, so no test can see it.

**Smaller relatives of the same change:** with `product_metadata=False` the characterisation `_retrieve` + `_parse` still fetch and parse openFDA for a step that returns immediately (`orchestrator.py:982`). And `_set_step(SOURCE_RETRIEVE)`/`PARSE_SOURCES` now fire twice, so `current_step` goes backwards mid-run; only display reads it (`main.py:331,388`, `api/products.py:428`, three frontend pages), so it is cosmetic, but a monitor reader sees the job regress.

**Also now-dead:** the `if not self._job_aliases: await self._expand_aliases(job)` guard in `_identity` (`orchestrator.py:836-837`) — `run_job` always expands first (`:641`) and `_expand_aliases` always sets a non-empty list. Only tests call `_identity` directly. Three lines; harmless, but it exists for a caller that no longer exists.

## 2. `stamp_series_identity` is called three times in-pipeline where one would do — **verified**

Call sites: `_datapoint_from_candidate` (`orchestrator.py:1737`), the LLM/table row builder in `_extract_revenue` (`:2455`), and every row again in `_quality_and_validation` (`:3052-3054`, over `filter_by(job_id=job.id).all()`).

Nothing reads `series_identity` or `geography_normalized` between creation and that re-stamp: readers are `select_job_series` (`:559`, called at `:3106`, after), and `run_quality_checks` via `dp_dicts` (`:3064-3066`, built after). Verified with `grep -rn "series_identity\|geography_normalized" app` — the only other readers are `quality/checks.py:197,215`, `dashboard/series.py:353,360`, `api/products.py`, all downstream of the quality stage.

**Do this:** delete the two creation-time stamps; keep the one in `_quality_and_validation` and the one in `api/products.py:717` (a reviewer row is selected immediately, with no re-stamp in between). Removes two call sites and the "written when the row is, and again once the labels are final" sentence from the docstring. Risk: a job that fails before the quality stage leaves rows with a null identity — which is exactly what the column already means ("empty means nothing decided", `db/models.py:174-180`), and such rows have no selection either.

## 3. A derivation's lineage is recorded twice, and the second record is read by nothing — **verified**

`_datapoint_from_candidate` writes `citation_json["derived_from"] = candidate["_inputs"]` (`orchestrator.py:1726`), and `_record_derivation_lineage` (`:1741-1807`, 67 lines) writes the same list again as 1 + N `EvidenceAssertionORM` rows plus N `DerivationLineageORM` rows.

`grep -rn "DerivationLineageORM" app` → written in the orchestrator, registered in `observability.py:61`'s generic table browser, read nowhere else. `citation_json` does reach a user surface (`dashboard/series.py:365` `"citation": datapoint.citation_json`). So the cheap record is the one a person can see, and the expensive one is a table nothing joins.

The docstring is also wrong about what it does: *"one assertion per figure, the derived one and each input, so an input that several derivations used is one row that all of them name"* (`:1747-1752`). `assertion()` unconditionally constructs `EvidenceAssertionORM(id=new_id(), …)` — there is no lookup of an existing assertion, so two derivations sharing one period total write two rows for it. The test (`tests/test_a_derivation_says_what_it_subtracted.py:143-168`) asserts only a single derivation, so it cannot catch this.

**Do this:** delete `_record_derivation_lineage` and keep `citation_json["derived_from"]` (−67 lines, −2 ORM writes per input per derived figure). Or, if the join table is wanted, make the dedup real and say so. Risk of deleting: an argument that never happened ("which derivations used this input?") becomes a JSON scan instead of a join.

## 4. One selection rule fires zero times on real data and can be wrong in the other direction — **verified**

`select_series_figures` has two dedup passes. The second (`series_identity.py:284-301`, "a remaining reading is a duplicate of the figure its cell holds") does the work the commit describes (the ELEVIDYS five-readings case). The first (`:265-282`, "a figure *selected* twice under two identities is one figure labelled two ways") requires two *published* rows in one cell with the same identity-cell but different identity and byte-equal values.

Measured over all 549 datapoints of `run13/workbench.db`, computing identities offline with the shipped `series_identity()` and comparing the shipped `select_series_figures` against a copy with that block removed:
```
rows: 549  standings: {None: 329, selected: 103, duplicate: 88, superseded: 29}
rows where the cross-identity collapse changes the answer: 0
```
(Strength tuple approximated as `(claim_rank, -confidence)`; the block's reachability depends only on cell/identity/value/publishes, which were reproduced exactly. A narrower scan — published quarterly rows only — gives `published quarterly cells: 90, cells with >1 identity: 1 (AYVAKIT 2024Q4), equal-value pairs across identities: 0`.)

Its failure mode is the wrong direction: a US-only product whose Worldwide and U.S. lines carry the same number gets two legitimately different series, and the regional one is marked `duplicate` and dropped from the curve. That shape is common in this catalog.

**Do this:** delete `series_identity.py:265-282` (−18 lines, one nested loop, one `by_cell` rebuild). Risk: if two identities ever do hold one figure at identical precision, both publish. The general pass at `:284` still collapses every unpublished re-reading, which is where run13's 88 duplicates come from.

## 5. 6e's promotion and 5f's carry can cancel each other out — **verified**

In `_reconcile_with_llm`, when the winner is held, the strongest `_publishes and _unquestioned` corroborator takes its place (`orchestrator.py:2977-3006`). Immediately after, `_carry_to_winner` (`:3007-3017`, body at `:248-282`) carries label flags onto that promoted row *from every agreeing row, including rows that are themselves unpublishable*, and a carried flag sets `validation_status = NEEDS_REVIEW` (`:278`).

Probe (`test_promote_then_demote.py`, reusing the 6e fixtures: a vetoed `llm` winner, a clean `xbrl_fact` auto_pass, and a third `needs_review` table row flagged `partial_period`):
```
llm        needs_review ['hard_veto:quote_states_a_different_period']
xbrl_fact  needs_review ['partial_period']
table      corroborates ['corroborates_published_figure', 'partial_period']
PUBLISHED: []
```
The tagged fact was promoted and then re-questioned by a reading the pipeline already refuses to publish — and that same row is simultaneously recorded as corroborating the figure it just withheld. This is the tier-blindness pattern: a reader's flag on one row withdraws a fact the filer tagged.

Exposure on run13 (groups where a published row has an agreeing reading carrying a `LABEL_FLAGS` member): **4 of 92**, all YUTIQ `combined_line` — which is the case 5f exists for and is held correctly. So the mechanism is right ~4% of the time and the interaction above is unmaterialised in this database.

**Do this:** carry flags only from rows that themselves publish, or skip the carry when the winner was replaced (one condition, `:3017`). Preserves the ELEVIDYS/YUTIQ case; stops an unpublishable reading vetoing a promoted one.

## 6. Two definitions of "one group" and two of "one figure" — **verified, unmaterialised**

- Reconciliation groups on `(period, period_type, _scope_key(revenue_scope), formulation)` (`orchestrator.py:2810-2818`). The series identity groups on issuer, product, scope, **geography**, formulation, **reported_as**, **currency**, period_type (`series_identity.py:164-198`). On run13: **45 of 359 reconcile groups hold more than one series identity, 137 rows** — almost all `geography=None` against `geography='United States'` for the same figure (NUPLAZID 2024Q3: 159.186 unspecified vs 159.186 United States). Reconciliation calls them one group and picks one; the identity calls them two series.
- "Same figure" is `_agrees_within_declared_precision` in reconciliation (`orchestrator.py:204-219`) and exact `float(a) == float(b)` in the selection (`series_identity.py:272, 291`). Demonstrated divergence:
```
python -c "... select_series_figures([159.186 @ identity A, 159.2 @ identity B])"
a SeriesStanding(selection='selected', held_by='a')
b SeriesStanding(selection='selected', held_by='b')
```
Two points for one quarter. It does not happen on run13 only because reconciliation puts those two rows in one group first and demotes one to `corroborates` (0 near-equal cross-identity published pairs in 90 cells). The two mechanisms protect each other by accident.

**Do this (low urgency, do not do it blind):** if the grouping is unified, unify it on the identity, not on the scope key — that is the direction the commits were already going (`claim_ranking` was lifted out for exactly this reason, `orchestrator.py:476-483`). It is a behaviour change on 137 rows, so it needs its own held-out set. Second-cheapest: make the selection's "same figure" call `_agrees_within_declared_precision` too, so one definition exists. Deleting the cross-identity block (finding 4) removes half the problem for free.

**AYVAKIT, for the record:** reconciliation already handles it correctly — the three rows carry `revenue_scope` `Worldwide`/`U.S.`/`ex-U.S.` and `_scope_key` only merges Worldwide with Product family, so they land in three groups and none contests another (`select ... where drug_name='AYVAKIT' and period='2024Q4'` → `144.1 Worldwide`, `124.1 U.S.`, `20.0 ex-U.S.`, all auto_pass). Publishing the regional components as their own series is right, and the identity says so. Whatever called it "conflicting" is not in `pipeline/`.

## 7. Repetition and seams (small, cheap)

- **`peers` recomputed per datapoint.** `_judge` computes `peer_product_names(sibling_row_labels(doc.tables, …))` inside the row loop (`orchestrator.py:2581-2587`); `_extract_revenue` computes the same thing once per document (`:2197-2203`). On run13's rows that is **549 computations where 162 would do** (`549 datapoints / 162 distinct source docs`). Memoize by `source_id`: ~4 lines, no behaviour change.
- **`_resettle_series` written twice.** `main.py:541-555` (re-stamps every row, then selects) and an inline copy in `api/products.py:736-742` (stamps only the new row at `:717`, then selects). Same idea, two shapes, two import sites of `stamp_series_identity`/`select_job_series`. Move one `resettle_series(db, job)` next to `select_job_series` in `pipeline/orchestrator.py` and call it from all three review paths — that is exactly the argument `dde7fb0` made for making the functions module-level, applied one step further.
- **`claim_ranking` returns three closures for two callers.** `select_job_series` uses one and discards two (`orchestrator.py:553`, `claim_tier, _reports_own_period, _accession_of = …`), and pays for a `SourceDocumentORM` query it only needs for the discarded ones. Either return a small frozen dataclass, or have `select_job_series` take the `claim_tier` it needs as an argument.
- **Coverage verdict is write-only.** `_parse` writes `metadata_json["coverage"]` per document (`orchestrator.py:962-976`); `grep -rn coverage app` finds no reader outside `connectors/coverage.py` and the generic observability table dump. The commit says so deliberately ("no count is claimed here"). 14 lines, fine — but if the next change does not consume it, delete it rather than keep a measurement nothing measures.
- **`reported_as` order is part of the identity key.** `series_identity` slugs `reported_as` as written (`series_identity.py:187`), and `reported_as_for` orders the pair by position in the quote (`orchestrator.py:347-350`). Two filings printing "Calderon + NuVessa" and "NuVessa and Calderon" would be two series for one line. Run13 has one spelling across 12 rows (`select distinct reported_as` → `['ILUVIEN + YUTIQ']`), so this is **inferred**, not observed. One-line fix: sort the names for the key part, keep the printed order in the column.
- **Within a job, three of the eight identity parts never vary** (issuer, product, and currency in practice), and nothing groups by identity across jobs — `dashboard/series.py:209-218` collapses every series of a job into one `quarters_held` list. The parts cost nothing and pay off the first time a reader compares two jobs; noted, not a finding.

## 8. `_search_revenue_fallback` / `_search_quarters_fallback`

Unchanged since `744864d` (identical line counts, 21 and 32, at `orchestrator.py:1451` and `:1503`) — outside the diff under audit, so I did not re-measure their value. Structurally they cost `run_job` 15 of its 93 lines (`:657-675`) and are the reason `sources`/`parsed`/`datapoint_rows` are rebound three times in the middle of the sequence; deleting them would make `run_job` read as one straight sequence of stage calls. That is the largest readability win available in `run_job` and it depends entirely on the zero-value measurement holding, which is another auditor's to confirm.

---

## Ranked: complexity removed × value preserved

1. **Fix or delete the unreachable narrative half of `_extract_metadata`** (`orchestrator.py:1289-1362`). Do this: split into label-metadata and narrative-metadata stages, or delete the branch. Risk: deleting loses `formulation`/`ticker`/`cik` and cross-source conflicts for good; splitting adds one call site. Either beats a 95-line branch that cannot run.
2. **Delete `_record_derivation_lineage`** (`orchestrator.py:1741-1807`), keep `citation_json["derived_from"]`. −67 lines, −(2N+1) row writes per derived figure. Risk: the lineage join table goes; nothing joins it today.
3. **Stamp the identity once** — drop `orchestrator.py:1737` and `:2455`. −2 call sites, one fewer invariant to hold. Risk: none found; failed jobs keep null identities, which already means undecided.
4. **Delete the cross-identity value collapse** (`series_identity.py:265-282`). −18 lines; measured 0 effect on run13 and removes a wrong-direction failure for US-only products. Risk: two identities holding one identical figure would both publish.
5. **Carry label flags only from publishable rows** (`orchestrator.py:3007-3017`). +1 condition, removes a promotion that undoes itself. Risk: a stub-quarter reading that is itself held no longer holds the published row — check against the YUTIQ four.
6. **One `resettle_series`, called from three paths** (`main.py:541`, `api/products.py:736`). −1 duplicate implementation.
7. **Memoize `peers` by source in `_judge`** (`orchestrator.py:2581`). −3.4× repeated work, no behaviour change.
8. **`claim_ranking` returns what its caller needs** (`orchestrator.py:476-532`). Cosmetic; do it while touching the file.
9. **Unify reconcile grouping with the series identity** (`orchestrator.py:2810-2818`). Highest value, highest risk — 137 rows move; needs a new held-out set under rule 4. Do 4 first, which removes half the exposure for nothing.

## Leave exactly as it is

- `series_identity.py` as a module: one key, one vocabulary, one selection, the geography vocabulary is a literal that says what it is a snapshot of and what a miss looks like (`:84-102`), `_SCOPE_TOKENS` is built from the enum (`:153`). Rule 1 satisfied without ceremony.
- `SeriesSelection` / `holds_the_series_figure` / `FINISHED_JOB_STATUS_VALUES` (`domain/models.py:70-159`): the list-what-counts shape, derived from the enums, with "empty means undecided" stated once and honoured by every reader.
- Migration `008_datapoint_series_identity.py`: same additive, inspect-first shape as 005-007; three nullable columns; correct.
- `_read_from` (`orchestrator.py:397-409`) and the widened `_candidate_of` (`:1622-1656`): every one of the 13 added keys is consumed by `extraction/derive.py:527-566` — I checked each. Not a dict growing keys nobody reads.
- `main.py`'s `/config` route (`:147-186`) and `imports/peak_sales.py`'s `CITED_ESTIMATE_TYPES` (`:18-20`): small, derived from the model/enum, tested.
- The four new series tests (`test_a_series_is_one_figure_per_quarter`, `test_the_pipeline_selects_one_figure_per_series`, `test_a_series_says_where_it_begins`, `test_review_resettles_the_series`, 740 lines): one unit file, one pipeline file, one begin/end file, one review-path file, no overlap I could find, both answers present in each. `./.venv/bin/pytest -q -p no:cacheprovider` over those plus the corroborator, derivation, reorder, config and options guards: **49 passed**. `ruff check app/pipeline app/main.py app/domain app/db app/imports` → 3 errors, all pre-existing `S112`.

## Verified vs inferred

Verified by command: findings 1 (probe), 2 (grep of all readers), 3 (grep + code read), 4 (run13 A/B over 549 rows), 5 (pytest probe + run13 exposure count), 6 (run13 group scan + direct call), 7 (`peers` count, `reported_as` scan), table of function lengths, test/ruff runs.
Inferred: what the deleted narrative branch was worth in fields actually used (I showed which fields only it can produce, not how often they mattered); the `reported_as` ordering risk (no instance in run13); that unifying the reconcile grouping is safe (it moves 137 rows and I did not score it).
