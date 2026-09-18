# Audit: retrieval/identity + characterisation (layer 2), `744864d..830aad2`

Snapshot read at `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/wt-audit`. Nothing edited. Scratch scripts under `.../scratchpad/audit-retrieval/`. Per the product brief I did **not** read `docs/plans/2026-09-17-006-*`; I worked from the commit messages and the code. Live openFDA calls made 2026-09-17.

Scope tests all pass:
`./.venv/bin/pytest -q -p no:cacheprovider tests/test_openfda_enrichment.py tests/test_openfda_fields_are_cited_where_they_were_read.py tests/test_fda_label_parser.py tests/test_indications.py tests/test_profile_judge.py tests/test_a_document_says_what_it_covers.py tests/test_an_issuer_is_resolved_or_refused.py tests/test_one_fetcher_keeps_the_pace.py tests/test_an_amendment_is_read_as_its_family.py tests/test_who_sells_it_is_asked_before_who_filed.py tests/test_capabilities_are_wired.py tests/test_earnings_sources.py` -> **122 passed**.

---

## A. Characterisation

### A1. `5b173f6` read an openFDA record by the keys it carries (`parsing/fda_label.py`)

**Value.** Measured, with filter and command: route disagreement 5/20 -> 0/20, dosage_form 0/20 -> 19/20, applications resolved 16/20 -> 19/20. I spot-checked the mechanism live and it holds.

**Complexity cost.** `_PATHS` (fda_label.py:40-54), `read_path`/`openfda_block`/`product_columns`/`_nested_product_values` (:71-127), `route_terms`/`route_readings_agree`/`_terms_cover` (:140-169), two new `ParsedFDALabel` fields (`route_readings`, `paths`) and a `route_conflict` property. 15 tests in `tests/test_fda_label_parser.py`.

**Simpler alternative: none worth taking.** This is the right shape - one table of "which key carries which fact", everything else derived, each value carrying its path. Leave it.

**Fragile.** `parse_label_record`'s `chosen()` (:228-231) takes the first entry of `_readings`' dict, which is only preference-ordered because `_readings` iterates `_PATHS[fact]` and dicts preserve insertion order. Correct, undeclared, and one refactor from silent breakage. One line of docstring, not code, fixes it.

**Duplication.** `indications_text` is read twice: `_readings` already computed `values["indications"]` from path `indications_and_usage`, then `:242` calls `_first_section(record, "indications_and_usage")` again with the path spelled out a second time. `indications_text = "\n".join(values["indications"]) or None` removes the helper and the second literal.

### A2. `4cb5fe4` molecule exclusion + direction-sensitive brand fallback (`connectors/openfda_fields.py`)

**Value.** Measured per product, live, before/after. Real: the old exclusion compared strings for equality against a molecule every alias set spells differently.

**Complexity cost.** `molecule_names`, `names_the_molecule`, `_contains_word`, `_candidates`, and a two-bucket (`exact` / `extending`) loop, openfda_fields.py:40-148. `blends_sibling_brand` is imported from `app/quality/profile.py` into a connector (:9) - a layering hop that exists for one predicate.

**Wrong / fragile - verified.** `MIN_ALIAS_LENGTH = 4` (:11) is applied in `_candidates` to the **product's own name**, not just to aliases (:81). Result:

```
$ ./.venv/bin/python -c "from app.connectors.openfda_fields import _candidates, brand_matched_results; \
  print(_candidates('Duo', ['Duo XR'])); \
  print(brand_matched_results([{'application_number':'NDA1','openfda':{'brand_name':['DUO'],'generic_name':['acmeine']},'products':[{'brand_name':'DUO'}]}], product='Duo', generic='acmeine'))"
['duo xr']
[]
```

For a three-letter brand the product's own name is dropped and the **sibling line-extension alias is the only candidate left** - the exact harm 3d exists to prevent, inverted, plus the record that really is the product matches nothing. Latent on the seed catalog (shortest name is `Alyq`, 4 chars), live for any 3-character brand. Fix: apply the floor to aliases only (`for name in aliases` branch), one line.

