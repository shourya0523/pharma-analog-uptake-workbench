---
title: What the audit found - reduce complexity, keep the value
date: 2026-09-17
status: complete
---

# What the audit found

Six read-only reviews of everything between `main` (`744864d`) and the pushed
head (`dde7fb0`): 91 commits, 150 files, roughly 4,000 net new lines of
application code, 5,400 of tests, 3,000 of plans. Each review held one body of
code and answered one question for every change in it: **is this the simplest
code that delivers the value the commit claims?** Reviewers read frozen
snapshots (`830aad2` for four of them, `dde7fb0` for the two whose scope M5
touched), edited nothing, and marked every finding verified-by-command or
inferred. Their full reports are the record; this document is the decision
over them.

The short version. **The value is real and mostly measured** - every reviewer
re-ran load-bearing measurements from the commit messages and they reproduce.
**The complexity that is not paying for itself has a shape**, and it is the
shape parallel work produces: the same idea built twice by two modules, a
parameter threaded for one caller, a record written that nothing reads, a fix
that landed beside the code it should have replaced. And **the fixes introduced
defects of their own** - seven in section 2 and seven more in section 5, three
of them serious: a metadata branch that can no longer run, a peer list read
from the wrong document, and a held-out set that skips the step it was drawn
to score.

Nothing below scores anything. The smoke run (section 6) is the score.

---

## 1. Cross-cutting: the same five shapes, in every module

| shape | instances found | cost |
|---|---|---|
| **written and never read** | derivation lineage table; coverage verdict; issuer-refusal flags; `therapeutic_area` column (no writer); `series_selection` on the dashboard point; five review-queue fields; `states_a_scope`; `carries_nothing`; `lot_extractor.yaml` (loaded by nothing); four prompt response fields; the completeness model call (0 rows in four runs) | ~200 lines, N row writes per figure, and one value (therapeutic area) that scores half credit because the column meant to carry it is empty |
| **one idea, two implementations** | re-settle after review (2); peer-name reading (2 modules, composed verbatim twice); "same figure" (precision-aware in reconciliation, byte-equal in selection); "one group" (scope key vs series identity); geography normalisation (uptake yes, peak selection no); period grammar (two compiled spellings); figure/period/row-label parsers in coverage vs fingerprint/periods/tables; brand-path tuple (3 spellings); `_job()` fixture (7 byte-identical copies); holdout guard properties (5 files) | the two copies can disagree, and in two cases already can (finding 4.3, 2.6) |
| **threaded for one caller** | `lineage` out-parameter through two derivation functions; `profile_fields`' seven kwargs from one call site; `claim_ranking` returning three closures where one caller uses one; the `characterisation` option-inversion literal | signatures wider than their use |
| **a fix beside the code it replaced** | the stage reorder left the narrative-metadata branch unreachable (95 lines that read as live); the chart still arbitrates with the old corroboration marker beside the new selection field; `record_coverage` kept as a wrapper of the kind the same branch deleted elsewhere; two hard vetoes that 6a's sentence unit made unreachable or redundant; a prompt rule the code removed and the prompt still gives | dead code that reads as live is the expensive kind |
| **a number where a shape belongs** | six size ceilings in the holdout guards (the set cannot grow); measured counts in three test docstrings; two plans marked `not-started` after 30 items shipped | rule 5 in test and doc form |

---

## 2. Defects introduced by the fixes - fix these, they are not optional

Ranked by what they cost the analyst. Each was verified by the reviewer's
command and re-checked on the snapshot by the coordinator where marked (*).

**2.1 The stage reorder unplugged the narrative metadata branch.** (*)
`run_job` now calls `_retrieve` twice and `_extract_metadata` between them,
so the metadata step only ever sees openFDA sources; the ~95-line branch at
`orchestrator.py:1289-1362` that reads metadata from filings (`formulation`,
`ticker`, `cik`, cross-source conflicts) can no longer run, and the guard for
the reorder monkeypatches the step away. Probe: `llm.extract_metadata called
with: []`. **Do:** split into `_label_metadata` (before identity) and
`_narrative_metadata` (after filings), or delete the branch and say the
profile comes from the label. Either beats dead code that reads as live.

