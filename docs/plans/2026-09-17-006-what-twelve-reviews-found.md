---
title: "plan: what twelve reviews found, and the order to fix it in"
date: 2026-09-17
type: plan
status: not-started
---

# What twelve reviews found, and the order to fix it in

Two passes. Seven module reviewers, one per group of files, against the job
"name a drug, get its quarterly revenue with a citation". Then five product
reviewers who were given no findings and told not to read the first pass, and
were framed on the whole product rather than layer 1: build the series,
characterise the product, do the analog work.

The second pass changed the order of this document. It did not overturn the
first pass's layer-1 defects - those are kept below, and several were
independently rediscovered from a different direction. What it changed is what
comes first, and why.

## What the second pass changed

**The first pass measured against a number that does not mean what it says.**
The README's headline is scored on `seed/cases/gold_all.json`, which was not
rebuilt when gold was. Every conclusion in the first pass that carried a
percentage carried this with it.

**The first pass ranked "wrong figure published" above "figure withheld", and
that was right - but it was looking at datapoints.** The expensive wrong
numbers are not in the datapoints. They are the three the analyst reads off
the screen: a chart plotting the ex-U.S. figure for a worldwide quarter, a
coverage percentage that reads 100% on an empty series, and a series labelled
as one product that is two.

**The first pass treated layers 2 and 3 as "not wired yet".** They are worse
than unwired: layer 2 emits attributes that are confidently wrong rather than
absent, and layer 3's method is unsound in four places independent of any
wiring. Wiring them today ships wrong numbers into an export column an analyst
will act on.

## The one-sentence version

There is no baseline; three numbers on screen are wrong rather than absent; the
artifact layer 1 produces is not a series; and the two layers that are the
product would, if wired today, publish confident errors.

---

## 0. There is no baseline. Nothing below can be measured until there is.

Four separate reasons, all verified. Until they are fixed, no number in this
document - including the ones the first pass measured - can be reported as an
improvement or a regression.

### 0a. The answer key the headline is scored on is 64% of gold's rows, short three issuers  `[V]`

    gold rows          : 2203      gold products : 55
    gold_all.json      : 1415      case products : 39
    in gold, not in gold_all.json:
      Alimta, Basaglar, Cialis, Cyramza, Emgality, Forteo, Humalog, Humulin,
      Jardiance, Mounjaro, Olumiant, Retevmo, Taltz, Trulicity, Verzenio, Zepbound

Every Eli Lilly series is absent as a *product*. But by *rows* the hole is
wider than one issuer:

    manufacturer          gold rows -> in case file
    Johnson & Johnson           770 ->  424
    Gilead                      609 ->  541
    Eli Lilly                   374 ->    0
    United Therapeutics         368 ->  368
    Actelion/J&J                 58 ->   58
    Merck                        19 ->   19
    Liquidia                      5 ->    5
                                788 rows uncovered

Gold grew four times on 2026-09-15 - `a69b887` (Lilly, 1,415 -> 1,789),
`0598973` (J&J schedules, 1,945), `fc9df36` (Gilead, 2,013), `6075aa1` (J&J
10-Q backfill, 2,203) - and `seed/cases/gold_all.json` was last touched on
09-11 (`4f6a65d`), before all four. 1,415 was gold's size on 09-08 when
`941d516` wrote `README.md:89` ("1,070 of gold's 1,415 quarters (75.6%)").

Two ratios, not one: the case file is 1,415/2,203 = **64.2%** of gold's rows;
the README's score is 1,070/2,203 = 48.6% of gold. An earlier draft of this
heading conflated them.

The values that *are* in the case file still match gold exactly - 0 mismatches
over all 1,415 - so this is a coverage hole rather than a corruption. But
`grep -rn "gold_all" backend/tests/` returns nothing: no test fails when they
diverge, and the hole grows every time gold grows. `docs/evaluation.md:39`
calls `gold_all.json` "every product-year in gold", which stopped being true on
2026-09-15.

**Do:** rebuild the case file from gold, add a test that fails when the two
diverge, re-run, and replace the README number with its as-of date and gold
size.

### 0b. The eval cannot see a refusal in the two files the headline comes from  `[V]`

`gold_all.json` and `gold_sample.json` carry 1,415 and 32 expectations and
**zero** `value_normalized_usd_millions: null`. So `correctly silent` and
`answered anyway` are unreachable states, and the headline cannot detect
over-publication at all. `docs/evaluation.md:44-46` asserts the both-answers
property directly under the case-file table.

This is CLAUDE.md rule 4's "a set that only refuses is passed by a system that
always refuses", mirrored: a set that only publishes is passed by a system that
always publishes. It matters now because several fixes below raise the publish
rate.

(`shapes_holdout.json` 14/92 null, `foreign_xbrl.json` 4/7, `unseen.json` 2/15
are fine and properly guarded.)

The structural cause is rule 1: a file built by slicing gold's rows cannot
contain a quarter gold lacks, and gold's own records of absence -
`series_coverage.jsonl`'s `issuer_stopped_reporting`, `excluded_products.jsonl`
- were wired to no eval. **Rank:** understated. The analyst's exposure is
over-publication, this document's own #1 class, and this is the measurement
gap that let section 1 exist unseen.

### 0c. The eval and the pipeline disagree by construction about what a conflict is  `[V]`

`scripts/eval.py:193` takes `spread` over every published value for a period
with no scope filter. The pipeline deliberately keeps scopes apart
(`_scope_key`, `orchestrator.py:167`, merges only Worldwide with Product
family). Measured:

    AYVAKIT 2024Q4  ->  144.1 Worldwide, 124.1 U.S., 20.0 ex-U.S.  (all auto_pass)

The expected answer, 144.1, is present and published. The eval reports
"published, conflicting" and counts it against the score. The disagreement
moves the headline in the direction that looks like a defect.

Reproduced by executing `score()` on the three real AYVAKIT rows, not by
reading it: `{"state": "published, conflicting", "want": 144.1, "read": 144.1,
"candidates": 3}` - the expected value is present, published, returned as
`read`, and the state is still "conflicting".

Three further blindnesses, each verified by execution:

- `score()` iterates `case["expect"]` only. On run13 under eval.py's own
  `TERMINAL` set (`ready_for_review`, `completed`, `failed`, `cancelled`), **47
  of 89** published (product, quarter) pairs are never examined. (An earlier
  draft said 35 of 73; that pair reproduces under no natural filter. The shape
  is robust - about half of what run13 published is invisible to the score.)
- **Scope is never read.** No `expect` row in *any* case file carries a scope
  or geography key; the only identity check is `covers()` on `reported_as`,
  which `gold_all.json` never sets. Demonstrated on the real U.S.-scope AYVAKIT
  row: `score()` returns "published, correct", and returns the identical result
  after the row's `revenue_scope` is replaced with `acme:NotAScopeAtAll` and its
  geography with `Nowhere`.
- `eval.py:193`'s `spread` is rule 1 in substance: the eval already receives
  each datapoint's `revenue_scope` and uses a written-down assumption ("one
  value per period") instead. And `eval.py:40,42,112` hand-copy `JobStatus`
  and `PUBLISHED_STATUSES` from `domain/models.py:66,70-76` with no staleness
  note and no test comparing them. Its docstring at `:8-13` carries a count of
  past scripts (rule 5).
- **Rank:** an earlier draft filed this as baseline hygiene. It gates section
  2's scope work: the score punishes the pipeline for the one thing it does
  right - keeping U.S., ex-U.S. and Worldwide apart - so whoever tunes against
  it is pushed to collapse scopes, which is defect 1a.
- `score()` runs regardless of job status. `eval.py:40` `TERMINAL` and `:112`
  `FINISHED` exist; at `:368` a job not in `FINISHED` only triggers a search for
  a later run, and falls through to the unconditional `score()` at `:393`. The
  mid-reconcile job is real - run13, `Nutrition`, status `running`, six
  `auto_pass` rows for 2023Q1 (139.9 x4, 138.5 x2) and six for 2023Q2 - and
  `score()` returns "published, conflicting" on both, reading 168.1 against a
  want of 164.8.

### 0d. Every eval case runs a configuration no user gets, and a test asserts it is what a user types  `[V]`

    foreign_xbrl.json    7  x {"openfda": false, "product_metadata": false}
    gold_all.json      372  x {same}
    gold_sample.json     8  x {same}
    shapes_holdout.json 24  x {same}
    unseen.json         15  x {same}

    domain/models.py:317  product_metadata: bool = True
    domain/models.py:320  openfda: bool = True

The UI sends `options: {}` and gets the defaults. With `product_metadata=False`
`_extract_metadata` returns at `orchestrator.py:709` and `_judge_profile` is
skipped. Across the 19 `workbench.db` databases in the scratchpad (366 jobs),
the only profile field ever written is `llm_aliases` (280 rows).

**An earlier draft said "layer 2 has never produced a field in any measured
run". That was the filter's absence, not the data's.** Widening the glob from
`*/workbench.db` to every `*.db` finds `jr/meta.db`, written by a reviewer's
probe that called `run_job` directly with both options on: two jobs, Tyvaso and
Opsumit, each holding 12 profile fields - `roa`, `moa`, `fda_approval_date`,
`therapeutic_area`, `indication`, `pharmacologic_class`, `manufacturer`,
`generic_name`, `brand_name`, `active_ingredients`, `cik`, `llm_aliases` - all
`needs_review`. The capability works. The true sentence is narrower: **no run
scored by the eval has ever written a profile field**, because no case file
turns layer 2 on. `llm_aliases` is an internal search-term payload, skipped from
judgment at `quality/profile.py:127`, so the ten empty tables are empty in
measurement rather than in production.

And `backend/tests/test_shapes_holdout_is_held_out.py:134-135`:

    def test_cases_are_what_a_person_would_type():
        assert case["options"]["openfda"] is False
        assert case["options"]["product_metadata"] is False

A passing test pinning the eval to the opposite of the shipped default, and
naming that configuration "what a person would type". Its docstring at
`:130-131` says "nothing here may hand the pipeline a document or a figure" -
a property the body does not enforce (rule 5) - and the assertion makes the
tuned configuration a suite invariant: switching the eval to the shipped
defaults fails `pytest` (rule 4).

**Rank:** layers 2 and 3 are the product. The only number the project reports
measures layer 1 with layer 2 off, so `therapeutic_area`, `moa`,
`competitive_intensity` and `peak_*` - the export columns an analyst acts on -
have never been scored in any run the project cites. This is why sections 3
and 4 were found late, not a baseline nuisance.

A latent rule-3 hole in the same path: `known_source_url` is in `eval.py:317
DRUG_FIELDS`, and the only test forbidding it
(`test_shapes_holdout_is_held_out.py:133`) reads `shapes_holdout.json` alone.
A case file carrying it would hand the pipeline gold's document URL - the
"table of document URLs" failure CLAUDE.md names, arriving through the eval.

### 0e. Four env overrides, all session-only - and nothing measured is reproducible  `[V]`

Filter stated: every `Settings` field in `config.py` compared against
`get_settings()` in the shell that ran the evals. An earlier draft named two;
the count had no stated predicate, and the two it missed are the larger ones.

    SEC_INCLUDE_8K=true               sec_include_8k            False -> True    config.py:31
    ENABLE_PROFILE_JUDGE=false        enable_profile_judge      True  -> False   config.py:49
    SEC_MAX_FILINGS=25                sec_max_filings           4     -> 25      config.py:30
    OPENROUTER_MODEL_EXTRACT=...      openrouter_model_extract  gpt-4o-mini -> gemini-3.8-flash  config.py:25

All four live in the **process environment of the session**, and in no file:
no `.env` exists at `backend/`, `deploy/` or the root, and the two example
files carry the declared defaults. `sec_max_filings` is read at
`sources.py:764`; `openrouter_model_extract` at every extraction call in
`llm/client.py`. No table in any run database has a column naming the model
(checked all 22 in run13), and `options_json` stores `ExtractionOptions` only.

So a re-run from a clean shell fetches 4 filings per job instead of 25 and
extracts with a different model. **Every number in this document, and the
README's 75.6%, belongs to a configuration that exists in no file and in no run
record.** This is the single strongest reason nothing below can be measured -
stronger than 0a - and an earlier draft filed it as a sequencing footnote.

Also load-bearing and unmeasured: `sources.py:10-11` asserts in prose that the
primary 8-K document "is a cover page and holds no figures" - a claim about
document contents with no command, used to justify the `sec_include_8k`
default (rule 2/5), and `:13` "Two rules here were bought with wrong answers"
is history in a docstring (rule 5).

The "14 of 28 documents" figure was a single live repro and is `[I]`. Its ratio
corroborates in run13: of 627 `source_documents`, 340 (54%) are `filing_type
8-K` whose URL is not an EX-99 exhibit - the cover pages `sources.py:5-11` says
in prose hold no figures - running 17-23 of 32-47 per job.

**Sequencing for 0d and 0e, because two switches have been treated as one.**
`openfda` and `product_metadata` cost one API call and write up to eleven
fields (`orchestrator.py:770`) - turn them on with the fixes in section 3, and
unset `sec_include_8k` in the same re-run. The profile judge is separate:
`_judge_profile` (`orchestrator.py:1033`) makes one web-search-backed LLM call
per profile field per job, uncapped (`profile_judge_max_fields: 0`, documented
as "no cap" at `quality/profile.py:137`). With layer 2 off it has nothing to
judge, which is why turning it on today does nothing at all. Turn it on
**after**, and measure it as its own configuration against its own correction
rate.

**Nothing below is scored until 0a-0d are done.**

---

## 1. Three numbers on screen are wrong rather than absent