**Verified limitation, answering "a generic with many sponsors".** The molecule exclusion is unconditional on the candidate side (:134), so a product marketed *under the molecule name* matches nothing:

```
$ ./.venv/bin/python .../audit-retrieval/probe_generic.py
Tadalafil   scopes=[brand:openfda.brand_name, brand:products.brand_name] results=17 matched=0 selected=None
            brands seen: ['N/A', 'TADALAFIL']
Treprostinil Injection  results=9 matched=0 selected=None
Opsumit     results=1 matched=1 selected=NDA204410 brand=OPSUMIT
```

17 applications come back, `openfda_no_brand_match` is flagged and `continue` runs (orchestrator.py:901-913): no route, no dosage form, no approval date, no indications, no canonical product. That refusal is defensible for *which application*, but the molecule-wide facts (route, dosage form, pharm class, indications) agree across those 17 and are thrown away with them. This is the single largest capability gap in layer 2 and it is not an accident of the change - the change made the refusal reliable.

### A3. `1fc8e6d` + `168c4c0` earliest approval across matches, `profile_fields`/`SourcedValue`, brand-path union

**Value.** The citation fix is the strongest change in my scope and it is measured: 227 of 312 (record, field) pairs previously cited `openfda.<field>`, a key the record does not carry. It now cites a key that exists. `earliest_approved_match` stops the route and dosage form describing a different application from the date. 7 tests in `tests/test_openfda_fields_are_cited_where_they_were_read.py`, 17 in `tests/test_openfda_enrichment.py`.

**Complexity cost.** `SourcedValue` (fda_label.py:260-272), `profile_fields` with **seven** keyword parameters (:295-305), `PROFILE_FIELDS` derived by calling the producer with empty arguments (:373), `earliest_approved_match` (openfda_fields.py:190-216), and in the connector the whole union apparatus (openfda.py:89-116): a `merged` dict, `scopes` and `searches` lists, an inner `collect` closure, and a scope-prefix protocol (`pair[0].startswith("brand")`, :103-104) used to re-split a list the function that built it already knew the shape of.

**Simpler alternative - the biggest single win in my scope. Verified live.** openFDA answers the union in one query:

```
$ ./.venv/bin/python .../audit-retrieval/probe_union.py     # api.fda.gov/drug/drugsfda.json, limit=10, 2026-09-17
Alyq       path1=['ANDA216932']            path2=['ANDA209942','ANDA216932'] OR=['ANDA209942','ANDA216932']
Remodulin  path1=['NDA021272']             path2=['NDA021272','NDA208276']   OR=['NDA021272','NDA208276']
Tyvaso     path1=['NDA022387','NDA214324'] path2=same                        OR=same
Uptravi    path1=['NDA207947','NDA214275'] path2=same                        OR=same
```

`openfda.brand_name:"X"+OR+products.brand_name:"X"` returns exactly what the two sequential queries merge to, including on Alyq and Remodulin - the two products the commit cites as the entire gain. So `search_queries` can return one brand query and one molecule fallback, and `retrieve` collapses to: try the brand query, fall back to the molecule query, done. That deletes ~25 lines, the closure, both bookkeeping lists, the prefix protocol, and **halves drugsFDA calls per job** (today openfda.py:106-107 loops both brand paths with no break, so it is 2 drugsFDA + 1 label = 3 HTTP calls per job unconditionally; the commit itself measured 21 -> 40 calls over 20 products). What it loses: two queries can return up to 20 results where one returns 10 - raise `LIMIT` if that ever matters, and say so.

**Duplication - three spellings of one path pair.** `openfda.py:20 BRAND_SEARCH_PATHS = ("openfda.brand_name", "products.brand_name")` (query syntax), `openfda_fields.py:33` repeats `("openfda.brand_name", "products[].brand_name")` inline (read syntax), and `fda_label.py:41 _PATHS["brand_name"]` is that same read tuple. `openfda_brand_names` should read `_PATHS["brand_name"]`; the query-syntax one is genuinely different and should say it is derived from the other by dropping `[]`.

