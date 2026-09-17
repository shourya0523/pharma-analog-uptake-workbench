---
title: "plan: what the seven module reviews found, and the order to fix it in"
date: 2026-09-17
type: plan
status: not-started
---

# What the seven reviews found, and the order to fix it in

Seven read-only reviewers, one per group of modules, against the product's job:
name a drug, get its quarterly revenue with a citation for every figure. Their
briefs are in `.claude/agents/`. Every number below was reproduced in the
session that wrote this file; the reviews themselves carry the commands.

The baseline they all measured is `run13`: 24 jobs, 549 datapoints, 20 of 24
cases finished, 44/75 correct on `seed/cases/shapes_holdout.json`.

    datapoints 549   auto_pass 127   needs_review 355   validation_tasks 374
    derived rows 16  member resolutions 445
    derivation_lineage 0   product_indications 0
    peak_sales_estimates 0   uptake_metrics 0   competitive_snapshots 0

## The one-sentence version

The pipeline reads well and publishes badly. Almost every defect is a claim
bound to the wrong subject, or a guard that is written and then not consulted;
and the whole of it has only ever been measured in a configuration no user
runs.

---

## 0. Before anything is fixed: measure the configuration people actually use

`ExtractionOptions` defaults `openfda: True` and `product_metadata: True`. The
UI sends `{}` and gets them. **Every case file turns them off** - all 372 gold
cases, all 24 shapes cases, all 15 unseen, all 8 sample, all 7 foreign. So
every number this project has produced describes a pipeline nobody runs, and
the ten empty tables (indications, peak sales, uptake, competitive snapshots,
lineage) are empty in measurement rather than in production.

Two config flags are also overridden in the deployed environment, both the
wrong way:

    sec_include_8k        default False  ->  true   (fetches cover pages
                                                     sources.py:10 calls worthless;
                                                     14 of 28 documents in a live repro)
    enable_profile_judge  default True   ->  false  (leaves quality/profile.py,
                                                     233 lines, entirely unreached)

**Do first:** turn the options on in the case files, unset the two env
overrides, re-run, and report the result as a second configuration rather than
as a regression or an improvement. Until that exists there is no baseline for
anything below.

---

## 1. Wrong figures published

These break job #4 - never publish a figure that is not this product's own -
and job #3 - publish it with a quote a person can check. They are ranked
first because a wrong published figure costs more than a missing one.

### 1a. Derived rows cite a document that does not contain their input

`orchestrator.py:2025` takes `selected_sources[0]` for every derived
candidate, whatever it actually subtracted.

    derived rows: 16
      cited document contains the derivation input:  3
      cited document does NOT contain it          : 13   (12 of them auto_pass)

`derivation_lineage` exists as a table and as `DerivationLineageORM`
(`db/models.py:468`) and has never been written. The schema for recording
exactly this is already there.

Fix: record the inputs a derivation used, cite them, and write the lineage row.

### 1b. A footnote's scope is taken by first regex match anywhere in the note

`labels.py:363 _note_scope` runs `_NOTE_PERIOD_RE.search(note)` and takes the
first hit. On ANI's 10-Q the note reads "no sales of YUTIQ during the quarters
ended March 31, 2026 and June 30, 2026 ... as of the second quarter of 2025" -
`_NOTE_SPAN_RE` does not know "quarters ended", so the scanner runs past the
claim and binds it to 2025Q2, from a subordinate clause about another
product's label. The suppression fires on the one quarter that was fine and
not on the two that should be empty.

Two structural faults, not one: a note naming several periods can return only
one, and "parsed nothing" is returned as "applies to the whole row" (567 of
680 corpus notes).

Fix: bind the period from the clause that carries the claim, return a set,
and distinguish "no scope" from "whole row". Widen `_NOTE_SPAN_RE` to
`quarters?/years?/period ended` and the hyphenated `N-month period` forms:
"quarters ended" appears in 145 corpus files, "the years ended" in 14 notes.

### 1c. `_INCLUDES_RE` fires on "does not include"

`labels.py:414-420` matches the claim and the product name separately, so
"Full year 2026 guidance does not include sales of YUTIQ" returns
`names=('YUTIQ',)` and flags the row combined.

Fix: one pattern carrying claim and subject, the way `_no_sales_of`
(`labels.py:378`) already does - it got every real ANI note right.

### 1d. Derivation launders provenance at four sites

`derive.py:358`, `derive.py:443`, `orchestrator.py:1279 _candidate_of`, and
`orchestrator.py:1291 _datapoint_from_candidate`, which never writes
`reported_as`, `geography` or `route_of_administration` and defaults scope to
"Product family". `_derived_point` then writes a fresh quote opening with the
bare product name, so the row asserts the identity it just lost.