Ranked first because the analyst has no way to know to distrust them. A blank
costs less than a confident error.

A population note for every count in this section: `run13/workbench.db` holds
**six `run_id`s, 24 jobs, 549 datapoints** - it is a database, not a run.
"run13" totals below are DB-wide unless a run is named.

### 1a. The quarterly chart plots the wrong number when a quarter has more than one scope  `[V]`

`frontend/src/pages/dashboardModel.ts:83` is
`byPeriod[period][point.product] = point.value` - last row wins, arbitrarily.
Run on the real run13 payload:

    AYVAKIT 2024Q4 plotted value: 20

The three published rows are 144.1 Worldwide, 124.1 U.S., 20.0 ex-U.S., all
`auto_pass`, all from the same 8-K exhibit. The chart shows **$20.0M for a
quarter in which worldwide revenue was $144.1M** - a 7x understatement in the
product's central view, with a tooltip that drills to the ex-U.S. row.
Reconciliation is right to keep the three apart (`orchestrator.py:2293` groups
on `(period, _scope_key(revenue_scope), formulation)`); nothing downstream ever
picks one.

4 of 99 published (product, period) groups in run13 carry more than one value
- exact on re-run - and they are two different defects: AYVAKIT's two are a
legitimate scope split (this item); Nutrition's two are same-scope duplicates
from the `running` job (1f). Do not fix them as one thing.

An aggravation not previously recorded: the row the chart drills to carries
the quote *"AYVAKIT achieved $144.1 million, including $124.1 million in the US
and $20.0 million ex-US"* - so the citation panel the analyst opens to check
the $20.0M point leads with $144.1M. The evidence on screen contradicts the
plot on screen.

### 1b. Coverage is a row count, and reads 100% on a series with nothing in it  `[V]`

`quality/completeness.py:90`: `quarters = sum(1 for row in quarters_held ...)`
counts **rows**, not distinct periods, and excludes only `rejected` - so
`needs_review` and `corroborates` rows count as coverage. And `gaps` counts
only `unresolved_quarters` rows, which are written only when the issuer filed
nothing at all in the window (`orchestrator.py:1155`: `if covered: return []`).
An issuer that filed but whose quarters the extractor failed to read produces
zero gaps. So "100%" means "of the quarters we noticed were missing, none are
missing."

    product     completeness_pct   Library card "qtrs"   published quarterly
    AGAMREE          100.0                 5                    5
    ILUVIEN          100.0                 1                    0
    ORLADEYO          47.6                10                    8   <- longest clean run in the DB

(An earlier draft printed only the last column and labelled it as the card;
the card shows the middle one, because of the second defect below.)

ILUVIEN is the whole item in one product: 100% coverage from 14 quarterly rows
that are every one `needs_review` or `corroborates`, and the "1 published qtr"
on its card is an **annual 2025** figure. Row-versus-distinct-period inflation
affects 19 of 24 jobs (NUPLAZID 33 rows over 10 periods; Pombiliti 22 over 6).

The number is anti-correlated with the thing it names. It is on the Library
card (`LibraryPage.tsx:258`), the API (`main.py:271,323`) and the product
export sheet. The page headline averages it to "Coverage 65%" for run13 -
reproduced exactly as `Math.round` over 22 Library rows.

Compounding: `_published_quarters` (`api/products.py:136`) counts distinct
published `period` of **any** `period_type`, so an annual `2025` or a
`2025Q4`-labelled annual counts as a quarter. It over-reports for 5 of 22
Library products (AYVAKIT 3/2, ILUVIEN 1/0, NUPLAZID 10/9, Ocaliva 5/2,
ORLADEYO 10/8).

### 1c. A two-product line is published as one product's revenue, because the alias step disarms the guard