**Wrong / fragile - verified, and it decides whose filings are downloaded.** `_manufacturer` (fda_label.py:279-292) prefers `openfda.manufacturer_name` over `sponsor_name`, and its docstring frames the two as a dataset difference ("the label dataset names it in the `openfda` block; a drugsFDA application names it at the top level"). On a drugsFDA record **both are present and they name different companies**:

```
$ ./.venv/bin/python .../audit-retrieval/probe_manu.py
Adcirca  NDA022332  sponsor_name='ELI LILLY CO'    openfda.manufacturer_name=['United Therapeutics Corporation'] -> chosen 'United Therapeutics Corporation'
Revatio  NDA021845  sponsor_name='VIATRIS'         openfda.manufacturer_name=['PFIZER LABORATORIES DIV PFIZER INC','Viatris Specialty LLC'] -> chosen 'PFIZER LABORATORIES DIV PFIZER INC'
Alyq     ANDA209942 sponsor_name='TEVA PHARMS USA' openfda.manufacturer_name=None -> chosen 'TEVA PHARMS USA'
```

The preferred key is the **labeler**, not the applicant. For Adcirca that is the right answer for revenue (United Therapeutics books it, Lilly holds the NDA); for Revatio it is the wrong one (Viatris books it today) *and* it is the first element of a two-element list in openFDA's undocumented order - the same order-dependence 3e removed from the approval date, left in place here. This value flows to `job.manufacturer` (orchestrator.py:1027) and from there to `resolve_cik(company_name=...)`, i.e. to which issuer's filings the job downloads. The comment introducing the whole reorder (orchestrator.py:527) says "A drug label names its **sponsor**" - the code prefers the labeler. Either state the choice and why, or take `sponsor_name` for the filer question and keep the labeler as the commercial owner. This is "present and wrong" territory: it produces a filer that looks reasoned.

**Seam.** `profile_fields`' seven kwargs are all threaded from one call site (orchestrator.py:945-955), and four of them (`indications`, `indication_value`, `therapeutic_area_value`, `moa_value`) are values the orchestrator computed from `label` and `record`, which `profile_fields` already has. Moving `parse_indications` / `therapeutic_areas` / `format_moa_profile_value` inside would leave a two-argument function and delete 11 lines at the call site. What it loses: `moa_epc_contamination_issue` lives in `quality/`, so either that check moves too or `moa_value` stays as one optional override.

### A4. `6621e13` therapeutic_area (`parsing/indications.py:132-165`)

**Value.** Real and large: without it every product was its own indication universe and competitive intensity was "low" for everyone. Measured in the commit; **I reproduced it independently** with a different filter (label endpoint directly, 15 of 20 seed products resolving a label):

```
$ ./.venv/bin/python .../audit-retrieval/probe_areas_all.py
14  'pulmonary arterial hypertension'                                  [Opsumit, Adcirca, Tyvaso, Letairis, Tracleer, Uptravi, Remodulin, Orenitram, Veletri, Tyvaso DPI, Yutrepia, Revatio, Alyq, Tadliq]
 3  'pulmonary hypertension associated with interstitial lung disease' [Tyvaso, Tyvaso DPI, Yutrepia]
 2  'adults with pulmonary arterial hypertension'                      [Winrevair, Adempas]
 1  'arterial hypertension in patients requiring transition from ...'  [Remodulin]
 1  'adults with persistent/recurrent chronic thromboembolic ...'      [Adempas]
```

**Complexity cost.** Two regexes and two functions, 34 lines. Cheapest possible implementation of the value. Held by 7 tests in `tests/test_indications.py`.

**Wrong / fragile - verified.** `_AREA_QUALIFIER = re.compile(r"\s*[(\[].*$")` (:132) deletes from the first bracket **to the end of the string**, so it also deletes the qualifiers the docstring promises to keep:

```
$ ./.venv/bin/python -c "from app.parsing.indications import therapeutic_area as t; \
  print(t('pulmonary hypertension (WHO Group 3) associated with interstitial lung disease')); \
  print(t('breast cancer (HER2-positive) after prior therapy'))"
pulmonary hypertension
breast cancer
```