Fix: carry the candidate's provenance through derivation. One field to add to
`_candidate_of` also revives `HELD_FOR_BOUND` (see 4b).

### 1e. `deterministic:product_quote_value_ok` publishes expenses and guidance

179 rows auto-passed on "product name and number appear in one sentence". Run
against real quotes it passes an impairment charge, an accounts-receivable
balance, forward guidance, a combined line and a company total. It published
AGAMREE 2023Q3 at $81.5m from "the $81.5 million IPR&D purchase consideration
for the acquisition of the license" - a quarter before the product was sold.

Fix: this is the judging change (doc 005). Nothing in this layer compares a
figure against the product's approval or launch date, though
`profile.fda_approval_date` and the `early_launch` validation reason exist.

### 1f. The combined-line demotion is overwritten

`orchestrator.py:2125` demotes a `combined_line` row to `needs_review` - the
rows carry the `label:combined_line` it appends - and `orchestrator.py:2248`
then sets `AUTO_PASS` on any supported quarterly/annual row without consulting
`label_flags`. Seven of seven published YUTIQ quarters are ILUVIEN+YUTIQ.

Fix: consult `label_flags` in the auto-pass condition. This closes the hole
properly rather than through `reported_as`.

---

## 2. A product bound to a company that does not sell it

The reported "aspirin returns another company's filing". Traced end to end,
this needs no bug - it is what the code does on that input.

- `_identity` (`orchestrator.py:593`) only calls `resolve_cik` when a ticker or
  manufacturer was supplied. A drug name alone skips the SEC name index
  entirely and goes straight to the model.
- `resolve_cik_from_search` keeps the `cik` digits and discards
  `company_name`, `confidence`, `source_url` and `notes`, all of which the
  prompt asks for. A confidence of 0.05 is accepted like 0.95.
- `SECConnector.retrieve` has no `product` parameter. Nothing can check that
  the issuer sells the product.
- A live repro stored **28 ACADIA filings, 22.1 MB, against a job for aspirin**,
  with `job.manufacturer` still None - which also leaves `issuer=""` on the
  member register, where two filers' `acme:ProductMember` collide in one slot.
- When no CIK resolves, the job is flagged `sec_retrieval_failed`, whose
  meaning is "EDGAR refused, do not fall back", and the search is skipped. The
  user is told we could not reach the SEC when we do not know who sells this.

And `resolve_cik` is unreliable when it is called. Against the real
10,422-registrant index: `Vertex` -> Vertex, Inc. (tax software);
`Vertex Pharmaceuticals` -> None; `Eli Lilly and Company` -> None; a wrong
ticker suppresses a good name. 1,966 registrants have a single-word normalised
title.

**Fix, in order:**
1. Move `_extract_metadata` before `_identity`. openFDA already knows a drug's
   sponsor and is currently asked at `orchestrator.py:419`, after the CIK has
   been guessed and the filings downloaded. This is the fix that reaches
   furthest and it depends on item 0.
2. Try the name index whenever a name exists, and try the name when a ticker
   misses.
3. Keep the model's `company_name` and `confidence`, and refuse below a floor.
4. Pass the product to retrieval, or check the resolved issuer against the
   sponsor openFDA named, and say `no_filer_of_record` rather than
   `sec_retrieval_failed` when identity is what failed.

No held-out set can see this class: every job in every run supplies
manufacturer, ticker and CIK, and there is no test of `resolve_cik` at all.

---

## 3. Correct figures withheld

20 of 75 expected figures were found, correct, and not published. Each cause
below is a bug rather than caution.

### 3a. The sentence splitter breaks table rows into "sentences"

`sentences.py:25` splits on `\s*\n+\s*`, and HTML-to-text puts each cell on its
own line, so `"EXONDYS 51\n$\n134,688"` is three sentences and
`value_and_product_in_different_sentences` fires. 326 rows, **212 with no other
objection**. The single largest contributor to the 69.4% held rate.

### 3b. `quote_states_a_different_period` reads only the first period

Mine, from this session. `periods_named_in` returns one period, so every
prior-year comparative column is rejected: "$84.6 million and $75.9 million for
the three months ended March 31, 2025 and 2024" yields `{2025Q1}` and the 2024
figure is vetoed. 142 rows, 27 alone.

### 3c. `ytd_language_as_quarterly` reads the whole quote

`client.py:828` calls `re_ytd_language(q)` although `read`, the sentence
carrying the value, is computed at `client.py:820` for exactly this. Same for
`TOTAL_REVENUE_RE` and the period check. 52 rows, 2 alone.