**2.2 The therapeutic area never reaches the reader.** (*)
`ProductIndicationORM.therapeutic_area` has no writer; the two readers that
prefer it fall through to a `"; "`-joined profile string, and analog matching
scores `indication_area` by containment - 0.5 instead of 1.0 on its heaviest
weight, against every single-indication product. **Do:** one line at the
indication-row write, `therapeutic_area=therapeutic_area(indication.disease)`.

**2.3 The held-out set hands the pipeline the CIK.** (*)
`holdout_2026_09.json` carries `cik` on all 12 cases; `example_drugs.csv` has
no such column; `_identity` skips resolution when a CIK is given. So the set
drawn to score the fixes skips section 8's own step, and gold (0 of 593 with
a CIK) does not - the two numbers are not comparable. **Do:** drop `cik` from
the 12 cases and add `assert "cik" not in case` to the guard. The smoke run
re-scores the set without CIKs (section 6).

**2.4 A promoted fact can be withdrawn by a reading the pipeline refuses to
publish.** 6e promotes the strongest clean corroborator when the winner is
held; 5f's carry then copies label flags onto it from every agreeing row,
including unpublishable ones, and a carried flag sets `needs_review`. Probe:
tagged fact promoted, then re-questioned by a `partial_period` table row that
is simultaneously recorded as corroborating it; nothing publishes. Unrealised
on run13 (4 of 92 groups carry, all the YUTIQ case, all correct). **Do:**
carry only from rows that themselves publish, or skip the carry when the
winner was replaced. One condition.

**2.5 The alias splitter re-opens the combined-line bug through the generic
name.** `_spells` tests substring containment both ways against the generic;
`product_aliases("NuVessa","acme-1",extra=["NuVessa/Acme"])` yields `Acme`
as our own alias. **Do:** word-boundary prefix on a held name. The predicate
gets smaller.

**2.6 Two definitions of "same figure" and of "one group".** Reconciliation
groups on scope key and compares within declared precision; selection groups
on the identity and compares byte-equal. On run13, 45 of 359 reconcile groups
hold more than one series identity (137 rows); `[159.186 @ A, 159.2 @ B]`
selects both. They protect each other by accident today. **Do (low urgency,
not blind):** make the selection's "same figure" call
`_agrees_within_declared_precision`; unifying the grouping moves 137 rows and
needs its own held-out set (rule 4). Deleting the cross-identity collapse
(3.3 below) removes half the exposure for free.

**2.7 The peer list leaks across documents** (*) - section 5. Any job with two
or more parseable sources filters every source's LLM candidates by the last
document's peers. One dict key.

**2.8 Smaller, verified, one line each.**
- `MIN_ALIAS_LENGTH` is applied to the product's own name: a 3-letter brand
  keeps only its sibling extension as a candidate (`openfda_fields.py:79-84`).
- `_AREA_QUALIFIER` strips from the first bracket to end of string, deleting
  the population qualifier the docstring promises to keep (`indications.py:132`).
- `_manufacturer` prefers the labeler over the applicant and takes `[0]` of a
  multi-element list in undocumented order; this feeds `job.manufacturer`
  and so which issuer's filings a job downloads (Revatio -> Pfizer, not
  Viatris). State the rule; stop taking `[0]` silently.
- `extract.py:736` still logs a `no_sales` row as skipped after the change
  that publishes it flagged.
- `_includes`' negation guard is one-sided (two lookbehinds); the symmetric
  tempered window is shorter. Zero corpus incidence today.
- `test_review_moves_completeness.py:95` runs the app lifespan and migrates
  whatever `DATABASE_URL` points at (verified: it stamped a container-local
  file to `008`). Drop the `with`.
- `test_capabilities_are_wired` counts a reference from inside the defining
  module, so a chain of unwired functions is invisible.
- `eval.py --attach` matches a run on the window alone and can score a
  different configuration; `--members` prints no configuration header.
- `docs/pipeline.md` lists the stages in the pre-reorder order and omits
  `_expand_aliases`; `docs/evaluation.md` omits `holdout_2026_09.json` and
  names one published status where the code counts two.

---

## 3. Simplifications, ranked by complexity removed x value preserved

Each line: do this / what it preserves / risk. Line counts are the reviewers'.