`parsing/labels.py:210 read_label` detects combined lines correctly.
`orchestrator.py:535` feeds it the LLM's merged alias list, which contains the
*other* product. Decisive:

    read_label("Total Pombiliti (R) + Opfolda (R) sales", <run13's merged alias list>)
      -> matched: Pombiliti  combined: ()           flags: ()
    read_label(same, [a for a in aliases if "opfolda" not in a.lower()])
      -> matched: Pombiliti  combined: ('Opfolda',) flags: ('combined_line',)

So `combined_with` is empty, `reported_as_for` (`orchestrator.py:241`) returns
`None`, and `_record_quarters_only_reported_with_another_product`
(`orchestrator.py:1193`) never fires. All 39 Pombiliti datapoints in run13 come
from `Pombiliti + Opfolda` lines; two are `auto_pass` and shipped with
`reported_as = None`.

ILUVIEN works **only because** YUTIQ happens not to be in its alias list. So
`reported_as_for` and the unresolved-quarter recording are correct code that
does not fire for the case they were built for.

Fix: the alias list a label reader is given must not contain another product's
name. Whether that belongs in `merge_aliases` (`llm/aliases.py:8`), in the
prompt, or in `read_label` is open - the miss is established, the site is not.

### 1d. The deliverable named `quarterly_revenue.csv` is not quarterly  `[V]`

`export/builder.py:267-295` emits `for d in j.datapoints` - every datapoint, no
status filter, no period-type filter - into eight columns with **no
`period_type`**:

    drug_name, period, value_normalized_usd_millions, source_url,
    source_quote, confidence_score, validation_status, revenue_scope

Built the real files with `ExportBuilder.export_powerbi_csvs` for each of
run13's six runs: 49 / 109 / 35 / 163 / 21 / 172 rows. Their union is the 549
datapoints in the database - **no exported file has 549 rows**; the largest
real deliverable has 172. Over the union, 31.3% are not quarterly (74 ytd, 44
annual, 41 nine_month, 12 six_month, 1 unknown), every figure exact on re-run.

**Five** (drug, period) keys carry a quarterly *and* a cumulative figure under
one period string - an earlier draft said "20+", which was the 21 keys carrying
more than one `period_type`; 16 of those are cumulative-vs-cumulative under an
annual string (`AGAMREE 2024 -> annual 46.0 / nine_month 24.966 / ytd 9.92`),
which does not corrupt a quarterly curve the same way. The five that do:

    FYCOMPA   2023Q2 -> quarterly [34.579, 34.6]           six_month  [62.4]
    FYCOMPA   2023Q3 -> quarterly [36.393, 36.4]           nine_month [98.8]
    ILUVIEN   2025Q4 -> quarterly [19.8, 19.843]           annual     [74.9]
    Pombiliti 2024Q3 -> quarterly [8.859, 12.277, 21.136]  nine_month [48.032]
    Pombiliti 2025Q3 -> quarterly [13.07, ..., 30.714]     nine_month [77.535]

and one worse than any of them: `AGAMREE 2024Q3 -> quarterly [15.0, 15.046]
AND unknown [25.0]`.

Anyone who loads this into Power BI and charts by `period` builds a curve
partly from cumulative figures. Also unfiltered by status: 355 `needs_review`
and 67 `corroborates` rows sit beside the 127 `auto_pass` ones.

(The per-job Excel workbook is the one genuinely traceable export -
`builder.py:121-240`, five sheets including a `Drug Profile` with a
`source_url` per field. The Power BI CSVs are the problem.)

### 1e. The product sheet cites a revenue filing beside characterisation fields  `[I]` - zero instances on run data

`export/builder.py:44-71` gives the product sheet one `source_url`, filled at
`:108` from `product.get("source_link")`, which `dashboard/series.py:193`
defines as *the first revenue datapoint's* URL. `therapeutic_area`, `moa`,
`pharmacologic_class`, `roa`, `approved_lot` come from openFDA and carry no
citation of their own.

So the README's "citations are mandatory on every source-derived field" holds
for layer 1 and not for layer 2, and the citation the analyst *does* see points
at a document that does not contain the field. Upstream the stored
`source_quote` is the literal string `f"openfda.{field}"` - a JSON path, not a
quote - at `orchestrator.py:794`, and again at `:931-932` where both
`source_section` and `source_quote` get it.

**The code sites are real and the defect has never happened.** Over run8, 10,
12 and 13, every profile field is `llm_aliases` with an `llm_search` citation,
`CanonicalProductORM` has 0 rows, and all 24 `products.csv` rows carry five
blank characterisation columns beside a revenue-filing `source_url` (6 of 7 on
run13's largest run, each equal to the first revenue datapoint's URL). That is
still wrong - a different wrong - and 1e cannot be scored on run data until
layer 2 writes something. It follows M7.

### 1f. Dashboard tiles and the Methodology tab assert machinery that never runs  `[V]`

- `dashboard/series.py:264` sums over `peak_products`, always empty, and
  returns `value: 0`; `DashboardPage.tsx:155` renders **"Aggregate selected
  peak $0M, Coverage 0/22"**. A confident zero where "not computed" is true.
- `DashboardPage.tsx:166-167` state "Launch uptake is a rolling-four-quarter
  revenue proxy divided by the typed selected annual peak" and "Competitive
  intensity uses the stored competitive_intensity_v1 peer cohort". Both
  describe code with no caller. It reads as "your data is thin" rather than
  "this was never built".
- The Launch-relative tab renders an empty chart with a 22-product legend:
  `buildChartData(payload, products, 'launch')` returns 0 rows but `names` is
  derived from `products` (`DashboardPage.tsx:58`), never from `chartData`, so
  the `names.length === 0` guard cannot fire. Structural, not data-dependent;
  `launch` and `launch24` both return 0 rows on run13.
- **32.3%** of published datapoints in run13 come from jobs that never finished
  - filter: the 127 `auto_pass`/`confirmed` rows by `job.status`: 86
  `ready_for_review`, 34 `running`, 7 `failed`. Narrowed to `running` alone it
  is 26.8%; the other 7 are from four **failed** jobs, which is the more
  surprising half. `build_dashboard_preview` (`dashboard/series.py:67`) queries
  every job regardless of status. The `running` job (Nutrition, mid
  `reconcile_conflicts`) holds the only true duplicates in the database -
  same product, period and scope, two values, all `auto_pass`.

All four tiles read the same in run8, run10 and run12 (`peakcov=0/N`,
`peak=$0M`, `launch_series=0`) - not a run13 accident. The other counts in
this section grow with corpus size (multi-value groups 0 -> 0 -> 2 -> 4;
published-from-unfinished 0% -> 0% -> 19% -> 32%), so they are floors to be
re-measured at fix time, not quoted.

---

## 2. What layer 1 produces is not a series

Individually correct figures, unfit to fit a curve to. This section is why
fixing section 5 alone would not deliver the product.

### 2a. There is no selected figure per quarter

run13: **377 quarterly rows over 157 distinct (product, period) cells** - 2.4
readings per cell, up to 6. `quality_checks` records this 115 times as
`duplicate_period_scope_formulation`, and all 135 checks are `status='open'`,
across 17 of 20 products. Detected, never resolved. `export/builder.py:131`
emits `for d in job.datapoints:` with no selection column and no ordering; the
chart is last-row-wins (1a).

The cost is not cosmetic. `calculate_revenue_uptake` slices
`rows[index-3:index+1]` - four **rows**, not four **quarters**. Nutrition has a
perfect unbroken 10-quarter single-scope published series and produces **0
uptake points**, because 34 rows over 10 quarters never give a window of four
consecutive distinct quarters.

Re-running layer 3's real functions over run13 with a peak and launch date
handed in free:

    | products in run13                     | 24 jobs |
    | with any published quarterly row      | 14      |
    | with >=4 consecutive published qtrs   | 10      |
    | yielding >=1 uptake point             |  9      |
    | yielding >=4 uptake points            |  5      |
    | with a peak select_peak_estimate picks|  0      |

    after dedup + a normalized vocabulary:  >=1 point: 10, >=4 points: 6

Deduplication alone is worth more here than any extraction improvement.

### 2b. The scope label is noise, not a scope

18 distinct `geography` strings for about five concepts, 63% null:

    None 236, 'United States' 61, 'Worldwide' 26, 'U.S.' 18, 'Rest of world' 8,
    'US' 5, 'Outside of U.S.' 4, 'North America' 4, 'ex-US' 3, 'Rest of World' 2,
    'Ex-US' 2, 'Ex-U.S.' 2, 'global' 1, 'U.S.; Rest of world' 1,
    'U.S. and Europe' 1, 'Japan' 1, 'Global' 1, 'Europe' 1

`_has_compatible_scope` (`uptake.py:38`) compares by exact string equality, so
a product whose U.S. track is spelled `'U.S.'` in one quarter and `'US'` in the
next is rejected as scope-incompatible **on spelling alone**.

Worse, the same number carries contradictory labels within one quarter:
ELEVIDYS 2024Q2, value 121.721, appears as `(Product family, None)`,
`(Worldwide, United States)`, `(Worldwide, Worldwide)` and `(U.S., United
States)`. ORLADEYO's 30 quarterly rows carry 11 distinct
(scope, geography, formulation) tuples - the decomposition and the total,
unmarked, in one list.

Gold's whole corpus uses three geography values, and 0 of its 55
`benchmark_identity` groups contain more than one
(scope, geography, formulation, currency, period_basis) tuple.

### 2c. Nothing checks comparability across products

`analytics/analog_matching.py` scores on `moa_class`,
`route_of_administration`, `approval_era`, `competitive_intensity_at_launch`.
`ProductProfile` (`:38-44`) has **no revenue-basis field at all**. Nothing asks
whether the target's curve is worldwide and the analog's is U.S.-only.

Live consequence: **YUTIQ and ILUVIEN ship identical curves**, quarter for
quarter, because every YUTIQ figure is read from an `ILUVIEN and YUTIQ` row.
Two library entries, one quantity, each eligible to be the other's top-ranked
analog. Same shape for Pombiliti (1c).

Gold's answer is `benchmark_identity` - `uthr_tyvaso_nebulized_reported`,
`merck_adempas_merck_territories_reported` - issuer, brand, geography and basis
in one machine-checkable key, one per series, riding on every quarterly row and
on `series_coverage.jsonl`. The shape of the fix already exists in the repo.

### 2d. The holes land in the ramp, not the tail

Distinct (product, quarter) cells in run13 by quarter-of-year:

    Q1: 48   Q2: 47   Q3: 38   Q4: 24
    Q1 by method: llm 82, xbrl_fact 21, table 18, prose 6, derived 1
    Q4 by method: derived_from_period_total 15, prose 11, llm 6, table 4, xbrl_fact 0

Q4 has zero XBRL because no issuer files a Q4 10-Q - it exists only as FY minus
nine months. The pipeline knows this (`extraction/derive.py:225`) and reaches
it for about half the Q4s it needs; 16 of 35 `unresolved_quarters` rows are
Q4s. For a rolling-four-quarter metric an interior Q4 hole destroys four
consecutive uptake points, so it is not a tail hole.

The second class is worse: **the launch quarter**. DAYBUE launched April 2023;
its exact 2023Q2 XBRL fact (23.217) is present in run10, run12 and run13, and
in run13 is demoted to `corroborates` - a status `PUBLISHED_STATUSES`
(`domain/models.py:65`) excludes - because it corroborates an LLM reading that
then failed review. 10 of 157 quarter-cells are unpublished while holding a
`corroborates` row; 5 of those corroborators are XBRL, table or derived
(NUPLAZID 2023Q1, FIRDAPSE 2023Q2, DAYBUE 2023Q2, FILSPARI 2025Q2, YUTIQ
2025Q2). See 6e - this is the same defect as 3e in the first pass, and it costs
series starts.

### 2e. Life events are detected and then dropped

`reported_as` is written on 12 of 549 rows (2%), covering 5 of YUTIQ's 8
quarters and none of ILUVIEN's. It is then discarded on the way out:
`QUARTERLY_HEADERS` (`export/builder.py:18`) has no `reported_as` column, and
the dashboard series payload (`dashboard/series.py:223`) carries no
`revenue_scope`, no `geography`, no `formulation` and no `reported_as`. Only
Product Detail surfaces it (`api/products.py:378`,
`ProductDetailPage.tsx:275`), where it renders well - one tab of one page.

Unmarked life events in run13:

- **A pre-launch expense published as revenue.** AGAMREE opens 2023Q3 = 81.5
  (`auto_pass`, quote: "the $81.5 million IPR&D purchase consideration for the
  acquisition of the license") and 2023Q4 = 36.0 ("we paid a regulatory
  milestone"), then 2024Q1 = 1.174. The curve reads as a collapse then a ramp.
- **A stub launch quarter.** That 2024Q1 quote says "net sales were
  approximately $1.2 million for the period between March 13, 2024 (date of
  commercial launch) and March 31" - a 19-day quarter plotted as a full one.
- **A restatement.** Nutrition 2023Q1 is published as both 138.5 and 139.9;
  2023Q2 as 164.8 and 168.1. Perrigo's own quote gives FY2023 = 563.2 =
  139.9+164.8+130.7+127.8. Take the other published Q1 and the year totals
  561.8, which the issuer never printed.
- **A segment posing as a product.** `Nutrition [PRGO]` is Perrigo's
  infant-formula reporting segment, carried through the whole pipeline as a
  drug and the highest-yield "product" in run13 by datapoints (34). It is an
  eval input, not a user's - but nothing guards the path from a name in a CSV
  to a published product revenue series.

Gold's contrast is worth copying, and it is two fields rather than a policy.
`series_coverage.jsonl` carries **`launch_quarter`** (30 of 55 records) and
**`commercial_start_quarter`** (55 of 55) as separate things, and they differ
for 24 of the 30 that have both:

    Adempas    launch 2013Q4  series starts 2024Q1
    Tracleer   launch 2001Q4  series starts 2016Q1
    Procrit    launch 1989Q2  series starts 2005Q1
    Letairis   launch 2007Q2  series starts 2008Q1

So gold never starts a series at the launch quarter by default and never
publishes a stub: the launch date anchors the x-axis, and a separate, declared
quarter says where usable data begins. The pipeline has neither field - and per
3g it does not have the launch date either.

`series_end_reason` on 26 of 55 records names the event and the evidence, and
the README states the principle: both boundaries are declared "rather than
silently applied, because a reader who mistakes either one gets a wrong answer
from a right-looking series."

Gold also carries a per-row **`precision`** (`as_reported` 2,128, `exact` 70,
`approximate` 5) and a **`derivation`** on every row (`direct_reported` 2,038,
`full_year_less_other_reported_quarters` 44,
`annual_less_reported_first_nine_months` 12, `acquisition_bridge_sum` 3, and
five more). The pipeline has `extraction_method`, which says which reader ran,
not what the number is.

And gold's six exclusions name the failure class rather than leaving a thin
series: `individual_reporting_discontinued`, `incomplete_pre_peak_history`,
`no_standalone_product_sales`, `private_issuer_no_public_sales`. One of them
anticipates AGAMREE exactly - Tadliq's reason reads "Approval, availability,
price, and pharmacy listings are not product revenue."

### 2f. The IR sources gold cites are on EDGAR; one class of filing is not

Gold cites 907 non-SEC rows (41%) and 643 PDF rows (29%). They are investor-
relations material, and they concentrate in two issuers:

    Johnson & Johnson  770 rows   579 from s203.q4cdn.com (J&J's IR CDN)
    Gilead             609 rows   263 from www.gilead.com (press releases)
    Eli Lilly          374 rows   374 SEC
    United Therapeutics 368 rows  365 SEC

J&J's are the quarterly `Sales-of-Key-Products-Franchises-<Q><YYYY>.pdf`;
Gilead's are quarterly earnings press-release pages.

**That count does not measure a retrieval gap.** The same content is on EDGAR,
as EX-99 on the earnings 8-K, and `is_earnings_exhibit` (`sources.py:163`)
already matches the naming. Verified against the live API:

    J&J 1Q2020   8-K 0000200406-20-000026  items 2.02,9.01
                 a2020q1exhibit992.htm     -> OPSUMIT, UPTRAVI, TRACLEER,
                                              STELARA, DARZALEX, XARELTO
    Gilead 2Q18  8-K 0000882095-18-000019  items 2.02,9.01
                 exhibit991earningspressrel.htm -> HARVONI, EPCLUSA, BIKTARVY

So gold cited the IR copy because it is the readable one, not because EDGAR
lacked it, and **no investor-relations fallback is needed for the bulk of it**.
The existing SEC path reaches both issuers today.

**The Actelion bridge is genuinely IR-only, and it is small.** The 8-K/A that
CLAUDE.md rule 1's table names is real and reachable, but it is not the source
of the quarters:

    8-K/A 0000200406-17-000046  filed 2017-08-29  items 2.01,9.01
      exhibit991actelionfinancia.htm  (1.2 MB, matched by is_earnings_exhibit)
      -> names OPSUMIT, UPTRAVI, TRACLEER, VELETRI, VENTAVIS in narrative only
      -> "IN CHF THOUSANDS": PRODUCT SALES 2,412,198 as a single total line
      -> no per-product figures, no quarterly columns (0 "six months",
         0 "June 30"), 137 mentions of CHF, 0 of a USD product table

So the rule-1 table is right that the 8-K/A holds "the Actelion financials" -
it holds the *company's* statements. Product-level quarterly revenue is not in
them. Gold's Opsumit, Uptravi and Tracleer quarters come from
`Actelion_Historical_Sales_Schedule.pdf` on J&J's IR CDN, which has no EDGAR
equivalent, and gold still declares 2013Q4-2015Q4 unavailable because even that
schedule reaches back only to 2016Q1.

That is 58 rows of gold (every `Actelion/J&J` row), not 907. A narrow
investor-relations fallback is justified for the acquired-product bridge; it is
not justified by the 41% figure, which is J&J and Gilead choosing the readable
copy of documents that are on EDGAR.

**The form and item filters are still worth fixing** (9c) - a filter is part of
the claim, and `EARNINGS_ITEM = "2.02"` written as a literal is rule 1's shape.
But neither is now known to unlock a quarter, and this document should not
claim one until a filing that carries product-level periods has been opened and
shown to.

---

## 3. Layer 2 emits attributes that are wrong, not missing

Layer 3 scores on four attributes. Layer 2 produces none of them under those
names, produces a substitute for one that is wrong for the products where route
is the whole story, and produces an approval date that is absent or wrong for 7
of the 20 seed products.

These are live-path defects: the defaults are on, and they were measured by
running layer 2's own functions against the live API, not by reading a run.

**openFDA is the right source; every defect below is in how it is read.**
Each one is a wrong field path, a wrong match or a dropped value - not a reason
to distrust the registry. Fixing them is cheap and the data is already there.

Gold did this job by hand rather than automating it:
`scripts/build_independent_gold.py` never calls openFDA, drugsFDA or DailyMed
(`grep -in "openfda|dailymed|drugsfda|api.fda.gov"` returns nothing), and
`read_product_attributes()` (`:2620`) takes `first_approval_year`, `moa_class`,
`route_of_administration` and `indication_area` from the curated
`seed/product_attributes.csv`. So gold never met 3d's sibling match or 3e's
application ambiguity - it never resolved an application.

That is an opportunity, not a caution. **`seed/gold/product_profiles.jsonl` is
a ready-made oracle for exactly what layer 2 should produce** - 62 products,
each carrying `route_of_administration`, `first_approval_year`, `moa_class`,
`indication_area`, `approval_era` and `competitive_intensity_at_launch`, and
declaring `attribute_provenance: curated_reference`. It is currently scored by
nothing. Scoring the openFDA path against it is the right direction under rule
3 - reference data flows into gold, and gold scores the pipeline - and it makes
3a-3h measurable rather than anecdotal. What the pipeline must not do is *read*
`seed/product_attributes.csv`, because that file is what builds this oracle.

A first score already exists and is in 3a: over the 20 seed products,
`openfda.route` differs from the curated value for 8.

### 3a. Route is read from a field that contradicts the same document

`parsing/fda_label.py:68` reads `openfda.route`. The same drugsFDA record
carries `products[].route`, and they disagree. Verified live:

    NDA022387  openfda.route=['ORAL']  products[].route=['INHALATION']  TYVASO
    NDA214324  openfda.route=['ORAL']  products[].route=['INHALATION']  TYVASO DPI

Across the 20 seed products `openfda.route` disagrees with `products[].route`
for 6 and differs from `seed/product_attributes.csv` for 8. `config.py:46`
already knows - "openFDA gives an inhaled product's route as ORAL" - which is
why `enable_profile_judge` exists, and the judge left the value unchanged.

Measured cost, feeding layer 2's real output into `rank_analogs`:

    target Tyvaso
      from pipeline attributes : Adcirca 1.0 (2 attrs), Letairis 1.0 (2), ...
      from curated attributes  : Nebulized Tyvaso 1.0 (4), Tyvaso DPI 0.83 (4), ...

The first inhaled prostacyclin gets two oral small molecules as its top
analogs, **at score 1.0**. `analog_matching.py:132`'s `minimum_attributes=2`
defends against exactly this, and 2 is precisely what layer 2 can supply, so
the guard never fires.

### 3b. `openfda.dosage_form` does not exist

Verified: the key is absent from the `openfda` block on both datasets
(`openfda` keys are `application_number, brand_name, generic_name,
manufacturer_name, nui, package_ndc, pharm_class_epc, pharm_class_moa,
product_ndc, product_type, route, rxcui, spl_id, spl_set_id, substance_name,
unii`). `parsing/fda_label.py:69` reads it anyway, so `df=None` for all 20 seed
probes including exact matches. It lives at `products[].dosage_form`.

Downstream: `identity/resolver.py:35` hashes `dosage_form` into `identity_key`,
so it is always the empty component; `orchestrator.py:866` writes
`dosage_form="unresolved"` on every `ProductFormulationORM`; and `dosage_form`
is #2 in `PRIORITY_JUDGE_FIELDS` (`quality/profile.py:117`), spending a web
search on a value that is never there.

### 3c. A whole class of products is invisible, including the oldest analogs

`connectors/openfda.py:27` searches `openfda.brand_name:` only, and
`openfda_fields.py:16` reads only `result["openfda"]["brand_name"]`. For older
and discontinued products drugsFDA returns the application with an **empty
`openfda` block** and the brand in `products[].brand_name`. Verified live:

    openfda.brand_name:"Flolan"    -> HTTP 404
    products.brand_name:"FLOLAN"   -> NDA020444, route INJECTION, dosage INJECTABLE
    products.brand_name:"VENTAVIS" -> NDA021779, route INHALATION

`connectors/openfda.py:58` logs `openfda_no_match` on the 404 and moves on. The
data is in the same endpoint, one field path over. This costs 3 of 20 seed
products and 4 of 21 run13 jobs - among them **Flolan (1995), the first PAH
product and the only one in the catalog with a complete 30-year ramp**, which
is the single most valuable analog an uptake workbench could hold. (Elevidys
and Vyjuvek are CBER gene therapies and are not in drugsFDA under any field;
for those there is no structured path at all.)

This is CLAUDE.md rule 1's shape: a field path written down rather than derived
from the document.

### 3d. The substring fallback attaches a sibling's application and its approval date

`connectors/openfda_fields.py:58`: after exact brand match fails,
`brand_norm in candidate or candidate in brand_norm`. This is the match
`extraction/members.py:10-14` explicitly refuses to make for revenue.

    Thiola      aliases ['THIOLA','THIOLA EC','TIOPRONIN'] -> THIOLA EC  NDA211843  2019-06-28
    Nucynta ER  aliases ['NUCYNTA ER','TAPENTADOL']        -> TAPENTADOL NDA200533  2011-08-25

Thiola is NDA019569 (1988): the job is characterised from a delayed-release
line extension with an approval date **31 years late**.

Two compounding mechanisms. The docstring at `openfda_fields.py:31` says "the
generic name is deliberately excluded from matching"; the exclusion at `:42`
drops only an alias string-**equal** to `job.generic_name`, and every stored
`llm_aliases` set spells the molecule differently ("tapentadol" vs "tapentadol
extended-release"), so the molecule name survives and the exclusion is dead.
And `quality/profile.py:79 blends_sibling_brand` exists for this and is applied
only to the LLM branch (`orchestrator.py:985`); the openFDA mapping loop
(`orchestrator.py:786-815`) has no sibling check.

### 3e. Among a brand's own applications, the first returned wins

    UPTRAVI NDA214275 route=['INTRAVENOUS'] ORIG AP 20210729   <- selected
    UPTRAVI NDA207947 route=['ORAL']        ORIG AP 20151221   <- the product

`earliest_approval_date([selected])` (`orchestrator.py:784`) is scoped to the
one selected application, and its docstring defends that scoping - the effect
is that a later line extension becomes the product's approval. Uptravi lands in
the 2020-2024 era bucket, intravenous: wrong on both scored attributes.
Approval date across the 20 seed products vs the curated reference: 3 absent, 4
differ (Uptravi 2021 vs 2015, Veletri 2008 vs 2010, Revatio 2012 vs 2005, Alyq
2022 vs 2018).

openFDA result order is not guaranteed, so this one is order-dependent and may
present differently on another day; the field-path findings above are not.

### 3f. `fda_approval_date` is always empty, exactly when resolution succeeds

`dashboard/series.py:134`:
`approval_date = canonical.initial_approval_date if canonical else fields.get("fda_approval_date")`.

`initial_approval_date` is read at `dashboard/series.py:134` and
`api/products.py:343` and is **never assigned anywhere in `app/`**. So having a
canonical product row *skips* the working fallback. A live run holds
`Tyvaso fda_approval_date = 2009-07-30` and `Opsumit = 2013-10-18` in
`drug_profile_fields`, both cited to
`submissions[type=ORIG].submission_status_date`, and exports `None` for both -
along with `approval_period`, one of the Dashboard's own analog filters
(`dashboardModel.ts:6`), so that dropdown is permanently empty.

### 3g. There is no launch anchor in the database at all

`orchestrator.py:784` computes `approval` per source; `:908-914` writes
`ProductIndicationORM.approval_date` and `launch_anchor_type` from that local.
Two openFDA sources are retrieved per job, and they are not the same record:

    drugsfda.json?...brand_name:"Tyvaso"  -> approval 2009-07-30, indications_text False
    label.json?...application_number:...  -> approval None,       indications_text True

`parsed_indications` is non-empty only for the `label.json` record, where
`approval` is `None`. So every indication row is written with
`approval_date=NULL, launch_anchor_type=NULL`.

`months_since_launch` is the x-axis of "Launch-relative" and "First 24 months"
and the input to `time_to_ninety_percent_peak`. Even with layer 3 wired
tomorrow there is nothing to anchor a curve on.

### 3h. `therapeutic_area` is a verbatim copy of `indication`, so nothing groups

`orchestrator.py:779`: `"indication": indication_value, "therapeutic_area":
indication_value`. Running the real parser on real labels:

    NDA021290 : 'pulmonary arterial hypertension (PAH) (WHO Group 1)'
    NDA204410 : 'pulmonary arterial hypertension (PAH, WHO Group I)'
    NDA207947 : 'pulmonary arterial hypertension'
    NDA022387 : 'pulmonary arterial hypertension (PAH; WHO Group 1); pulmonary
                 hypertension associated with interstitial lung disease (...)'

Four spellings of one indication universe. Both grouping functions use exact
equality - `competitive_intensity_llm.py:190` and
`competitive_intensity.py:77` - so on pipeline output every product is its own
universe of one, i.e. "the first approval in the indication", i.e. intensity
`low` for everyone. Gold's README names this failure: "a number with the shape
of a measurement and none of the meaning."

### 3i. `moa_class`, `approval_era` and `competitive_intensity_at_launch` have no producer

`grep -rn "moa_class" backend/app/` finds nothing outside `analytics/` and one
prompt template slot; `approval_era` appears only in `analog_matching.py`. The
LLM metadata prompt's vocabulary
(`app/prompts/metadata_extractor.yaml`) is `generic_name|manufacturer|
fda_approval_date|therapeutic_area|indication|moa|pharmacologic_class|roa|
dosage_form|formulation|ticker|cik` - no class, no era, no intensity.

So of the four weighted attributes at most two are ever comparable, and `moa`
(weight 1.0, the heaviest) is free text inconsistent in **kind**: where
drugsFDA carries `pharm_class_moa` it is a class term; where it does not, the
label's prose is used. In the 20-product probe `moa` came back `None` for 10.
The Library also shows `moa=None` for Tyvaso although the profile holds the
prose value, because `_moa_text` reads `MoAComponentORM`, which openFDA's
`moa_terms` left empty - another handoff where having the canonical row yields
*less* than not having it.

`dashboard/series.py:29 _approval_period` buckets to `f"{start}-{start+4}"`,
the same shape as gold's `approval_era`, and is computed for display only.

**Note for rule 3.** `seed/product_attributes.csv` holds `moa_class`,
`route_of_administration`, `first_approval_year`, `indication_area` and
`peer_universe_role`, and `extraction/members.py:273 load_products()` already
opens that file and takes `drug_name` only. But
`scripts/build_independent_gold.py:2620` builds `seed/gold/product_profiles.jsonl`
from those same columns - so if the pipeline read them, gold would stop being
able to score characterisation at all. What layer 2 needs is a **procedure**
that derives a class and an era; the CSV could at most be a cache in front of
it, and would have to pass the delete test.

---

## 4. Layer 3's method is unsound independently of the wiring

The unwiredness is declared - `tests/test_capabilities_are_wired.py:43` lists
the analytics layer under `SCRIPT_ONLY` as "open work", and 13 of the product
sheet's 28 columns are structurally always null. What is not declared is that
the method is wrong in four places, three of which produce a confident number
rather than a refusal.

**So: do not wire this layer to fix the blank columns.** A blank column costs
the analyst less than a confident wrong one.

### 4a. Competitive intensity is a rank within the batch, not a property of the market

`analytics/competitive_intensity.py:133-152`. For a cohort of >=6,
`categorize_snapshots` sorts by `raw_score` and assigns low/medium/high by
**percentile position**, tie-broken on `indication_id`.

    six snapshots, identical peer rosters, identical raw score 3.0:
      same0 pct=0   -> low     same3 pct=60  -> medium
      same1 pct=20  -> low     same4 pct=80  -> high
      same2 pct=40  -> medium  same5 pct=100 -> high

Six products with the same score and the same rivals, split alphabetically
across three bands. Six each launching into a completely empty market return
two `high`; six each facing eight direct rivals return two `low`.

    agreement with gold's marketed_peer_count_at_launch_v1: 9/20
      Adcirca peers=6 gold=high app=low      Tyvaso peers=6 gold=high app=medium

Gold bands an absolute count (0-1 low, 2-4 medium, 5+ high) and gets 15 high /
3 medium / 2 low, monotone in launch year - the true story of PAH becoming
crowded over thirty years. A percentile method is structurally forced to
~1/3 each and can never produce that distribution.

Three aggravations: `low_coverage` is set on the **wrong branch** - `True` for
cohorts < 6, the absolutely-banded and defensible path, and `False` at `:150`
exactly when the label is rank-derived; the band flips discontinuously at
cohort size 6; and zero peers yields `raw_score=0.0 -> "low"`, so "we found no
competitor registry entries" and "this drug launched into an empty market" are
the same output. Gold gates that case with
`not_assessed_outside_catalog_universe` on 42 of 62 rows. **No equivalent gate
exists in the code** - `calculate_competitive_snapshot` will band anything.

The column is exported (`export/builder.py:57,95`), offered as a dashboard
filter (`dashboard/series.py:243`), and is a 0.75-weighted similarity input.
And `tests/test_competitive_intensity.py:49` **asserts** the forced
distribution and calls it "stable percentile categories" - a test that passes
precisely when the label is meaningless.

### 4b. The observed peak can be confidently wrong in three shapes and silently absent in a fourth

`analytics/peak_sales.py:130-147`.

- **A series beginning after the real peak gets an "observed" peak anyway.**
  Three annual rows 900/700/500 return
  `SelectedPeak(estimate_type='observed', value=900,
  selection_method='mature_observed_annual_peak_v1')`. Gold guards this with
  `peak_eligible: false` and emits no peak row; there is no `peak_eligible`, no
  launch-anchor check and no coverage assertion in `peak_sales.py`.
- **A five-year hole is invisible.** Rows for 2010, 2011, 2018, 2019 return
  2011's value, "confirmed" by two years seven and eight years later.
- **A year counted twice confirms its own peak.** `aggregate_comparable_sales`
  emits an `AnnualSales` for an annual observation (`:87`) *and* one for the
  four quarterly observations of the same year (`:115`), with no dedup -
  issuers routinely publish both. A product with one year after its maximum
  returns `observed` where gold's rule
  (`independent_max_with_two_later_lower_years`) gives `not_yet_observed`.
- **More data produces no answer.** `_mature_observed_peak:133` requires a
  single `(currency, geography, revenue_scope, period_basis, formulation_scope)`
  key across **all** annual rows, so a product with both a U.S. and a Worldwide
  line returns `None` with no reason. Partition by scope and pick the
  best-supported one; the code gives up instead.

Also: `peak_sales.py:113` gates the quarterly roll-up on four **distinct**
periods then sums **all** rows at `:118`, so five observations covering four
quarters yield a 25% overstatement. The `consensus` branch enforces a 365-day
recency window and the `modeled` branch enforces none. And
`imports/peak_sales.py:44` accepts `estimate_type == "observed"` from a CSV
which `select_peak_estimate` never reads.

### 4c. Similarity's stated defence against unknown attributes is inverted

`analytics/analog_matching.py:8-15` says scoring `None` as similar "would
quietly promote products we know least about", so unknowns are dropped from the
denominator. Dropping them from the denominator promotes those products too,
and less visibly - a candidate cannot lose credit on an attribute it never
declared. Over the 62 gold profiles:

    TARGET Revatio    Cialis   0.8889 n=3 unknown=[intensity]  <- #1
                      Adcirca  0.8750 n=4 unknown=[]  (moa, route AND era match)
    TARGET Winrevair  Mounjaro 0.5556 n=3 unknown=[intensity]  <- #1
                      Liqrev   0.4167 n=4 unknown=[]

Revatio's best analog is an erectile-dysfunction drug; Winrevair's is a
mass-market GLP-1. Both win by not knowing something. With the profiles the app
can actually produce (section 3), a candidate known only by route and era, both
matching, scores **1.0** and outranks the genuine four-attribute match.

`minimum_attributes: int = 2` is documented as "what stops a single coincidental
agreement from ranking above a genuine four-attribute match" - two coincidental
agreements out of two known is still coincidental, and the sort key is
`(-score, -attributes_compared, ...)` with score primary, so depth never
rescues it.

**`indication_area` is in the data and is not scored.** Both
`seed/product_attributes.csv` and `seed/gold/product_profiles.jsonl` carry it;
`ProductProfile` has no such field. Nothing in the similarity model knows what
disease a product treats - the first thing any analyst filters on.

Smaller: `rank_analogs:142` silently drops candidates below
`minimum_attributes`, so the analyst never learns one was considered;
`_era_start("Pre-2000")` returns 2000, so an unbounded pre-1999 bucket earns
the same half-credit against `2000-2004` whether the approval was 1998 or 1982;
and `_similarity` compares with `==` case-sensitively, so `ORAL != Oral` scores
a confident **0.0**, not an unknown - fixing route correctness without
normalisation buys nothing.

### 4d. Uptake divides by a number whose scope it never checks

`analytics/uptake.py:61,110` take `selected_peak: float` - currency, geography
and revenue scope discarded at the call boundary. U.S. quarters over a
worldwide peak of 1000 return a clean `[('2020Q4', 0.24), ('2021Q1', 0.24),
...]`. "24% of peak" reads as early ramp; it is a scope mismatch.
`_has_compatible_scope` polices the numerator window rigorously and nothing
polices numerator against denominator.

`missing_reason` can be false: precedence at `:79-88` puts
`insufficient_history` and `nonconsecutive_quarters` ahead of
`incompatible_sales_scope`, so a product reporting both a U.S. and a Worldwide
line every quarter produces an all-null curve labelled `nonconsecutive_quarters`
when the quarters are perfectly consecutive - sending the analyst to look for
filings that already exist.

`time_to_ninety_percent_peak` (`:107-141`) applies **no scope filter at all** -
not `ALLOWED_PRODUCT_SCOPES`, not currency, not geography; a `Franchise`-scope
row satisfies the threshold. It admits rows where `period_end is None` and both
sorts key on `(row.period_end or date.min, ...)`, so an undated row sorts first
and is returned as the *earliest* period to reach 90%. It returns a
`SalesObservation`, not a time - `launch_date` is only a filter and no interval
is computed. And when the peak is a `consensus` or `modeled` estimate no
observation may ever cross the threshold, returning `None`, indistinguishable
from "not yet"; `SelectedPeak.estimate_type` is not passed in, so the function
cannot know.

Structurally, the trailing-four-quarter metric means the first three quarters
of every series are always `insufficient_history` - the launch ramp, the part
that carries the forecast, never has a value.

### 4e. Three competitive-intensity methods, none reconciled

`competitive_intensity_v1` (rank-banded, 4a), gold's
`marketed_peer_count_at_launch_v1` (absolute, gated, the sound one), and
`llm_competitive_intensity_v1`. They agree 9/20 where two can be compared, and
nothing computes that disagreement - `compare_to_rule`
(`competitive_intensity_llm.py:158`) takes `rule_label` as an argument and has
no caller in `app/`.

The LLM module is the most disciplined code in the layer: it refuses on
`inconclusive`, on an out-of-vocabulary label or confidence, and - the good one
- when the model cites a peer that is not on the roster it was given
(`:135-146`). It is the only one of the three that returns a reason instead of
a value when it cannot answer, and it is the one with no path to the product.

Note for rule 3: `peers_at_launch` (`:174`) takes profile dicts keyed exactly
to gold's schema. It does not read `seed/gold/` and no caller does, so the rule
holds today - but the shape invites a future caller to hand it gold rows.
`seed/product_attributes.csv` is the correct source.

### 4f. There is no eval for any of this

`find . -name "eval_*.py"` returns nothing; `scripts/eval.py` is layer 1 only.
Every held-out set is about revenue extraction, printed row labels or XBRL
members. So there is no number, held-out or otherwise, for similarity, peak
selection, uptake or intensity. Under rule 4 that is not a shortfall to fill
with an existing set - those are spent - but a new set drawn from issuers none
of them use. `seed/gold/product_profiles.jsonl` cannot be the scorer for 4a or
4c: it is the oracle those rules would be fitted to.

---

## 5. Wrong figures published

From the first pass, kept. These break "never publish a figure that is not this
product's own" and "publish it with a quote a person can check".

### 5a. Derived rows cite a document that does not contain their input

`orchestrator.py:2025` takes `selected_sources[0]` for every derived candidate,
whatever it actually subtracted.

    derived rows: 16
      cited document contains the derivation input:  3
      cited document does NOT contain it          : 13   (12 of them auto_pass)

`DerivationLineageORM` (`db/models.py:468`) exists and has never been written.
Fix: record the inputs a derivation used, cite them, write the lineage row.

### 5b. A footnote's scope is taken by first regex match anywhere in the note  `[V]`

`labels.py:362 _note_scope` takes the first `_NOTE_PERIOD_RE` hit. Run on the
real ANI note as `table_footnotes` extracts it from `anip-20260630.htm`
(accession 0001023024-26-000069):

    note: "(1) There were no sales of YUTIQ during the quarters ended March 31,
           2026 and June 30, 2026, ... as of the second quarter of 2025."
    _NOTE_SPAN_RE first hit   : None                 <- does not know "quarters ended"
    _NOTE_PERIOD_RE first hit : 'second quarter of 2025'
    _note_scope(note)         -> (None, '2025Q2')
    applies_to(3,'2026Q1') False   applies_to(3,'2026Q2') False   applies_to(3,'2025Q2') True

The real reader on that document, asked for YUTIQ, skips 2025Q2 as
`footnote_says_no_sales` and keeps `2026Q2 18,718 (combined_line)`. So the
suppression fires on the quarter that had sales and not on the two the filing
says were zero. **run13 publishes YUTIQ 2026Q2 = $18.718m `auto_pass` from that
document, against a footnote on the same table saying there were no sales.**
2026Q1 = $19.255m is derived from the same line, also `auto_pass`. And the
skip is emitted as a string `extract.py:729-733` never persists - 0 occurrences
of `footnote_says_no_sales` in any column of any run database - so the review
queue never sees it.

Two structural faults: a note naming several periods can return only one (33
corpus notes do), and "parsed nothing" is returned as "applies to the whole
row" - 566 of 679 corpus footnotes, over `table_footnotes` on every selected
table in the 433 `.htm` files of the run7 cache. Only 10 of those 566 name a
span phrase the regexes cannot see (`period ended`, `twelve-month period`);
the other 556 genuinely state no period. So the misparse subset is small and
the larger fault is that `applies_to()` cannot say "no scope".

Widening `_NOTE_SPAN_RE` is still justified, but an earlier draft's "145 corpus
files" counted `quarters? ended`; the plural `"quarters ended"` is in 13 files,
and at footnote level `quarters?\s+ended` matches 5 notes in 3 files.

**Rule 1:** the period grammar is written down twice. `labels.py:104
_SPAN_MONTHS` and `:105 _QUARTER_WORDS` are literal copies of `periods.py:50
MONTH_WORDS` and `:225 _QUARTER_WORDS`, and `labels.py:94 _NOTE_SPAN_RE` is a
strictly narrower rewrite of `periods.py:152 _PERIOD_PHRASE_RE`, which already
handles "three and six months ended" and "fiscal years ended". A producer
exists and the note reader does not use it - which is why widening one leaves
the other stale.

**Cost:** a YUTIQ/ILUVIEN switch-over is exactly the shape an analyst uses as
an analog for a transition ramp, and the series is inverted at the point of
the switch, silently. Fix: bind the period from the clause carrying the claim,
return a set, distinguish "no scope" from "whole row", and use `periods.py`'s
grammar rather than a copy.

### 5c. `_INCLUDES_RE` fires on "does not include"  `[V]`

`labels.py:85 _INCLUDES_RE` and the block at `:414-420` match claim and product
name separately. On the real ANI guidance note (`anip-20260508xexx991.htm`,
accession 0001023024-26-000049):

    note: "(2) Full year 2026 guidance does not include sales of YUTIQ, ..."
    _INCLUDES_RE fires: True
    read_footnote(asked ILUVIEN) -> names=('YUTIQ',)  period='2025Q2'
    read_footnote(asked YUTIQ)   -> names=('ILUVIEN',) period='2025Q2'

and `extract.py:662,677` turns a non-empty `names` into `FLAG_COMBINED` on the
row. The same note also shows 5b - the period binds to "second quarter of
2025" from a subordinate clause - so the two compound on one note. Over the
corpus, `_INCLUDES_RE` fires on 152 of 679 footnotes; 29 carry a
negated/exclusion claim; 3 of those also trip `_INCLUDES_RE`, and only the ANI
one names products. Narrow but real.

`_no_sales_of` (`labels.py:378`) got 4 of 4 real ANI "no sales" notes right
with 0 false positives over all 31 ANI footnotes in the cache, including this
one, where it correctly returns `()`. Fix: one pattern carrying claim and
subject, as it does.

**Cost:** a row wrongly labelled combined is a row the analyst is told is two
products when it is one - the identity error the brief calls "an analog set you
cannot trust". run13 has 12 `combined_line` rows, 4 `auto_pass`; 5f says the
demotion the flag should trigger is overwritten anyway.

### 5d. Derivation launders provenance at four sites

`derive.py:358`, `derive.py:443`, `orchestrator.py:1279 _candidate_of`, and
`orchestrator.py:1291 _datapoint_from_candidate`, which never writes
`reported_as`, `geography` or `route_of_administration` and defaults scope to
"Product family". `_derived_point` then writes a fresh quote opening with the
bare product name, so the row asserts the identity it just lost. One field
added to `_candidate_of` also revives `HELD_FOR_BOUND` (7b).

### 5e. `deterministic:product_quote_value_ok` publishes expenses and guidance

179 rows auto-passed on "product name and number appear in one sentence". Run
against real quotes it passes an impairment charge, an accounts-receivable
balance, forward guidance, a combined line and a company total - including
AGAMREE 2023Q3 at $81.5m from an IPR&D purchase-consideration sentence, a
quarter before the product was sold (2e).

This is the judging change (doc 005). Nothing in this layer compares a figure
against the product's approval or launch date, though `fda_approval_date` and
the `early_launch` validation reason exist - and section 3f/3g say why that
date is not there to compare against.

### 5f. The combined-line demotion is overwritten

`orchestrator.py:2125` demotes a `combined_line` row to `needs_review` and
`orchestrator.py:2248` then sets `AUTO_PASS` on any supported quarterly/annual
row without consulting `label_flags`. Seven of seven published YUTIQ quarters
are ILUVIEN+YUTIQ. Fix: consult `label_flags` in the auto-pass condition. Note
this closes the hole only where the flag was set at all - 1c is why it often is
not.

---

## 6. Correct figures withheld

20 of 75 expected figures were found, correct, and not published. Each cause is
a bug rather than caution.

### 6a. The sentence splitter breaks table rows into "sentences"  `[V]`

`quality/sentences.py:25` (the file is under `quality/`, not `parsing/`) splits
on `\s*\n+\s*`, and HTML-to-text puts each cell on its own line. On the real
cached `srpt-20230630.htm` (accession 0000950170-23-037125):

    excerpt  : 'EXONDYS 51\n$\n134,688'
    sentences(excerpt) -> ['EXONDYS 51', '$', '134,688']
    sentence_carrying(excerpt, 134688) -> '134,688'   names EXONDYS 51? False

The veto is raised at `llm/client.py:810-816`. run13: **326** datapoints carry
`hard_veto:value_and_product_in_different_sentences`; **212** carry no other
`hard_veto:` flag (168 if "objection" also counts the reconciliation flags).
Both exact on re-run; drift 67 -> 132 -> 224 -> 326 across run8/10/12/13. 322
of the 326 are `extraction_method='llm'` with a newline in the quote - a model
quoting a table block - and the row's own `extracted_from_table` and
`extraction_method` are available to the veto and unused.

**A count is not a capability:** those 212 rows cover 73 distinct (job,
quarter) cells, **40 of which have no published figure from any other row**,
against 99 published cells in run13. They would still have to pass the judge,
so 40 is a ceiling, not a forecast - but it is the single largest hole in the
early ramp, and the holes are in product-revenue tables, where the first four
to eight quarters of a launch live.

### 6b. `quote_states_a_different_period` reads only the first period

`periods_named_in` returns one period, so every prior-year comparative column is
rejected: "$84.6 million and $75.9 million for the three months ended March 31,
2025 and 2024" yields `{2025Q1}` and the 2024 figure is vetoed. 142 rows, 27
alone. A regression introduced in the session that wrote the first pass.

### 6c. `ytd_language_as_quarterly` reads the whole quote

`client.py:828` calls `re_ytd_language(q)` although `read` - the sentence
carrying the value - is computed at `client.py:820` for exactly this. Same for
`TOTAL_REVENUE_RE` and the period check. 52 rows, 2 alone.

### 6d. `filing_contradicts_itself` is tier-blind

Its premise is that the filing said two things; when the second is a reader's
misreading of the same sentence the premise is false, and a tier-4 prose row
vetoes a tier-0 tagged fact. A tier-aware version measured **+3 correct, 0 new
wrong**; a period-type-aware version is +3 and **+1 wrong** - so tier, not
period type. `test_one_figure_one_publication.py:172` locks the current
behaviour and needs rewriting to "two claims of equal strength".

### 6e. A corroborator is never promoted when the winner is held

`orchestrator.py:2461` computes corroborators from winners and never
re-examines the group. 37 quarters across six runs have a `corroborates` row
and nothing published; 14 of those corroborators were themselves clean.
Measured at **+1 correct, 0 regressions**; with 6d, **+4 and zero new wrong**.

The second pass found what this costs the series rather than the score: 10 of
157 quarter-cells in run13, and 5 of the lost corroborators are XBRL, table or
derived - including DAYBUE 2023Q2 and NUPLAZID 2023Q1, which are **series
starts**. See 2d.

### 6f. `conflicting_values` rejects both sides, by period

`check.py:231` groups by `(period, period_type, scope)` and `candidates.py:183`
drops every reading of a period with an error finding. **20 gold-correct
readings discarded**, including Pombiliti 2025Q1 = 21.005 carrying
`reported_as: "Pombiliti + Opfolda"` - the identity the replacement row lacked.
A reading flagged `label_not_understood` vetoes a reading whose label was fully
accounted for, and the reconciler built to choose between claims never gets the
chance.

### 6g. SOURCE_PRIORITY ranks tagged facts below a model reading the same filing

An XBRL instance retrieved inside a 10-Q is typed `QUARTERLY_REPORT` (priority
4); the human-readable document from the same accession is `SEC_FILING`
(priority 0). Both the fallback ranking and the `contested` tier read
`priority_index` before `claim_rank`. 100% of tagged facts sit in the lower
band; 32 were demoted to citations while a model's reading of the same filing
published. Fix: `claim_rank` before `priority_index`, or type the instance by
its accession's form. This is what makes 6d and 6e hold rather than move.

### 6h. Reconciliation groups by period label and ignores `period_type`

65 groups across six runs mix period types, so a six-month figure competes with
the quarter it shares a label with. Add `period_type` to the key at
`orchestrator.py:2293`.

---

## 7. Signal computed and discarded

### 7a. `detect_period_context` dates a filing by a year mentioned once  `[V]`, re-measured

`periods.py:330` takes the latest year outright:
`best = max((key for key in counts if key[0] == framing), key=lambda key: key[2])`.
The correct guard already exists 49 lines above at `:281`:
`throughout = [key for key, n in counts.items() if n * 2 >= most]` - run over
the same counter it returns the right year.

**An earlier draft measured this on text the pipeline never sees.**
`orchestrator.py:1663` calls `detect_period_context(doc.full_text)`, and
`full_text` is capped at 400,000 characters (`documents.py:731-745`). On the
433 `.htm` files of the run7 cache:

    untruncated            235 datable   15 misdate   12 named once   max most-named 180
    as the pipeline runs   235 datable   10 misdate    6 named once   max most-named  66

Perrigo holds exactly: `prgo-20221231.htm` (the document run13's Nutrition job
used) -> `PeriodContext(months=12, month=12, year=2040)` from `(12,12,2040)`
named once against 2022 named 66 times. **ANI FY2025 -> 2027 does not hold on
the pipeline's input** - it returns 2025, correctly, on the truncated text -
and is struck; the ANI document that still misdates is `ani-20260401.htm`
(picks 2026 named once over 2025 named 3 times). The ten: `prgo-20221231`,
`prgo-20231231`, `coll-20231231x10k`, `coll-20241231x10k`, `tvtx-20250331`,
`tvtx-20250630`, `tvtx-20260630`, `fold-20240630`, `fold-20250630`,
`ani-20260401`.

**Cost is larger than "signal discarded".** With `context.year = 2040`,
`prose.py:353 _after_the_document` returns `False` for `2023Q1`, `2030` and
`2040Q4` alike - not a weakened guard, a disabled one. And the object is
stringified into the model prompt at `orchestrator.py:1831` as
`reporting_period: "twelve months ended December 2040"` with `period_columns:
["2040","2039"]`, so the model is told the filing's comparative is 2039. A
debt maturity or a milestone forecast entering the series as revenue is the
failure the workbench exists to prevent. Its output is section 5's.

**Rule 5, load-bearing:** `periods.py:324-329` justifies the defective line
with "a Q4 2005 release mentions 'three months ended December 31, 2004' five
times in its footnotes against four for 2005, which is how the document came to
be dated a year early" - counts, an unnamed document, and a defect history,
read as the argument *for* `max(key=year)`. The next reader inherits a claim
they cannot check and the guard at `:281` looks like a different concern.

### 7b. `_declared_slack` and `HELD_FOR_BOUND` are dead because one key is dropped

42 of 42 derived rows across three runs are unbounded. The tagged reader emits
`rounding_uncertainty_usd_millions` and `_as_datapoint` reads it, but the
quarter inputs arrive through `_candidate_of`, which drops it (5d). One missing
key kills the bound guard, the "+/- n from input rounding" clause in every
derived quote, and the citation field.

### 7c. 75 six- and nine-month tagged facts are discarded  `[V]`, re-measured

`xbrl.py:139-156` returns `None` for any span but 3 or 12 months. Over the 61
distinct `.xml` source documents run13 recorded (all in the run7 cache), with
members resolved against run13's job names and `load_products()`:

    predicate an earlier draft used (amount, months, product member):
        {1: 38, 3: 181, 6: 95, 9: 95, 12: 124}   six+nine = 190
    predicate product_facts applies (xbrl.py:566-573, also element in
    revenue_elements and not hypothetical):
        {3: 114, 6: 37, 9: 38, 12: 66}           six+nine = 75

190 is money-unit facts on a resolvable product axis; **75** is what would
otherwise have become tagged revenue readings. The headline overstated the
recoverable facts by 2.5x.

An earlier draft said the span-difference rule is therefore fed "only by
tables". Not in these runs: across run8/10/12/13 **every** `six_month` and
`nine_month` datapoint is `extraction_method='llm'` (run13: 41 nine-month, 12
six-month), zero from `table`, zero from `xbrl_fact`. So `derive.py:49-56` is
fed by the model alone - the producer the pipeline ranks lowest - while the
filer's own audited spans are discarded. This is also why Q4 has zero XBRL
readings (2d): Q4 and Q1 for a filer that tags but prints no quarterly table
are the cells at risk, the tail and the start of the ramp.

**Rule 5, minor:** `extraction/tagged.py:41-42` hard-codes another module's
constant in prose ("scored above the table reader's 0.75"); true today
(`candidates.py:35`), which is exactly how it goes stale silently.

### 7d. The judge is blind to what the pipeline knows

Unit, currency, geography, extraction method, filing form, sibling rows, the
filing's own tagged value for the period, the footnote. `peer_names` is
accepted by two functions and supplied by none, so `hard_veto:other_brand`
cannot fire. Footnotes reach the quote on 2 of 549 rows. See doc 005.

### 7e. Smaller

- `region_rows_no_total` is produced, stored and read by nothing.
- 1,168 of 1,183 fingerprint skip notes are unconsumable by their only reader.
- `issue_flags` holds 177 free-text sentences beside its tags, read with a
  substring test that already misses three of the four reconciliation outcomes.
- `reported_as` reaches one of six surfaces (2e).
- `corroborates` appears in real output (67 rows in run13), in no doc, and in
  none of `eval.py`'s five output states - invisible to the score and the
  reader.

---

## 8. A product bound to a company that does not sell it

The reported "aspirin returns another company's filing" needs no bug - it is
what the code does on that input.

- `_identity` (`orchestrator.py:593`) only calls `resolve_cik` when a ticker or
  manufacturer was supplied. A drug name alone skips the SEC name index and
  goes straight to the model.
- `resolve_cik_from_search` keeps the `cik` digits and discards `company_name`,
  `confidence`, `source_url` and `notes`, all of which the prompt asks for. A
  confidence of 0.05 is accepted like 0.95.
- `SECConnector.retrieve` has no `product` parameter. Nothing can check that
  the issuer sells the product.
- A live repro stored **28 ACADIA filings, 22.1 MB, against a job for aspirin**,
  with `job.manufacturer` still None - which also leaves `issuer=""` on the
  member register, where two filers' `acme:ProductMember` collide in one slot.
- When no CIK resolves the job is flagged `sec_retrieval_failed`, whose meaning
  is "EDGAR refused, do not fall back", and the search is skipped. The user is
  told we could not reach the SEC when we do not know who sells this.

And `resolve_cik` is unreliable when it is called. Against the real
10,422-registrant index: `Vertex` -> Vertex, Inc. (tax software);
`Vertex Pharmaceuticals` -> None; `Eli Lilly and Company` -> None; a wrong
ticker suppresses a good name. 1,966 registrants have a single-word normalised
title.

**Fix, in order:**
1. Move `_extract_metadata` before `_identity`. openFDA already knows a drug's
   sponsor and is asked at `orchestrator.py:419`, after the CIK has been
   guessed and the filings downloaded. This depends on 0d - and on 3c and 3d,
   or it will resolve the wrong sponsor.
2. Try the name index whenever a name exists, and try the name when a ticker
   misses.
3. Keep the model's `company_name` and `confidence`, and refuse below a floor.
4. Pass the product to retrieval, or check the resolved issuer against the
   sponsor openFDA named, and say `no_filer_of_record` rather than
   `sec_retrieval_failed` when identity is what failed.

No held-out set can see this class: every job in every run supplies
manufacturer, ticker and CIK, and there is no test of `resolve_cik` at all.

---

## 9. The project's account of itself is false in several places

A stale doc traps the next person; a test asserting the wrong thing defends the
mistake.

### 9a. A module docstring cites a test by the wrong name and overstates what it covers ~15x  `[V]`

`extraction/adjudicate.py:29-32` says "`test_no_real_gold_row_needs_review`
runs every quarter in `seed/gold` through here and requires all of them to
resolve." No test of that name exists - the string appears only in the
docstring. An earlier draft of this item stopped there and called it "a false
safety claim". That overstated it: a guard **does** exist under another name,
`test_extraction_stack.py:715 test_no_real_series_trips_the_adjudicator`, driven
by `real_rows_that_trip()` at `:1348`, which reads gold's quarterly and annual
files and asserts nothing trips.

What is false is the coverage. "Every quarter in `seed/gold`" is measured as:

    gold quarterly rows                        2203
    years reached by the total-vs-parts check    36
    quarterly rows inside those years           143   (6.5%)

A drug-year is reached only if `annual_revenue.jsonl` carries a matching
`(drug_name, year)` with a value. Only `adjudicate_total_against_parts` and
`adjudicate_split_ownership_quarter` ever see real data;
`adjudicate_reported_value` and `adjudicate_positional_solutions` are exercised
only by the 13 fixtures in `adjudication_cases.jsonl`.

So two defects: a wrong name, and a coverage claim overstated by ~15x. And a
third of the same shape in the guard itself - `real_rows_that_trip`'s docstring
(`:1350-1353`) says "any year with all four quarters", the code applies no such
filter (`expected_parts=len(quarters)`), and 1 of the 36 years reached has
three.

### 9b. Rule 4's own discovery glob misses a holdout set  `[V]`

`backend/tests/answer_keys.py:22`:

    return sorted(REPO.glob("seed/*/*.jsonl")) + sorted(REPO.glob("seed/*/*.json"))

Its docstring: "the keys are discovered from the tree instead, and a set added
later cannot be forgotten here." One was - `seed/holdout_foreign_xbrl.json`
sits **directly** under `seed/`, one level up from the glob: 17 cases, 8
foreign issuers (argenx, BioNTech, Grifols, HUTCHMED, Dr Reddy's, Alvotech,
Indivior, Genmab). Missed twice over: `products_in()` reads
`drug_name`/`member`/`expected`, and that file keys its product as `product`.

Verified by running `answer_key_paths()`: 21 files, and the foreign set is not
among them. Its keys are `product`, `expect`, `member`, ... - `products_in()`
finds none of `drug_name`/`expected`, so the 17 `product` values are invisible
and only the 3 `member` names surface. Of the 8 issuers, **7 are invisible to
`spent_issuers`**; Genmab is not, because `seed/cases/foreign_xbrl.json` is
discovered and names it.

A future holdout drawn from those seven would pass
`test_no_case_comes_from_a_scored_issuer` while reusing a spent issuer - the
exact failure the module was written to close. Fix: `seed/**/*.json{,l}`, and
read `product` too.

Same function, same shape, not previously listed: `identifying()` emits
single-letter and punctuation-bearing tokens - `GENMAB A/S` yields `{genmab, a,
s}`, `Indivior Pharmaceuticals, Inc.` yields `pharmaceuticals,` with the comma
kept so it escapes `COMPANY_WORDS`. `as` *is* in `COMPANY_WORDS` (`:66`) -
the `a` and `s` exist only because `identifying()` splits `A/S` on the slash.
So the one "covered" foreign issuer is covered by two junk tokens.

**Rank:** understated. This document's closing section owes a new held-out set
before any fix is reported as an improvement; a guard that cannot see seven
issuers is on that critical path, not a section-9 tidy-up.

### 9c. Rule 1's own table has a false entry, and its first row is still live in the code

`DOCUMENT_FITNESS` is cited in CLAUDE.md as a derived list that held. It is a
hand-written literal of 10 of 12 `SourceType` members (`orchestrator.py:117`),
as is `SOURCE_PRIORITY` (`:218`); both omit `dailymed` and `openfda` and no
test asserts exhaustiveness. Behaviourally harmless - both omissions rank last
anyway - but it is the rule's worked example of the good shape. (The other five
exemplars do hold.)

And the table's **first** row - `form in ("8-K", "10-Q")` excluding `8-K/A` -
is still live in two places:

- `connectors/sources.py:541` is `if form != "8-K": continue`, inside
  `_fetch_earnings_exhibits`. **108 filings carry item 2.02 on form `8-K/A`**
  in the cached corpus and every one is skipped. `form_family()` exists at
  `sources.py:198`, built for exactly this, and is not used here.
- `sources.py:289,832`: `PRIMARY = {"10-K","10-Q","20-F","40-F"}` /
  `SECONDARY = {"6-K","8-K"}`, applied as `if form not in allowed`. **1,308
  filings** in the cache are forms the module's own `reports_a_period()`
  accepts and this set drops - including 317 `10-K/A` and 188 `10-Q/A`, the
  amendments that carry restated financials.

No test covers either. Whether any of those amendments carries a
product-revenue table is not established; what is measured is that they are
never listed.

Gold's own 2,203 quarters cite 284 accessions across 8 issuers, and none is an
amendment:

    8-K 173   10-Q 75   10-K 34   6-K 1   20-F 1   amendments: 0

Fix these because rule 1 says a filter is part of the claim. Do **not** fix
them expecting quarters: the 8-K/A the rule-1 table cites carries company-level
CHF statements with no per-product figures (2f), and no filing has yet been
opened and shown to carry product periods this filter drops.

### 9d. Four `SCRIPT_ONLY` reasons do not survive a grep, and 28 of 30 have no script  `[V]`

`tests/test_capabilities_are_wired.py` keeps a hand-maintained exception list,
which is fine - but four of its 30 entries carry a reason that is false,
verified with the test's own `_definitions()` / `_references()`:

- `reading_rank` is already referenced inside `app/` at `orchestrator.py:1582`.
  The entry is a no-op and its reason ("kept for the evals that compare against
  them") is not true.
- `html_tables`, `extract_revenue_rows`, `read_positional_block` carry the same
  reason, and **no eval can import them**:
  `test_the_eval_runs_the_pipeline.py:48` fails any `scripts/eval*.py` that
  imports `app`. They are kept for *tests*.
- `read_peak_sales_csv`'s reason is "importers, run by hand against a file
  someone was sent". There is no caller in `app/` or `scripts/`, and no route.
  It cannot be run by hand at all, and `README.md:106` documents its CSV columns
  as a supported path.

Checked across all 30, not only these four: **only 2 entries have any caller
in `scripts/` at all** - `save_register` and `backfill_job`. The test computes
the refuting evidence and discards it: `outside_scripts` is built at `:94` and
`name in SCRIPT_ONLY` at `:98` short-circuits before it is consulted (rule 1,
with the derivation already in hand). And the functions on the list are layer
3 - `rank_analogs`, `calculate_revenue_uptake`, `select_peak_estimate` - so
the list is what stops the suite saying the product's third layer is unwired. The list is named
`SCRIPT_ONLY`; for 28 of 30 entries nothing scripted calls them. The group
comment "Domain types, constructed in tests and by callers outside this
package" (`:53`) covers four *functions* with no caller outside the package.
All 30 names still resolve to a definition, so nothing is stale in the other
direction.

A reason nobody re-checks is how a to-do becomes permanent.

### 9e. Stale docs that read as current  `[V]`

| where | claim | what is true |
|---|---|---|
| `README.md:78` | "the five evals" | `docs/evaluation.md:3` - "There is one eval". `1cc1db6` deleted the other nineteen three days after the sentence was written. |
| `README.md:83-87` | the headline "runs none of the twelve stages in `run_job`, so the LLM extractor, the evidence judge and conflict reconciliation are all absent from it ... The same script **without the flag**" | No such flag exists. `eval.py` goes in through `POST /runs` and runs every stage. The README tells the reader the headline excludes the judge and reconciliation; it includes them. |
| `AGENTS.md:15` | "backend `uv run pytest` (23 tests). Frontend has no test suite" | 711 tests; `frontend/package.json:10` has `vitest run` plus two page tests. |
| `docs/sourcing/excluded-products.md:1-7` | "the eight excluded products ... more than half of its own addressable surface" | `excluded_products.jsonl` holds 6, and 7 of the 20 seed products have no gold quarterly row (the 6 plus Revatio) - "eight" is wrong under either reading. The catalog is 2,203 quarters, so 434:2203 is ~20%. The document's prioritisation conclusion no longer follows from its own arithmetic, and it carries no as-of date. |
| `docs/plan-after-the-full-sweep.md:14` vs `README.md:89` | 1,162/1,415 vs 1,070/1,415 | Two live docs, same denominator, different current score; the newer has the higher number and labels it "before any of the fixes below". |
| `CLAUDE.md:195` | `scripts/eval_*.py` | Only `scripts/eval.py` exists - a name pattern written down for files that do not exist, in the file that warns against it. |
| `README.md:106-110` | the manual peak-sales CSV and its columns, as a supported path | `read_peak_sales_csv` has no caller in `app/` or `scripts/` and no route (9d). An analyst who prepares that file has nothing to feed it to. |
| `docs/evaluation.md:44-46` | both answers are represented in every case file | The two headline files carry zero null expectations (0b). |

**Rank within the table:** `README.md:83-87` is not a stale row. It tells the
reader the headline **excludes** the LLM extractor, the judge and
reconciliation and is therefore a floor; it includes all three. An analyst
reads a pessimistic number as conservative when it is the whole system's
actual score - a false claim about the meaning of the only number the product
publishes.

`docs/pipeline.md` has 10 rows; `JobStep` has 13 members and `run_job`
(`orchestrator.py:403-477`) makes 14 distinct calls besides `_set_step`.
Omitted from the doc: `_search_revenue_fallback` (`:426`),
`_search_quarters_fallback` (`:436`) - both gated on `enable_llm_search`, which
is **`True` by default** (`config.py:53`) - `_quarters_no_filing_covers`
(`:433`), `_record_unfiled_quarters` (`:443`) and
`_record_quarters_only_reported_with_another_product` (`:446`). In the other
direction, `_reconcile_with_llm` has a row in the table but is not called by
`run_job`; it is called from `orchestrator.py:2279` inside `_judge`.

And `pipeline.md` contradicts itself on the bulk-tagged reader: `:141` says it
is off unless configured, `:163` says "`app/` calls all four" - under a section
that heads five readers. `config.py:42` defaults `notes_dataset_dirs = ""`, so
one of the five is off by default and contributes nothing to any measured
number.

Everything else spot-checked in `pipeline.md` holds (`PDF_COLUMN_GAP = 1.5`,
the 0.7 confidence gate, `EARNINGS_ITEM = "2.02"`, every EX-99 rather than the
first, `SOURCE_PRIORITY`/`CLAIM_STRENGTH` as two axes).

### 9f. Tests that pass on data the pipeline does not produce  `[V]`, narrowed

No assertion-free tests exist - hygiene is good. The failure mode present is
the other one, and an earlier draft overstated it. `tests/test_export.py:18-44`
constructs `CanonicalProductORM`, `MoAComponentORM` and `PeakSalesEstimateORM`
by hand and asserts the exporter reads them back (`assert row["peak_type"] ==
"consensus"` at `:40`).

"Rows the pipeline cannot write" is true of **one of the three**.
`PeakSalesEstimateORM` has no writer anywhere in `app/`. The other two are
written - `CanonicalProductORM` at `orchestrator.py:835`, `MoAComponentORM` at
`:883` (and both in `remediation/backfill.py`) - behind a gate on
`first_label.brand_names` at `:822`. That gate never fired in run8/10/12/13
(0 rows in all three tables, in all four), which is an empirical fact about
those runs and a capability question for M7, not an absence of a writer.

The four analytics test modules are a different failure again, not this one:
`test_peak_sales.py`, `test_uptake_metrics.py`, `test_competitive_intensity.py`
and `test_analog_matching.py` construct no ORM and assert no SELECT. They test
pure functions that `app/` never calls. `test_competitive_intensity.py:63-64`
asserts `count("low") == 2` and `count("high") == 2` over six synthetic
snapshots - the test pins the forced distribution 4a calls the defect. A test
that asserts the defect is the spec is rule 4's failure in test form, and it
is the reason 4a will be argued about when someone fixes it.

### 9g. Holdout sets: which are spent, which are orphaned  `[V]`

    holdout/               reached by answer_key_paths() glob; no code names it
    holdout2/              reached by answer_key_paths() glob; no code names it
    holdout_labels         tests/test_product_disambiguation_holdout.py, CLAUDE.md
    holdout_members        scripts/eval.py, tests, CLAUDE.md  -- live, guarded
    holdout_foreign_xbrl   scripts/sourcing/build_foreign_xbrl_holdout.py only

`seed/holdout/` and `seed/holdout2/` are 348 KB of spent cases that no code
names - an earlier draft said "referenced by nothing", produced by a name grep
that a glob-based reader escapes (rule 2). `answer_key_paths()` reaches both,
and they are the **sole** source of eight issuers in `spent_issuers()`:
AbbVie, Alnylam, Amgen, Insmed, Novartis, Novo Nordisk, Sanofi, Vertex.
Deleting them (section 10) would silently free all eight to be drawn into a
"new" held-out set - 9b's failure arriving through a tidy-up. **They stay.** `seed/holdout_labels/product_labels.json`
**has become a test fixture**: `test_product_disambiguation_holdout.py:19-24`
loads it into the suite, so it is re-scored on every `pytest` run and is a
permanent tuning target. Rule 4 says stop tuning before the set is exhausted; a
set in the suite can never stop being tuned against. It is also the only
holdout with no `test_no_case_comes_from_a_scored_issuer` guard - the property
currently holds - `{alkermes, biogen, jazz}` intersects no other key's
identifying words - but nothing checks it. (`seed/holdout_foreign_xbrl.json`
has no guard either, and is not even discovered - 9b.)

No key has become a pipeline input: `test_no_pipeline_input_carries_gold_evidence`
passes and is not vacuous, and `scripts/audit_gold.py` runs clean (2,203
quarterly and 75 annual rows, 0 findings). One gap:
`test_gold_is_not_an_input.py:135` reads only
`seed/gold/quarterly_revenue.jsonl`, so an input carrying URLs or quotes copied
from `annual_revenue.jsonl` (75 rows), `product_profiles.jsonl` (62),
`peak_sales.jsonl` (12) or `adjudication_cases.jsonl` (13) passes the
value-level check. That is CLAUDE.md's own rule-1 table row, "three answer-key
filenames" - and the same test's `GOLD_MARKERS` (`:35-36`) is a hand-written
tuple of four gold filenames, so app code naming `annual_revenue.jsonl`,
`adjudication_cases.jsonl`, `excluded_products.jsonl` or
`unresolved_quarters.jsonl` by bare filename is caught only by the broader
`"seed/gold"` marker. The same file shows the right shape twenty lines away:
`PIPELINE_INPUTS` (`:28`) is drift-guarded by `_seed_files_the_app_reads()`
(`:192-201`), which forces the literal to be a superset. `GOLD_MARKERS` should
copy it. A cousin: `seed/holdout_members/combined_name_members.json` carries
`issuers_deliberately_excluded`, a hand-written list of ~25 issuers duplicating
what `spent_issuers()` derives, already stale.

An earlier draft said "four of gold's answer keys are scored by nothing at
all" and listed five. One of the five is scored: `adjudication_cases.jsonl` is
replayed through the adjudicator by `test_extraction_stack.py:705`. The other
four are *read* - by dataset-integrity tests in `test_gold_dataset.py` - but no
**eval** scores the pipeline against them; `scripts/eval.py` reads only
`seed/cases/*.json` and `seed/holdout_members/`. "Scored by nothing" had
conflated "no eval scores against it" with "nothing reads it".

### 9h. `check_by_hand.py` verifies the weakest of the three things a citation claims  `[V]`

It runs. What it does not check: **the stored quote** (`source_quote` appears
nowhere in it - it searches for the *value* in a window of -400/+120 characters around any
>=4-letter word of the drug or generic name (`:207`), so a fabricated quote passes as long as the
number sits near the name); **the period** (`check_instance(raw, drug, None,
...)` at `:181` passes `period_start=None`, so the context-period filter at
`:94` never runs and a tagged fact for the wrong quarter passes); and annual
figures (`:175` skips them). Also `float(d.get("value_reported") or 0)` makes a
missing value search for 0.0, and `:24` hardcodes a personal contact address as
the SEC user agent - `getenv`/`os.environ` appear nowhere in the file.

Period attribution is the defect class `docs/research/sec-table-period-context.md`
exists for, and the by-hand checker is blind to it.

**Rank:** understated. This is the product's only by-hand audit path - the
last line of defence for "citations are mandatory on every source-derived
field" - and what it certifies is "a number near the name". An analyst
spot-checking a suspicious figure gets a green light from a check that cannot
see a wrong quarter.

### 9i. Three run options the API accepts and nothing reads  `[V]`

`pdfs`, `random_validation_sampling`, `use_uploaded_template`
(`domain/models.py:327,329,330`) have zero reads in `app/` - each grep returns
only the definition. Sampling is unconditional at `validation/sampling.py:67`
(`validation_sample_rate`, `config.py:28`, 0.10). They are persisted into every
run's `options_json` (`main.py:152`) - verified across all 24 runs in
run8/10/12/13, each carrying `{'pdfs': True, 'random_validation_sampling':
True, 'use_uploaded_template': False}` - so a caller reading a run back believes
PDF parsing or sampling was toggled when it was not. The mechanism: PDF parsing
is decided by file extension at `parsing/documents.py:685`, never by the
option, and sampling is unconditional at `validation/sampling.py:67-69`. An
analyst who unticks PDFs still gets them; one who unticks sampling still gets
10% of auto-pass rows in their queue.

### 9j. The account is also false in the docstrings, where rule 5 forbids it  `[V]`

Section 9's thesis is that the project's account of itself is false in its
docs. The same account is in its docstrings - counts, defect histories and
coverage claims that cannot be checked from the file they sit in - in the very
modules this section audits. Each is rule 5 in its pure form; only 9a flagged
one of them:

    test_capabilities_are_wired.py:3-7      "Six times in this project a reader... was
                                            written, given tests, measured, and never called"
    scripts/eval.py:8-13                    "Nineteen eval scripts called the readers
                                            directly and two called run_job"
    tests/answer_keys.py:5-7                "a set was built reusing three issuers another
                                            key scored"
    test_product_disambiguation_holdout.py:3-8
                                            "the way three readers here were once written,
                                            tested, measured and never called"; and "Biogen,
                                            Jazz and Alkermes appear in no dataset" - a
                                            data claim nothing checks (true today)
    test_gold_is_not_an_input.py:5-8        "broken within a day of the gap existing"
    connectors/sources.py:13                "Two rules here were bought with wrong answers"
    extraction/adjudicate.py:29-32          9a
    test_extraction_stack.py:1350-1353      "any year with all four quarters" - a filter the
                                            code at :1392 does not apply

`test_gold_is_not_an_input.py:39-49` deliberately exempts docstrings, which is
why `adjudicate.py:29` can name `seed/gold` in app code and pass. Each of these
belongs in a commit message.

---

## 10. Delete or decide

Measured, with no production caller or no effect:

- `_search_revenue_fallback` (ran twice, produced 0 datapoints) and
  `_search_quarters_fallback` (never ran in six runs), plus
  `_quarters_no_filing_covers`, `_record_unfiled_quarters`, the
  `NO_FILER_OF_RECORD` code and the dead `SourceType.LLM_SEARCH` branch. **Not**
  `judge_with_search`, which touches 368 rows.
- 10 of the 22 hold mechanisms never fire; 4 are unreachable because
  `filter_revenue_candidates` applies the same predicate upstream.
- `ValidationTaskORM.issues / judge_status / deterministic_results`: 0 of 1,850
  rows across 18 databases.
- `lot_extractor.yaml`; `Settings.llm_search_max_queries`;
  `FileStore.public_uri`; `ExtractionOptions.pdfs`,
  `random_validation_sampling`, `use_uploaded_template` (9i).
- `tables.py`'s `extract_revenue_rows` half and its duplicate `_scope_for`.
- ~~`seed/holdout/` and `seed/holdout2/`~~ - **no.** They are the sole source
  of eight issuers in `spent_issuers()` (9g). Deleting them frees those
  issuers for a "new" held-out set.

Decide rather than leave:

- `positional.py` (116 lines): 0 PDFs in 2,153 recorded sources and 0 in the
  553-file cache. Gold cites a PDF on 643 rows, which looks like a reason to
  keep it - but 2f shows those PDFs are the readable copy of EX-99 documents
  that are on EDGAR in HTML, so the count describes gold's citation choice
  rather than a format the pipeline must read. Delete unless a source that is
  only ever a PDF turns up.
- `adjudicate.py` (256 lines) has no production caller, but
  `seed/gold/adjudication_cases.jsonl` holds the cases it was written for. A
  wiring decision, not obviously a deletion - and its docstring's false test
  citation (9a) goes either way.
- `analytics/` is 846 lines whose three tables are empty in all 19 databases.
  Section 4 is the argument for **fixing the method before wiring**, and
  section 3 is the argument that wiring it now would feed it wrong attributes.
  Neither is an argument for deleting it.

---

## 11. Infrastructure

- **No per-job deadline.** `handle_job` awaits `run_job` bare; no `wait_for`
  anywhere in `pipeline/`, `jobs/` or `main.py`. A 240s search call can occupy
  726s through retries, and httpx's timeout is per-read, not wall clock. A hung
  job holds its semaphore permit forever.
- **Startup recovery throws away finished work.** FIRDAPSE had completed 12 of
  13 stages - every figure extracted, judged and gated - and was marked
  `failed`. A job at or past `quality_checks` has nothing left that appends
  datapoints.
- **Unfinished jobs publish.** `build_dashboard_preview` and `eval.py` both
  read them without distinction (1f, 0c). 32% of run13's published datapoints
  come from jobs that never completed.
- **Write amplification.** One datapoint is written and committed four times,
  on SQLite, whose driver blocks the shared event loop on the write lock.
- `fetch_page` reaches sec.gov with no throttle and no retry, and 9 of 10
  `llm_search` sources across five runs are sec.gov URLs. The guard test that
  should catch this iterates a hand-written list of four names and silently
  skips module-level functions.
- **Review is one click per row.** `POST /validation-tasks/{id}/actions`
  (`main.py:476`) takes a single task; no bulk endpoint, no bulk UI. run13 has
  374 open tasks across 20 products. Confirming rows also does not de-duplicate
  - confirming both the Worldwide and ex-U.S. AYVAKIT rows publishes both.
- **The Export page needs a UUID typed by hand** (`ExportPage.tsx:41`) and finds
  the run via `localStorage.getItem('lastRunId')`, so a previous run's export is
  unreachable from a fresh browser.

---

## 12. Retrieval: cascade by coverage, not by assumption

**Status: UNMEASURED.** Everything in this section is a design, not a finding.
No tier order below has been scored, and the section exists partly because
three claims about document contents were asserted in this document's own
history without opening the document. It should not be implemented ahead of
sections 0-2, and its ordering must be measured before it is trusted.

### 12a. What is wrong with the present design

Retrieval decides what to fetch from *properties of the filing index* - the
form string and the item codes - and never from the document. Three literals
carry that decision:

    sources.py:289,832  PRIMARY = {"10-K","10-Q","20-F","40-F"}
                        SECONDARY = {"6-K","8-K"}
    sources.py:541      if form != "8-K": continue
    sources.py:544      if EARNINGS_ITEM not in items      ("2.02")

Each is rule 1's shape: a written-down list standing in for "which filings
carry a product-level revenue table". The list cannot be right, because whether
a filing carries such a table is a property of the filing, and issuers differ -
Eli Lilly and United Therapeutics put product revenue in the periodic reports
(374 of 374 and 365 of 368 of gold's rows are SEC), J&J puts it in an 8-K
EX-99.2 schedule, and an acquired product's pre-acquisition history may be in
neither.

The failure is symmetric and both halves are live: the filter fetches
documents that carry nothing (`sec_include_8k` pulled cover pages into 14 of 28
documents in a live repro) and skips documents that carry something, and in
neither case does anything downstream notice.

### 12b. The coverage predicate

The one question retrieval never asks is the only one that matters: **does this
document answer a period I still need, for this product?**

Define it over a parsed document `D`, a product `P` with its alias set, and the
set `Q` of periods still unanswered for `P`:

    for each q in Q, coverage(D, P, q) is one of
      carries   D holds a figure whose row label resolves to P (labels.read_label)
                under a column or context whose period resolves to q (periods.py)
      refutes   D states P had no sales in q, or D's own reporting context
                (periods.detect_period_context) excludes q entirely
      silent    neither

    and the document verdict is
      answers        carries every q in Q
      partial        carries some q in Q
      names_only     P resolves somewhere in D, no q in Q is carried
      absent         P resolves nowhere in D
      unreadable     the parse failed

Every branch is computed from the document by machinery that already exists -
`read_label` for the subject, `periods.py` for the period, `fingerprint.py` for
the table shape. Nothing in it reads a form code or a filename.

**The predicate must be a figure test, not a name test.** `"OPSUMIT" in text`
is `names_only` at best: a product is named in narrative, in a risk factor, in
a collaboration note, beside no number at all. This distinction is the whole
point of the section - it is exactly the error that put a wrong claim about the
Actelion 8-K/A into this document twice (2f), and a cascade that routes on a
name test would encode that error in code.

### 12c. The cascade

Escalate a source class only while `Q` is non-empty, and record at each step
which class answered which period:

    tier  class                                          entered when
    0     tagged facts in filings already held           always
    1     10-K / 10-Q and their families                 always
    2     8-K EX-99 earnings exhibits (item 2.02)        Q non-empty after 1
    3     6-K / 20-F / 40-F and families                 issuer files them
    4     8-K/A item 9.01 acquired-business financials   Q spans an acquisition
    5     the issuer's investor-relations site           Q non-empty after 4
    6     model web search                               Q non-empty after 5

Three things this ordering is **not** allowed to be:

- **Not a written-down list.** The tier of a filing is derived from
  `form_family()` and its item codes, not matched against a literal set. The
  order of the tiers is a snapshot of measured yield and must say so, with the
  measurement that produced it and what would make it stale.
- **Not a reason to stop early.** "Stop" means stop escalating to a new tier,
  never stop reading within one. A second reading of a period already answered
  is a corroborator, and 2d is a quarter lost because a corroborator was
  discarded. Tier 0 and 1 are always read in full.
- **Not a licence to add tier 5 or 6 first.** Tier 6 exists today as
  `_search_revenue_fallback` and produced 0 datapoints in two runs (section
  10). Tier 5 is justified by 58 rows of gold, all Actelion/J&J. Both are the
  narrow tail; neither is the reason to build this.

### 12d. What it is worth, and what would show it

Unknown, and that is the point of stating it as a design. What can be said:

- The predicate is worth something on its own, before any cascade, because it
  turns "we fetched 28 documents" into "14 of them carried nothing", which is
  measurable and currently is not.
- Tier 4 has no demonstrated quarter behind it (2f). Tier 5 has 58 rows of
  gold behind it. Tiers 2 and 3 are already reached today.
- So the honest expectation is that this section improves the *tail* - thin
  issuers, acquired products, foreign filers - and does nothing for the median
  case, where sections 5 and 6 show the pipeline already holds figures it
  refuses to publish.

**To measure it:** count, per issuer in a run, the periods answered per tier
entered and the documents fetched per period answered. A cascade that lowers
the second number without lowering the first is working. Both numbers are
available from `RetrievedSource` and the datapoints today; neither is recorded.
Score on a set drawn from issuers none of the existing keys use, containing at
least one acquired product and one issuer whose product detail is not in its
periodic reports.

---

## Order of work

Each step is a commit. Nothing is scored until step 1 is done.

1. **Section 0** - the baseline. Rebuild `gold_all.json` with a divergence
   test; give the gold case files refusals; make `eval.py` scope-aware and
   stop it tallying unfinished jobs; replace the README number. Then 0d and
   0e's `sec_include_8k`, and report the result as a second configuration
   rather than a regression or an improvement.
2. **1b, then 1d** - the coverage number and the export's period types. Two
   small changes that stop the product asserting things that are not so.
   `_published_quarters` filters `period_type`; `quarterly_revenue.csv` gains
   `period_type` and a status filter.
3. **7a** - `periods.py:330`. One line, the guard already exists 49 lines
   above, and it disables a forecast guard for 15 documents.
4. **6a, 6b, 6c** - the three vetoes. 212 + 27 + 2 sole-blocked rows.
5. **6g, then 6d, 6e, 6h** - claim rank before source priority, then tier-aware
   contradiction, corroborator promotion, and `period_type` in the key.
   Measured at +4 with zero new wrong answers, and 6e is what recovers the
   series starts in 2d.
6. **2a and 2b** - one selected figure per (product, quarter, scope), and a
   controlled geography and scope vocabulary. The 115 open
   `duplicate_period_scope_formulation` checks are the work list. Worth
   `>=4 uptake points` on 6 products rather than 5, and it is the precondition
   for every layer-3 number ever meaning anything.
7. **1c, then 5f** - the alias list must not contain another product's name, and
   the auto-pass condition must consult `label_flags`. In that order: 5f only
   helps where 1c let the flag be set.
8. **5a, 5d, 7b** - derivation: cite what it subtracted, carry provenance,
   restore the bound. One change to `_candidate_of` serves all three.
9. **5b, 5c, 6f** - the footnote clause binding, "does not include", and
   `conflicting_values` not rejecting both sides.
10. **Section 3** - layer 2, in this order: read route and dosage form from
    `products[]` and treat a disagreement with `openfda.route` as a conflict for
    the judge (3a, 3b); search and match `products[].brand_name` too (3c, which
    alone recovers Flolan and Ventavis); delete or gate the substring fallback
    and drop molecule aliases by the record's own generic name (3d); resolve
    several applications by earliest ORIG or record the ambiguity (3e); assign
    `initial_approval_date` or drop the field so the fallback runs (3f); write
    the approval date onto the indication rows from the record that has it
    (3g); give `therapeutic_area` a producer distinct from `indication` (3h).
    Then turn `openfda` and `product_metadata` on and re-measure **against
    `seed/gold/product_profiles.jsonl`**, which already holds the curated
    answer for 62 products and is wired to nothing. **0b's profile judge is
    measured separately, after this**, because only now does it have fields to
    judge - and its correction rate is measurable against the same oracle.
11. **Section 8** - identity, starting with the stage reorder, which depends on
    step 10 landing first or it resolves the wrong sponsor.
12. **Section 4** - layer 3's method, before any wiring: absolute intensity
    bands with a not-assessed gate; `peak_eligible`, contiguity and dedup in
    `_mature_observed_peak`, partition rather than abort on multiple scopes;
    scope and currency on the uptake denominator; `indication_area` on
    `ProductProfile` and `minimum_attributes` above 2; normalise the value
    vocabulary before comparing with `==`. Then a held-out analog set (4f), and
    only then wire it.
13. **Section 9** - the false docstring (9a), the glob (9b), the four
    `SCRIPT_ONLY` reasons (9d), the stale docs (9e), `check_by_hand` (9h).
    9c's `8-K/A` and `10-K/A` filters are a retrieval change, so they go with
    step 4 - as a rule-1 correction with no quarter attached to it, until one
    is measured.
14. **Section 10** - the deletions, once nothing above depends on them.
    `positional.py` is not among them: 2f is why it looks dead.
15. **Section 11** - the job deadline, recovery, and unfinished jobs not
    publishing.
16. **Doc 005** - the judging change, last, because it is measured against a
    deterministic pass that steps 3-9 have made correct.

## What rule 4 requires

Everything above is diagnostic. The numbers come from `shapes_holdout`, which
`run13` was built against, and from gold, which found several of these - so
neither may score a fix.

Gold's 55 drugs and the shapes holdout's 22 are disjoint, and so are their
issuers, so **gold remains available as an oracle for finding further defects
of this kind** - it is spent only as a scorer for what it found. Step 1 makes
gold's case file cover gold, which widens the oracle; it does not make it a
scorer.

A new held-out set is owed before any of these numbers is reported as an
improvement, drawn from issuers none of `gold`, `holdout`, `holdout2`,
`holdout_labels`, `holdout_members`, `holdout_foreign_xbrl` or `shapes_holdout`
uses. ANI, Perrigo, Collegium, Travere and Amicus all appear in the diagnostics
above and are therefore already spent. The set must contain figures that should
be refused as well as figures that should pass: several fixes above raise the
publish rate, and a set of things to publish cannot catch a fix that publishes
too much - which is 0b restated, and 0b is why the existing gold files cannot
serve.

**Layer 2 has an oracle nobody uses; layer 3 has none.** No eval scores a
route, an approval date, a therapeutic area, a similarity ranking, a peak or an
uptake curve today. But `seed/gold/product_profiles.jsonl` holds 62 products
with the curated route, approval year, MoA class, indication area, era and
intensity - an answer key for section 3 that exists and is wired to nothing.
Use it as a **diagnostic oracle** for finding layer-2 defects, exactly as gold's
quarterly rows were used for layer 1. Then, per rule 4, a fix it found is
scored on a new held-out set of products none of gold's 62 contains - and the
pipeline still must not read the *attribute columns* of
`seed/product_attributes.csv` (`moa_class`, `route_of_administration`,
`first_approval_year`, `indication_area`), which are what build the oracle.
It reads `drug_name` from that file today (`extraction/members.py:259-283
load_products()`), CLAUDE.md names the file an input, and that is fine.

Section 4 has nothing at all and needs a new set outright.