### 3d. `filing_contradicts_itself` is tier-blind

Its premise is that the filing said two things; when the second is a reader's
misreading of the same sentence the premise is false, and a tier-4 prose row
vetoes a tier-0 tagged fact. A tier-aware version was measured at **+3 correct,
0 new wrong**. A period-type-aware version instead is +3 and **+1 wrong** -
so tier, not period type. `test_one_figure_one_publication.py:172` locks the
current behaviour and needs rewriting to "two claims of equal strength".

### 3e. A corroborator is never promoted when the winner is held

`orchestrator.py:2461` computes corroborators from winners and never
re-examines the group. 37 quarters across six runs have a `corroborates` row
and nothing published; 14 of those corroborators were themselves clean.
FILSPARI 2025Q2 has three readings of 71.887, the strongest clean, and
publishes nothing. Measured at **+1 correct, 0 regressions**; with 3d,
**+4 and zero new wrong answers**.

### 3f. `conflicting_values` rejects both sides, by period

`check.py:231` groups by `(period, period_type, scope)` and `candidates.py:183`
drops every reading of a period with an error finding. **20 gold-correct
readings discarded**, including Pombiliti 2025Q1 = 21.005 carrying
`reported_as: "Pombiliti + Opfolda"` - the identity the replacement row lacked.
A reading flagged `label_not_understood` is vetoing a reading whose label was
fully accounted for, and the reconciler built to choose between claims never
gets the chance.

### 3g. SOURCE_PRIORITY ranks tagged facts below a model reading the same filing

An XBRL instance retrieved inside a 10-Q is typed `QUARTERLY_REPORT`
(priority 4); the human-readable document from the same accession is
`SEC_FILING` (priority 0). Both the fallback ranking and the `contested` tier
read `priority_index` before `claim_rank`. 100% of tagged facts sit in the
lower band; 32 were demoted to citations while a model's reading of the same
filing published.

Fix: `claim_rank` before `priority_index`, or type the instance by its
accession's form. This is what makes 3d and 3e hold rather than move.

### 3h. Reconciliation groups by period label and ignores `period_type`

65 groups across six runs mix period types, so a six-month figure competes with
the quarter it shares a label with. Adding `period_type` to the key at
`orchestrator.py:2293` removes the class.

---

## 4. Signal computed and discarded

### 4a. `detect_period_context` dates a filing by a year mentioned once

`periods.py:330` takes the latest year outright. Perrigo's FY2022 10-K is dated
**December 2040** from a single debt-maturity date; ANI's FY2025 as 2027. 15 of
235 datable documents pick a year named once over one named up to 180 times.
That string goes into the LLM extraction prompt, and it disables
`prose.py`'s forecast guard entirely for those documents. **The correct guard
already exists 49 lines above at `periods.py:281`.** One line, highest blast
radius in the review.

### 4b. `_declared_slack` and `HELD_FOR_BOUND` are dead because one key is dropped

42 of 42 derived rows across three runs are unbounded. The tagged reader emits
`rounding_uncertainty_usd_millions` and `_as_datapoint` reads it, but the
quarter inputs arrive through `_candidate_of`, which drops it (1d). One missing
key kills the bound guard, the `+/- n from input rounding` clause in every
derived quote, and the citation field.

### 4c. 190 six- and nine-month tagged facts are discarded

`xbrl.py:139` returns `None` for any span but 3 or 12 months, so the
span-difference rule can never be fed by tagged facts - only by tables.

### 4d. The judge is blind to what the pipeline knows

Unit, currency, geography, extraction method, filing form, sibling rows, the
filing's own tagged value for the period, the footnote. `peer_names` is
accepted by two functions and supplied by none, so `hard_veto:other_brand`
cannot fire. Footnotes reach the quote on 2 of 549 rows. See doc 005.

### 4e. Smaller

- `region_rows_no_total` is produced, stored and read by nothing.
- 1,168 of 1,183 fingerprint skip notes are unconsumable by their only reader.
- `issue_flags` holds 177 free-text sentences beside its tags, and is read with
  a substring test (`"conflict" in " ".join(...)`) that already misses three of
  the four reconciliation outcomes.
- `completeness_pct` counts held rows as answered: ILUVIEN reports 100% with 0
  published. It is also the model's guess, differing from the count on 9 of 15
  jobs, and the first review action silently replaces it.
- `dashboard/series.py` omits `revenue_scope`, so a worldwide figure and its
  U.S. component are indistinguishable and any sum triple-counts.
- `reported_as` reaches one of six surfaces; the review queue, the per-job
  page, the dashboard and both exports drop it.