### Orchestrator and pipeline

1. **Delete `_record_derivation_lineage`** (`orchestrator.py:1741-1807`), keep
   `citation_json["derived_from"]`. -67 lines, -(2N+1) row writes per derived
   figure; the citation reaches the dashboard, the table joins nothing, and its
   docstring's dedup claim is false (no lookup, so a shared input is written
   twice). Risk: a join that never happened becomes a JSON scan.
2. **Stamp the series identity once**, in `_quality_and_validation` (drop
   `:1737` and `:2455`). Nothing reads it between creation and the re-stamp.
   Risk: a job that fails before quality leaves null identities - which the
   column already defines as "undecided".
3. **Delete the cross-identity value collapse** (`series_identity.py:265-282`).
   Measured 0 effect on 549 run13 rows; fails in the wrong direction for a
   US-only product whose Worldwide and U.S. lines carry one number. Risk: two
   identities holding one identical figure would both publish.
4. **One `resettle_series`** beside `select_job_series`, called from the three
   review paths; today `main.py:541` re-stamps every row and
   `api/products.py:717` stamps only the new one. Closes a legacy-row edge case.
5. **Split `_retrieve`** into product-documents and filings; deletes the
   `characterisation` option-inversion literal (a written-down list of every
   non-openFDA option, stale the day a sixth source lands), the `openfda:
   False` flag and `asked_for_filings` with its two guards. Do this with 2.1.
6. **Return the derivation lineage instead of threading it** (`derive.py:117,
   286`): one production caller. -2 optional parameters, -4 guards; ~10 test
   call sites gain `.output`.
7. **Memoize peers by source in `_judge`** (549 computations where 162 would
   do on run13), and **one peer-name function** instead of
   `sibling_row_labels` + `peer_product_names` composed verbatim at two sites
   with the alias set computed twice.
8. **`claim_ranking` returns what its caller needs**; `select_job_series`
   discards two of three closures and pays a query for them.
9. **Link `carried()`'s nine-key list to `_candidate_of`** (a test that the key
   sets agree); delete the unreachable `record is None` branch.

### Retrieval and characterisation

10. **One openFDA brand query** (`openfda.brand_name:"X"+OR+products.brand_name:"X"`)
    instead of two sequential ones merged by a closure with a scope-prefix
    protocol. Verified live to return the identical union on all four brands
    tested, including both the commit cited as its gain. -25 lines; halves
    drugsFDA calls per job. Risk: `limit=10` now bounds the union; raise it
    and say why.
11. **Return `SearchedIdentity` from `resolve_cik_from_search`** and let
    `_identity` merge its flags. Deletes the mutable `quality_flags`
    parameter, `last_resolution`, `nothing()` and the `not_asked` branch, and
    the orchestrator's duplicated `cik_from_llm_search` literal; it is the only
    version in which the three named refusals reach a job.
12. **Collapse `profile_fields`' seven kwargs** by computing the four derived
    values inside (one call site threads them all); keep `moa_value` as the
    one override because its check lives in `quality/`.
13. **Dedup the small readers**: `coverage._cell_figure` vs
    `fingerprint._is_figure`; `coverage._period_end` vs `periods.period_span`;
    `openfda_brand_names`' inline tuple vs `_PATHS["brand_name"]`;
    `fda_label.py:242`'s second read of `indications_and_usage`. Delete
    `carries_nothing` (referenced nowhere).
14. **Coverage: consume it or delete it.** The verdict is now written per
    document (`_parse`, M5) and read by nothing but the table browser. The
    module's core is good and honest; the recorder is the measurement retrieval
    never had. Decide in M10: wire a reader (the `_quarters_no_filing_covers`
    date heuristic is what it should replace) or drop the write.

### Parsing and extraction

15. **Fold `_PERIOD_PHRASE_RE` into `_NAMED_PHRASE_RE`** (`periods.py:156,162`).
    Verified no-op across all 433 cached documents; deletes a constant and the
    comment that exists to justify two.
16. **Collapse `NoteReading.months` + `periods` into one `(months, key)` set**;
    deletes the test-only `states_a_scope` and the mixed-span hazard in
    `_dates_part_of_the_period`.