The docstring says "a qualifier that changes which patients are treated is left in, because it is a different market". It is left in only when no parenthetical precedes it. On today's PAH labels the parenthetical happens to sit last (verified above for Tyvaso, Opsumit, Winrevair, Uptravi), so the module is right on the catalog by the shape of those labels, not by the rule it states. `\s*[(\[][^)\]]*[)\]]` (strip the bracketed span, keep the tail) is the same line and matches the docstring.

**`_AREA_TRAILING_CLASS` is a written-down list in disguise** (:133-135): `who|nyha|ajcc|fab` is four classification systems with no note saying what it is a snapshot of or what makes it stale (CLAUDE.md rule 1's own remedy). One comment.

**The value does not reach the reader - verified.** Two consumers prefer a column nothing writes:
- `app/db/models.py:327` declares `ProductIndicationORM.therapeutic_area`; `grep -rn "ProductIndicationORM(" -A 14 app/` shows **no writer sets it** - the orchestrator's row (orchestrator.py:1121-1130) writes disease/setting/population/biomarker/lot only.
- `app/dashboard/series.py:166-169` and `app/api/products.py:361` read that column *first* and fall back to the profile field.
- The profile field is the list joined with `"; "` (orchestrator.py:950), so Tyvaso's `therapeutic_area` is `"pulmonary arterial hypertension; pulmonary hypertension associated with interstitial lung disease"`. Downstream grouping is exact equality (`analytics/competitive_intensity.py:117`) and `analytics/analog_matching.py:159-165` scores `indication_area` by containment, i.e. **0.5 instead of 1.0** on the heaviest weight, against every single-indication PAH product.

One line at orchestrator.py:1121 (`therapeutic_area=therapeutic_area(indication.disease)`) makes the column live, gives the dashboard the *set* rather than the joined string, and restores exact-equality grouping. Highest value-per-line in this report.

### A5. `b69741e` derived judge vocabulary (`quality/profile.py:123-161`)

**Value.** Replaces a hand-written eight-name tuple with a membership read off both producers. Asserted mechanism + printed vocabularies; the drift test in `tests/test_profile_judge.py` (17 tests) is the real guard.

**Complexity cost.** `prompt_metadata_fields()` parses a YAML prompt file with a regex over its JSON skeleton (`_PROMPT_FIELD_ENUM`, :21) at import time; `judgable_fields()`; `PRIORITY_JUDGE_FIELDS` computed at import from a filtered literal (:161). Three concepts where there was one tuple.

**Simpler alternative.** Marginal. The prompt-scraping is the awkward half (a regex over a prompt file, silently returning `frozenset()` if the skeleton moves - :131-133), but the drift test converts that silence into a failure, which is the point. I would keep it and not thread it further.

**Seam.** `has_label_section_header` (profile.py:83) is on `SCRIPT_ONLY` as `NOT_WIRED` in `tests/test_capabilities_are_wired.py:81` - a helper only tests call, correctly declared. `select_assertion` in `app/identity/resolver.py` likewise (:82). `app/identity/` has **no commits in this range**; it is unchanged from main and I found nothing wrong with it.

### A6. `cf5fbb2` launch anchor written after both records (orchestrator.py:872-880, 1153-1164)

**Value.** Correct and necessary: the approval lives on the drugsFDA record and the indications on the SPL, so the anchor cannot be written inside the source loop. Two accumulator lists and a post-loop pass, ~15 lines.

**Already as simple as the value allows.** Leave it.

### A7. The profile judge - what it costs and buys (orchestrator.py:1259-1332)

**It runs**: `enable_profile_judge=True` (config.py:51), gated on `openrouter_api_key` and `enable_llm_search`. `profile_judge_max_fields = 0` means **no cap** (config.py:52-53), so it is **one LLM call per profile field** - ~11 openFDA fields plus whatever the LLM branch wrote, per job. It buys: a verdict and flags on every field's `citation_json`, and a correction only when the judge contradicts *and* cites a URL with a verbatim quote above 0.6 confidence (`apply_profile_judgment`, profile.py:207-270) - a well-drawn gate, four named refusal flags, no silent overwrite. That part is as simple as the value allows.

Two notes. (1) The comment justifying the setting (config.py:48-50) cites `openfda.route` giving ORAL for an inhaled product - the defect `5b173f6` fixed by reading `products[].route` first. The judge's stated reason for existing is now historical; the judge may still be worth its cost, but not for that reason. (2) The judge is shown `source_quote`, `source_url` and `openfda_application_number` (:1289-1295) but **not** `source_field`, the key 3j went to some trouble to record - so the judge challenges a value without being told which key of the record produced it.

---

## B. Retrieval and identity

### B1. `1a01080` one fetcher against one host (`sources.py:131-207`)

**Value.** Real defect, plainly stated: `fetch_page` bypassed the SEC pace while `_get_with_retry` observed it. Measured with a live fetch. `is_sec_host` via `urlparse` instead of `"sec.gov" in url` is a correct and cheap hardening.

**Complexity cost.** Net negative - the retry loop moved from a method to a module function, one `sec` boolean guards three call sites inside it, `_get_with_retry` is now a 3-line delegate. 4 tests in `tests/test_one_fetcher_keeps_the_pace.py` cover both answers (paced / not paced).

**This is already as simple as the value allows.** The guard test (`tests/test_earnings_sources.py`, derived: `reads == paced` read out of the module's AST) is the right shape and replaced a hand-written four-name tuple. Leave all of it.

### B2. `77ca84b` read a form as its family at every gate (`sources.py:328-345, 426-448`)

**Value.** Measured over 280 cached submission indices with the filter stated: +108 earnings 8-K/A, +1,463 primary. The commit is also honest that this buys corroborators, not new quarters (0 of 34 dropped filings carried a figure a kept filing did not).

**Complexity cost.** `states_item` and `reading_order` (two small derived functions replacing an `in` test and a form-keyed dict), `PRIMARY` derived from `PERIODIC_FORM_FAMILIES` minus two literal sets, plus `EARNINGS_FORM`. 6 tests in `tests/test_an_amendment_is_read_as_its_family.py`.

**Already as simple as the value allows.** The one nit: `form_family(form) != form_family(self.EARNINGS_FORM)` (:~663) calls `form_family` on a constant that is already a family; `form_family(form) != self.EARNINGS_FORM` reads the same and says the constant is a family, which the comment at :446-448 already claims.

### B3. `42a1f1d` refuse an issuer the model is unsure of (`llm_search.py:43-186`)

**Value, split.** The behavioural half is real and delivered: a CIK below `llm_cik_min_confidence` is no longer taken, so a job no longer publishes 28 filings of a company the model guessed at. Measured on the live ticker index for the `&`/`and` half (0/249 -> 225/249), with the negative side checked (nothing that resolved stops resolving). 10 tests in `tests/test_an_issuer_is_resolved_or_refused.py`, both answers represented.

**The reporting half is not delivered, and it is built twice.** Verified:

```
$ grep -rn "last_resolution\|quality_flags=" app/ tests/ | grep -v "quality_flags=list"
app/connectors/llm_search.py:117,167,179      (writes only)
tests/test_an_issuer_is_resolved_or_refused.py:175,181,182,192,203,206   (the only readers anywhere)
```

`llm_search.py:149` takes an optional `quality_flags: list[str] | None` that it mutates in place, **and** `llm_search.py:117` keeps `self.last_resolution`. Neither is read by `app/`. The one production caller, `orchestrator.py:741-751`, passes no `quality_flags`, never reads `last_resolution`, and hand-writes `job.quality_flags += ["cik_from_llm_search"]` - duplicating `SearchedIdentity.flags` (:71-76). So the three named refusals (`cik_search_returned_no_cik`, `..._refused_no_confidence`, `..._refused_low_confidence`) reach a log line and nothing else, and `SearchedIdentity.nothing()` / the `"cik_search_not_asked"` special case exist purely to let the unused property return `[]`.

**Simpler alternative.** Return `SearchedIdentity` from `resolve_cik_from_search` and let `_identity` do `job.quality_flags = sorted(set((job.quality_flags or []) + resolution.flags))`. That deletes the mutable-list parameter, the `last_resolution` attribute, the `nothing()` constructor and the `"cik_search_not_asked"` branch, removes the orchestrator's duplicated literal, and is the only version in which the analyst ever sees why the issuer was refused. Preserves everything; loses nothing. Cost: `resolve_cik_from_search`'s return type changes, and `tests/test_an_issuer_is_resolved_or_refused.py` moves from asserting on the sink to asserting on the return.

### B4. `2a1cf0d` read the label before asking EDGAR (orchestrator.py:513-552, 753-836)

**Value.** The strongest ordering argument in the branch and it is correct: EDGAR's name index answers a ticker or a company name, a drug name alone reaches no index, so `_identity` used to go straight to the model. Now the openFDA pass runs first and puts a sponsor on the job. 4 tests in `tests/test_who_sells_it_is_asked_before_who_filed.py`, including one that the characterisation pass does not trip the "no filer of record" verdict.

**Complexity cost.** `_retrieve` is now called twice with two option-dict inversions: a `characterisation` literal turning four keys off (:538-540) and `{**options, "openfda": False}` (:545), plus a third predicate inside `_retrieve` (`asked_for_filings`, :806) that exists only because one of the two callers turns filings off, guarding two branches (:813, :824). The `characterisation` dict is a written-down list of every non-openFDA option `_retrieve` reads - complete today (I checked: `_retrieve` reads exactly `sec_filings`, `earnings_releases`, `openfda`, `company_ir`, `transcripts`), stale the day a sixth source is added, and the symptom would be the characterisation pass silently downloading filings before the sponsor is known, i.e. the defect coming back.

**Simpler alternative.** Split `_retrieve` into `_retrieve_product_documents(job, options)` (openFDA + nothing else) and `_retrieve_filings(job, options)` (everything else), and have `run_job` call them in order. That deletes both option-dict inversions, the `openfda` flag, and `asked_for_filings` with its two guards; the "no filer of record" reasoning lives naturally in the filings method where it is always true. Preserves the ordering and the flag semantics; loses the ability to run both passes in one call, which nothing wants.

**Note.** `resolve_cik`'s arguments now both default to `None` (sources.py:~461) and a missing ticker no longer ends the question - but `orchestrator.py:734` still reads `if not job.cik and (job.ticker or job.manufacturer)`, so the guard the commit message says buys nothing is still there. Harmless, one line, and worth removing with the split above.

### B5. `ae370c3`/`82eaad7` `connectors/coverage.py` (new, 321 lines, `NOT_WIRED`)

**Value.** The question is the right one and nothing else asks it, and the module is scrupulous about being a figure test rather than a name test - `names_only` as an explicit verdict is the honest answer for a row-grouped schedule, and saying so is worth more than a `carries` that hides the gap. The measurement it produced (627 documents of run13, 547 carrying nothing for their window) is the kind of number the project needs. Held by 16 tests in `tests/test_a_document_says_what_it_covers.py`. Judged on its own terms: it is good, and I would keep it.

**Complexity cost, and where it overlaps.**
- `record_coverage` (:204-226) is a three-line wrapper over `coverage` that **only tests call** - and it is the reason `coverage` itself passes `test_capabilities_are_wired` (the wrapper references it inside `app/`). The same branch deleted `select_openfda_result` for being exactly this ("a wrapper reachable only from tests is what `test_capabilities_are_wired` forbids", `1fc8e6d`). Two agents, opposite conclusions.
- `DocumentCoverage.carries_nothing` (:110-112) is referenced **nowhere** - not in `app/`, not in `tests/` (`grep -rn "carries_nothing" app/ tests/` returns the definition only). Dead property.
- `_cell_figure`/`_FIGURE_RE`/`_YEAR_RE` (:77-78, 256-272) are a second implementation of `app/extraction/fingerprint.py:151-152, 592-597` `_is_figure`, with different regexes (`[\d,]*\d` vs `[\d,]+`, `%` excluded vs allowed). One should be the value-returning version and the other `is not None` over it.
- `_period_end` + `_SPAN_END_MONTH` (:83, 310-321) are a third parser of the canonical period namespace: `app/parsing/periods.py:506` `period_span` uses all but the identical regex. A `period_months(key)` in `periods.py` would let coverage use `period_span` and delete both.
- `_row_label` (:237-247) is a third row-label reader beside `parsing/tables.py:73 _row_labels` (first cell) and `parsing/labels.read_label`. This one is genuinely different (first non-figure cell) and earns its place; the other two do not.

**Seam.** `_quarters_no_filing_covers` (orchestrator.py:1356-1384) answers "which quarters does no filing cover" from **filing dates**; `coverage` answers it from **figures**. If coverage is ever wired, the date heuristic is what it replaces - worth saying in one of the two docstrings so the next person does not grow a third.

### B6. `2846bcc` put each comment back above the code it explains

**Value.** Correct, prose-only, and re-measured against the live ticker index. The `_REGISTRANT_SUFFIXES` staleness note is exactly what rule 1 asks for.

**But the same defect is still in the orchestrator**, unfixed, at `orchestrator.py:393-402`: four comment lines describing `_DETERMINISTIC_METHODS` (defined at :404) sit directly above `LABEL_FLAGS` (:402), with the `LABEL_FLAGS` comment appended underneath them. It predates this branch (`git log -S"How a deterministic candidate was obtained"` -> `3b27b45`, on main), so it is not a regression - but the pass that fixed three of these fixed the ones in `sources.py` and left this one.

---

## Ranked: do these

1. **Write `therapeutic_area` onto the indication row.** `orchestrator.py:1121-1130`, add `therapeutic_area=therapeutic_area(indication.disease)`. One line. Today `ProductIndicationORM.therapeutic_area` is never written (verified) and the two readers that prefer it (`dashboard/series.py:166`, `api/products.py:361`) fall through to a `"; "`-joined string that scores 0.5 instead of 1.0 on `analog_matching`'s heaviest weight. This is the whole of `6621e13`'s value arriving at the reader. *Risk: none; it is a nullable column with two readers already written for it.*
2. **One openFDA brand query instead of two.** `connectors/openfda.py:89-116` -> `openfda.brand_name:"X"+OR+products.brand_name:"X"`, verified live to return the identical union on all four brands tested including both the commit's cited gains. Deletes ~25 lines, the closure, `scopes`/`searches`, the `startswith("brand")` protocol, and halves drugsFDA calls per job. *Risk: `limit=10` now bounds the union rather than each path; raise `LIMIT` and say why.*
3. **Decide who reports a refused issuer.** `llm_search.py:117,149,180-183` + `orchestrator.py:741-751`. Return `SearchedIdentity`, drop both the mutable `quality_flags` parameter and `last_resolution`, delete the orchestrator's duplicated `cik_from_llm_search` literal. Removes two unwired channels and one dataclass constructor, and is the only version where the three named refusals reach a job. *Risk: return-type change, one production call site, one test file.*
4. **State or fix the labeler-vs-sponsor choice.** `parsing/fda_label.py:279-292`. Verified: on a drugsFDA record both keys exist and disagree (Adcirca -> United Therapeutics vs ELI LILLY CO; Revatio -> the first of two labelers, in openFDA's undocumented list order). This feeds `job.manufacturer` and therefore which issuer's filings a job downloads. *Risk: changing the preference moves the filer for products where the labeler is currently right (Adcirca); the safe move is to state the rule and stop taking `[0]` of a multi-element list silently.*
5. **Split `_retrieve` in two.** `orchestrator.py:538-545, 753-836`. Deletes the `characterisation` option-inversion literal, the `openfda: False` flag and `asked_for_filings` with its two guards. *Risk: four test call sites use `_retrieve(job, {...})` directly and would move.*
6. **Apply `MIN_ALIAS_LENGTH` to aliases, not to the product's own name.** `openfda_fields.py:79-84`. Verified: a 3-character brand keeps only its sibling extension as a candidate. One line. *Risk: none.*
7. **Make `_AREA_QUALIFIER` strip the bracketed span, not the tail.** `indications.py:132`. Verified: today it silently deletes the population qualifier the docstring promises to keep whenever a parenthetical precedes it. *Risk: re-run the 20-product area grouping above; PAH labels put the parenthetical last, so I expect no movement on the catalog.*
8. **Delete `record_coverage` and `carries_nothing`, or wire the first.** `coverage.py:110-112, 204-226`. `carries_nothing` is referenced nowhere at all; `record_coverage` is a test-only wrapper of exactly the kind this branch deleted elsewhere, and it is what makes `coverage` look wired. *Risk: `test_capabilities_are_wired` will then name `coverage` itself - which is the honest state, and the entry already exists for `record_coverage`.*
9. **Collapse the duplicate readers.** `coverage._cell_figure` vs `fingerprint._is_figure`; `coverage._period_end`+`_SPAN_END_MONTH` vs `periods.period_span`; `openfda_brand_names`' inline path tuple vs `_PATHS["brand_name"]`; `fda_label.py:242`'s second read of `indications_and_usage`. Four small dedups, no behaviour change.
10. **Tidy-ups with a reason:** give `_AREA_TRAILING_CLASS` (`who|nyha|ajcc|fab`) a staleness note; note in `parse_label_record` that `chosen()` depends on `_PATHS` order; fix the orphaned comment at `orchestrator.py:393-402`; refresh the `enable_profile_judge` comment (config.py:48-50) now that its cited defect is fixed; add `source_field` to what the judge is shown (`orchestrator.py:1289-1295`) and to the export's Drug Profile sheet (`export/builder.py:232-236`, which today writes field/value/source_url/confidence/status and drops both the quote and the key).

## Leave exactly as it is

- **`fda_label._PATHS` + `read_path`/`openfda_block`/`product_columns`** - one irreducible table, everything else read off the record, each value carrying its path. The right shape for the job.
- **`route_readings_agree`/`_terms_cover`** - "two grains of one route are not a conflict" is a real distinction and costs nine lines.
- **`get_with_backoff` + `is_sec_host` + the AST-derived `reads == paced` guard** - net simplification, and the guard is the best example of rule 1 in my scope.
- **`states_item`, `reading_order`, the derived `PRIMARY`** - each replaces a literal with the module's own answer; nothing to remove.
- **`apply_profile_judgment`** - four named refusals, a correction accepted only with a URL and a verbatim quote, no silent overwrite. As simple as that value allows.
- **`PRIORITY_JUDGE_FIELDS` + its drift test** - the prompt-scraping is awkward, but the test turns its one failure mode into a loud one.
- **`app/identity/resolver.py`** - unchanged in this range and nothing wrong with it.
- **`coverage.py`'s core** (`coverage`, the figure-not-name rule, `names_only`, `_refute_what_the_document_predates`) - judged on its own terms, this is the measurement retrieval never had, and it is honest about being a floor.

## Verified vs inferred

Verified by command, shown above: the brand-path OR union; the generic/molecule product matching nothing; the 3-character brand candidate inversion; `therapeutic_area` stripping the tail; the 20-product area grouping; labeler vs sponsor on five products; `ProductIndicationORM.therapeutic_area` having no writer; `last_resolution`/`quality_flags` having no reader in `app/`; `carries_nothing` having no reader anywhere; the orphaned comment at `orchestrator.py:393-402`; the export sheet's columns; all scope tests passing.

Inferred, not measured: that the `_retrieve` split and the `profile_fields` signature reduction are net simplifications (I read the call sites, I did not write the patch); that raising `LIMIT` is sufficient compensation for the single OR query (no seed brand has more than two applications); that the judge's per-field call count is ~11-14 per job (read from the uncapped default and the field set, not counted on a run).