---

## 5. Delete

Measured, with no production caller or no effect:

- `_search_revenue_fallback` (ran twice, produced 0 datapoints) and
  `_search_quarters_fallback` (never ran in six runs), plus
  `_quarters_no_filing_covers`, `_record_unfiled_quarters`, the
  `NO_FILER_OF_RECORD` code and the dead `SourceType.LLM_SEARCH` branch.
  **Not** to be confused with `judge_with_search`, which touches 368 rows.
- `positional.py` (116 lines): 0 PDFs in 2,153 recorded sources and 0 in the
  553-file cache.
- `adjudicate.py` (256 lines): no production caller - though
  `seed/gold/adjudication_cases.jsonl` holds the cases it was written for, so
  this is a wiring decision, not obviously a deletion.
- 10 of the 22 hold mechanisms never fire; 4 are unreachable because
  `filter_revenue_candidates` applies the same predicate upstream.
- `ValidationTaskORM.issues / judge_status / deterministic_results`: 0 of 1,850
  rows across 18 databases.
- `lot_extractor.yaml`; `Settings.llm_search_max_queries`;
  `FileStore.public_uri`; `ExtractionOptions.pdfs`,
  `random_validation_sampling`, `use_uploaded_template`.
- `tables.py`'s `extract_revenue_rows` half and its duplicate `_scope_for`.

And decide, rather than leave: `analytics/` is 846 lines whose three tables are
empty in all 19 databases, because nothing constructs them. Wire it or delete
it.

---

## 6. Infrastructure

- **No per-job deadline.** `handle_job` awaits `run_job` bare; no `wait_for`
  anywhere in `pipeline/`, `jobs/` or `main.py`. A 240s search call can occupy
  726s through retries, and httpx's timeout is per-read, not wall clock. A hung
  job holds its semaphore permit forever.
- **Startup recovery throws away finished work.** FIRDAPSE had completed 12 of
  13 stages - every figure extracted, judged and gated - and was marked
  `failed`. A job at or past `quality_checks` has nothing left that appends
  datapoints.
- **Write amplification.** One datapoint is written and committed four times,
  on SQLite, whose driver blocks the shared event loop on the write lock.
- `fetch_page` reaches sec.gov with no throttle and no retry, and 9 of 10
  `llm_search` sources across five runs are sec.gov URLs. The guard test that
  should catch this iterates a hand-written list of four names and silently
  skips module-level functions.

---

## Order of work

Each step is a commit, and each is measured against the configuration from
item 0 rather than against the numbers above.

1. **Item 0** - the honest baseline. Nothing below means anything without it.
2. **4a** - `periods.py:330`. One line, the guard already exists in the file.
3. **3a, 3b, 3c** - the three vetoes. 212 + 27 + 2 sole-blocked rows. 3b is a
   regression I introduced this session.
4. **3g, then 3d, 3e, 3h** - claim rank before source priority, then tier-aware
   contradiction, corroborator promotion, and `period_type` in the key.
   Measured at +4 with zero new wrong answers, but 3g is what makes it hold.
5. **1a, 1d, 4b** - derivation: cite what it subtracted, carry provenance,
   restore the bound. One change to `_candidate_of` serves all three.
6. **1b, 1c, 3f** - the footnote clause binding, `does not include`, and
   `conflicting_values` not rejecting both sides.
7. **1f** - `label_flags` in the auto-pass condition.
8. **Section 2** - identity, starting with the stage reorder that item 0 enables.
9. **Section 5** - the deletions, once nothing above depends on them.
10. **Section 6** - the job deadline and recovery.
11. **Doc 005** - the judging change, last, because it is measured against a
    deterministic pass that steps 2-7 have made correct.

## What rule 4 requires

Everything above is diagnostic. The numbers come from `shapes_holdout`, which
`run13` was built against, and from gold, which found several of these - so
neither may score a fix.

Gold's 55 drugs and the shapes holdout's 22 are disjoint, and so are their
issuers, so **gold remains available as an oracle for finding further defects
of this kind** - it is spent only as a scorer for what it found.

A new held-out set is owed before any of these numbers is reported as an
improvement, drawn from issuers none of `gold`, `holdout`, `holdout2`,
`holdout_labels`, `holdout_members`, `holdout_foreign_xbrl` or `shapes_holdout`
uses. ANI, Perrigo, Collegium, Travere and Amicus all appear in the diagnostics
above and are therefore already spent for this purpose. The set must contain
figures that should be refused as well as figures that should pass: several
fixes above raise the publish rate, and a set of things to publish cannot catch
a fix that publishes too much.