17. **State the loss in `_states_a_figure`'s docstring**: a `label_not_understood`
    row's own quote is no longer checked. The shipped design is the simpler
    one; the loss is real and unstated.

### Surface and analytics

18. **Let the chart read `series_selection`** before falling back to
    `corroborated_by`. Retires ~40 lines of parallel selection in
    `dashboardModel.ts`; the riskiest item here (the chart's history of visible
    bugs) - rebuild the 8 fixtures around real identity/selection values first.
19. **Render what is emitted**: `series_selection` and `geography` on the
    review-queue row (the field that says "confirming this duplicates a
    selected quarter" is already in the payload and shown nowhere);
    `series_selection` in the dashboard drill panel; `source_field` on the
    export's Drug Profile sheet and in what the profile judge is shown.
20. **`peak_sales.scope_key` must normalise geography** the way `uptake.py`
    now does, and the analytics dataclasses need `reported_as`/the identity
    before any of the 12 `NOT_WIRED` entry points gets a caller - otherwise the
    combined-line bug re-enters layer 3 the day it is wired. Zero cost today.

### Tests, evals, docs, agents

21. **One parametrised holdout guard** over `answer_keys.answer_key_paths()`
    for the four properties five files repeat; per-set files keep only what is
    unique. -350-400 lines; a new case file is covered the day it lands.
22. **A `conftest.py`** with `job_db()`, `datapoint_row()`, `reconcile()`:
    seven byte-identical `_job()`s and five `_reconcile` signatures today, and
    `create_engine` in 38 files.
23. **Fold four new one-concern test files** into their existing homes (three
    into `test_one_figure_one_publication.py`, one into
    `test_publication_gate.py`). After the fix pass, not during.
24. **Drop the six size ceilings** in the holdout guards (the set cannot grow),
    cut the three measured numbers and ~250 lines of before-and-after from the
    new test docstrings (rule 5 in test form).
25. **Two slow tests are 32% of the suite**: a module-scoped engine fixture
    for `test_every_label_flag_holds_the_row`; profile the 9.9s one. Add
    `filterwarnings` (67k warnings bury real errors).
26. **Plans**: set 007 `status: complete`; cut 006 to the open items
    (sections 4, 10, 11, 12) with a one-line `item -> commit` index for the
    ~30 that shipped; move the 11 one-shot `verify-m*.md` beside 007 (they pin
    line numbers that no longer exist), keep the protocol and the reviewers.

---

## 4. Leave exactly as it is

Every reviewer produced this list unprompted, and they agree on the shape:
**where a change replaced a written-down list with its producer, or made one
definition where there were several, it earned its lines.**

- `pipeline/series_identity.py` and the selection: one key, one vocabulary
  with an explicit unrecognised bucket, one enum, one migration, both answers
  in every guard. The best-evidenced commits on the branch.
- `export/builder.py`'s columns derived from `DatapointORM.__table__`, and
  `/config` deriving from `Settings.model_fields` with credentials stripped by
  name and by DSN structure.
- `fda_label._PATHS` + `read_path`/`product_columns`: one irreducible table,
  every value carrying its path. `route_readings_agree`. The launch anchor
  written after both records.
- The single paced fetcher with `is_sec_host`, the AST-derived `reads ==
  paced` guard, `states_item`/`reading_order`/derived `PRIMARY`.
- `apply_profile_judgment`'s gate (a correction only with a URL and a verbatim
  quote; four named refusals; no silent overwrite).
- `MONTHS_TO_PERIOD_TYPE` + `period_label` (net deletes decision sites);
  `Cell`/`cell_of`/`Finding.cells`; `quality/sentences.py`'s 12 lines;
  `xbrl.roots`/`settles` (loses no revenue element across 52 linkbases); the
  multi-date footnote reader (serves 16 distinct corpus notes, not one).
- The four analytics fixes as fixes; the widened `_candidate_of` (all 13 added
  keys consumed); `_read_from`; the migration.
- `scripts/build_gold_cases.py` with its flags generated from the options
  model; `tests/answer_keys.py`; `eval.py`'s `configuration()` and
  `unexamined()`; `docs/sourcing/excluded-products.md`; the README's refusal to
  print a headline score.

---

## 5. Judging layer

The one review that started from a measurement the register never made:
**does the judge ever overrule a deterministic reader?** Replaying the current
vetoes over all 549 run13 rows, 513 (93%) never reach a model; of the 12
deterministic rows that did not publish, the judge held exactly one. The rest
were held by the filing contradicting itself, by label flags, or by the search
validator. That is not an argument for deleting the judge. It is an argument
that vetoes that never fire are carrying complexity for nothing, and that the
one gate deciding 93% of rows is under-specified.

**Defects (verified; the first re-checked on the snapshot by the coordinator).**

- **The peer list leaks across documents.** (*) `peers` is computed per
  document in the prepare loop (`orchestrator.py:2197`) and read in the second
  loop (`:2318`) without reassignment, so every source's LLM candidates are
  filtered by whichever document was prepared last. Any job with two or more
  parseable sources can drop a correct candidate whose quote names a brand
  printed in a different filing. Introduced by 7d. Fix: store `peers` in
  `prepared[src.source_id]` like every other per-document value.
- **`_spans_named_in` discards the key-less period its own docstring exists
  for.** `spans_named_in("For the Six Months Ended June 30,")` is empty; with
  the column years more than 120 characters below the heading, a six-month
  figure auto-passes as quarterly with no model asked. One line; simulated
  zero change on the 549 quotes, documented behaviour restored.
- **The judge is told the opposite twice.** `evidence_judge.yaml:11` still
  orders a "misclassified" verdict on a six-month heading, which is the rule
  6b removed and lines 30-34 of the same prompt now contradict. Four of its
  rules are applied deterministically after the model answers, so the model's
  opinion on them is discarded either way. And the commit that says the judge
  is shown the filing's product list does not show it: `peer_names` reaches
  only the veto, never the prompt.
- **The coverage denominator throws away a third of the work.** `expected`
  intersects on both sides, so 40 published quarters across run13's 24 jobs
  count for nothing, and the card prints "9 qtrs" beside "80%" from two
  different universes. One word: `| held | missing`.
- **An unquoted model number can become a figure.** `DATAPOINT_ENRICH_FIELDS`
  includes `value_reported` and `period`; nothing grounds a suggestion. The
  cap and forced review are real, and run13 never exercised it (it filled
  geography 109 times, a value 0 times). Drop the two value fields.
- **Free prose in a codes column.** The model's `issues` sentences are written
  to `issue_flags`, and reconciliation later tests `"conflict" in
  " ".join(flags)` - a sentence containing the word marks the row a conflict.
- **A model call per job that produces nothing.** `llm.completeness()` runs
  at the last stage; its `completeness_pct` is unread since M0/M4, its
  `limitations` and `recommended_next_steps` were always unread, and across
  all four run databases it wrote 0 of 64 unresolved-quarter rows.

**Simplifications, in the reviewer's order.**

1. Store `peers` per document (the fix above). One dict key.
2. Delete the `ytd_language_as_quarterly` veto branch and `_QUARTER_SPAN`:
   after 6a made a table row one unit, the row never carries the heading, so
   the veto cannot fire alone on any production candidate (its test passes
   only because its fixture has no `period`). Keep `names_a_year_to_date_span`
   for `fast_judge.py:85`, which blocks auto-pass on 121 two-span quotes.
3. Delete `company_total_without_product`: wholly inside
   `product_missing_from_quote` on every real row; `TOTAL_REVENUE_RE` matches
   0 of 549 quotes.
4. `_spans_named_in` calls `periods.periods_named` plus the two prose-only
   patterns ("full-year 2002", "the second quarter and first six months");
   three of prose's five period regexes are strict subsets of the parsing
   grammar. Confine this to the judge's path: rewiring the extractor's
   ambiguity test at `prose.py:496` is a behaviour change that needs its own
   held-out set.
5. The coverage denominator fix, and inline `coverage_pct` (one caller).
6. Delete `prompts/lot_extractor.yaml` (loaded by nothing) and the four
   response fields nothing reads.
7. Reconcile the prompt with the code (rule 4: a prompt change is scored on a
   fresh held-out set before its number is claimed); drop the duplicated
   `label_residue` from `{context}`; explain `label_flags`/`label_residue`,
   the two candidate keys the model has no other way to interpret.
8. Export the `period_type -> months` inverse from `parsing/periods.py`; it
   is built byte-for-byte twice, in `client.py` and `check.py`, in one pass.
9. Un-gate the footnote text: the note travels on the quote whenever the mark
   is cited; only the `partial_period` flag stays behind `applies_to`. Today a
   mis-parse blinds the judge at the moment it could have caught the error.
10. Decide what `llm.completeness()` is for, or delete the stage.
11. Sanitise `issue_flags` to codes; the model's prose goes to
    `reviewer_notes`.

**Not a simplification, but the largest finding for the analyst.** The
auto-pass gate `product_quote_value_ok` asks only whether the number and the
name appear anywhere in the quote. On a two-year comparative row it passes
the prior-year column claimed as the current quarter, and 210 of the 293
auto-passes on run13 sit in a unit holding more than two numeric tokens. The
year-cross that saved 76 correct rows from a spurious veto is also what stops
the period veto from catching this. Smallest closure: the value must sit in
the same unit as the product and that unit must hold no other money figure,
else fall through to the model. This is a rule-4 change: build the held-out
set first.

**Leave as is:** the year-cross in `periods_named_in`; `_period_claimed_by`;
`fast_judge.py:85` reading the whole quote; the coverage guard asserting both
endpoints; enrichment's cap and forced review; `llm/aliases.py` and
`llm/grounding.py`.

---

## 6. What this hands to M10

M10 was "delete and infra". It now takes, in this order:

1. Section 2, all of it, and section 5's defects - the introduced defects,
   one commit each, each with the reviewer's probe turned into the guard test.
2. Section 3 items 1-17 and 21-26, and section 5's simplifications 1-6 and
   8-11 - the changes with no behaviour change or a measured zero, each
   verified by re-running the reviewer's command before and after.
3. Its own original list (`_search_revenue_fallback`, the three dead
   `ValidationTaskORM` columns, the two orphan prompts, `llm_search_max_queries`,
   `FileStore.public_uri`, the `tables.py` half; the per-job deadline; startup
   recovery).
4. Not 18 or 20, not 2.6's grouping unification, not section 5's prompt
   reconciliation (7) or the auto-pass gate: those are behaviour changes on
   the chart, on 137 rows, on the judge's prompt and on 293 auto-passes, and
   each needs its own held-out set before it is scored (rule 4).

The smoke run against `dde7fb0` is the baseline every one of these is held
against: the same case files, the same configuration header, and a number
that must not move for a change that claims to change nothing.

---

## 7. Smoke run 1: the held-out set, as a user would run it, on `dde7fb0`

Server started with every shell override unset except the API key, the SEC
contact string and the two model names (extract `google/gemini-3.8-flash`,
judge `openai/gpt-4o-mini`); `/config` reported those four of 38 and nothing
else. Every layer-two option on. Case file `seed/cases/holdout_2026_09.json`
as it stood, i.e. **with the CIK handed in** (2.3); the run without CIKs
follows. One job (MIPLYFFA) was mid-extraction when the container restarted
and startup recovery marked it failed rather than re-queuing it (section 11's
"recovery throws away work", observed live); it is unscored.

    88 expected figures across 11 scored cases        correct 64 / 88
      correctly silent           33
      published, correct         31   (xbrl_fact 13, table 7, llm 5, prose 4, derived 2)
      no answer                  15
      published, wrong identity   6
      answered anyway             2
      published, WRONG            1
    37 published (product, period) pairs no expectation examined

Diagnosis, not building. Each loss class traced in the run's own database
(`.../scratchpad/wt-audit2/backend/storage/workbench.db`) and the eval detail.

**No answer (15) is retrieval, not extraction, and it is the shipped
default - with the causes now verified.** Every one of the 12 jobs holds
exactly 4 `sec_filing` documents - `sec_max_filings = 4` as the code declares
it (every earlier number ran with 25), spent annual-first so no quarter is
reached. Traced per quarter against the document gold cites: 7 are 10-Q pages
never fetched whose accession the job already fetched as an XBRL instance
that found nothing (Neurocrine does not tag product revenue: `grep -ci
ingrezza nbix-2024*_htm.xml` -> 0, 0, 0); 5 are earnings exhibits rejected by
`is_earnings_exhibit`'s filename pattern, which Neurocrine's, Eton's and
Zevra's filing agents do not follow - the item 2.02 gate passes for all of
them; 1 is an acquired-business 8-K/A; 2 are not retrieval. One, not three,
is the FY-minus-nine-months class. Register 12e carries the design and the
corrections.

**The coverage verdict** is `names_only` on every 10-K page and every XBRL
instance, and `partial` on 40 of 244 - not on all documents, as an earlier
draft here said. The causes are in 12e; none is the row-grouped case.

**Wrong identity (6): the key asks for a pair label on one product, and this
document's first account of it was wrong.** An earlier draft here said the
pipeline stamped `reported_as='Upstaza/Kebilidi'` because `Kebilidi` was
missing from the alias set, and proposed feeding the openFDA record's brand
names into the aliases. Verified against the smoke database, every premise
fails: `select distinct reported_as from datapoints` -> `[None]` on all 596
rows; the job's stored `llm_aliases` has `Kebilidi` third of thirteen; the
job's only openFDA source is `OpenFDA no match`, and drugsFDA returns 404 for
both names today (a CBER gene therapy). The draft was written from the eval's
state name, not from the rows - rule 2's failure, in the document that cites
rule 2. The six figures are exact and published under the product's own name.
What scores them wrong is `seed/cases/holdout_2026_09.json`, which expects
`reported_as="Upstaza/Kebilidi"` on every valued period; `read_label` returns
the same one-product reading with or without `Kebilidi`, because an unmarked
slash-joined name is tolerated beside a name it knows (`labels.py:339-344`),
and even a stamped pair would score wrong, since the code's vocabulary is
`A + B` and the key wants the filer's `A/B`.

The filer says it is one product: "This gene therapy is approved and marketed
with the brand name Kebilidi in the United States" (10-K, accession
0001104659-26-017575), one worldwide line, singular verb. A pair stamp would
put every quarter of the one complete launch curve of a rare-disease gene
therapy into the review queue as "reported only with another product". So
the pipeline's answer is the analyst's answer and the key's expectation is
the defect; correcting it is a property ("a regional brand pair is one
product"), not a fit, and is a decision for a person because the set is
scored. Not an alias feed: measured over the 20 seed products' matched
records, brand names that are not the product are 2 (a titration pack and a
diluent), and the generic fallback's window for one product carries its four
nearest competitors - an alias feed from retrieved rather than matched
records would read every competitor's row as the product's own.

The real defect on this product is 2026Q2, scored "no answer": the 10-Q row
`Upstaza/Kebilidi 11,163 11,889` had its current-quarter value filed as
2025Q2 and superseded by the tagged fact. The two-number row again.

**Answered anyway (2) is the auto-pass gate the judging review named.** The
Sephience 2025Q2 figure 26.741 is a `table` row from the **2026Q2** 10-Q
(`tmb-20260630x10q.htm`), quote `Sephience $ 22,078 $ 26,741`: two numbers on
one row, the figure assigned to the comparative column, auto-passed with
`deterministic:product_quote_value_ok` - which asks only that the number and
the name appear in the quote. 2025Q1 is then derived from that figure. This
is section 5's "210 of 293 auto-passes sit in a unit holding more than two
numeric tokens", arriving as a wrong publish on a fresh set. Rule 4 item;
build its held-out set before changing the gate.

**Published, WRONG (1) is the issuer disagreeing with itself.** Translarna
2024Q4: the run holds three readings - 93.7 (Feb 2025 release, "for the
fourth quarter of 2024"), 89.1 (Feb 2026 release, same quarter as the
comparative), and a `Translarna France (98,629)` adjustment row - and gold
says 74.854 from the 10-K. The pipeline auto-passed the issuer's own headline
figure. Whether gold or the release is the analyst's answer is a question
about that filing, not about the code; opened as such, not asserted.

What moves the number, in order, and what each costs: the four-filing cap is
a configuration decision and 12e's set cover replaces it;
the Upstaza six are the key's expectation, a decision for a person; the two-number
row is the auto-pass gate and needs a new held-out set; Translarna needs a
person to open the 10-K. None of these is in the M10 tracks now running.
