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

### 0a. The answer key the headline is scored on is 48.6% of gold, missing one issuer

    gold rows          : 2203      gold products : 55
    gold_all.json      : 1415      case products : 39
    in gold, not in gold_all.json:
      Alimta, Basaglar, Cialis, Cyramza, Emgality, Forteo, Humalog, Humulin,
      Jardiance, Mounjaro, Olumiant, Retevmo, Taltz, Trulicity, Verzenio, Zepbound

Every Eli Lilly series - one of gold's seven issuers, the most recently added
and therefore the least exercised. 1,415 was gold's size on 2026-09-08, when
`941d516` wrote `README.md:89` ("1,070 of gold's 1,415 quarters (75.6%)").
Gold was rebuilt on 09-15 by `a69b887`; the case file was not.

The values that *are* in the case file still match gold exactly - 0 mismatches
over all 1,415 - so this is a coverage hole rather than a corruption. But
`grep -rn "gold_all" backend/tests/` returns nothing: no test fails when they
diverge, and the hole grows every time gold grows. `docs/evaluation.md:39`
calls `gold_all.json` "every product-year in gold", which stopped being true on
2026-09-15.

**Do:** rebuild the case file from gold, add a test that fails when the two
diverge, re-run, and replace the README number with its as-of date and gold
size.

### 0b. The eval cannot see a refusal in the two files the headline comes from

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

### 0c. The eval and the pipeline disagree by construction about what a conflict is

`scripts/eval.py:193` takes `spread` over every published value for a period
with no scope filter. The pipeline deliberately keeps scopes apart
(`_scope_key`, `orchestrator.py:167`, merges only Worldwide with Product
family). Measured:

    AYVAKIT 2024Q4  ->  144.1 Worldwide, 124.1 U.S., 20.0 ex-U.S.  (all auto_pass)

The expected answer, 144.1, is present and published. The eval reports
"published, conflicting" and counts it against the score. The disagreement
moves the headline in the direction that looks like a defect.

Three further blindnesses, each verified, each cheap to state and not cheap to
fix: `score()` iterates `case["expect"]` only, so 35 of 73 published
(product, quarter) pairs on run13's finished jobs are never examined; every
`expect` row in `gold_all.json` carries exactly `period`,
`value_normalized_usd_millions`, `gold_id`, so publishing the right number
under the wrong scope scores **correct**; and `score()` runs regardless of job
status, so a job that died mid-reconcile - holding six `auto_pass` rows for one
quarter - moves the headline with nothing marking which rows came from it.

### 0d. Every eval case runs a configuration no user gets, and a test asserts it is what a user types

    foreign_xbrl.json    7  x {"openfda": false, "product_metadata": false}
    gold_all.json      372  x {same}
    gold_sample.json     8  x {same}
    shapes_holdout.json 24  x {same}
    unseen.json         15  x {same}

    domain/models.py:317  product_metadata: bool = True
    domain/models.py:320  openfda: bool = True

The UI sends `options: {}` and gets the defaults. With `product_metadata=False`
`_extract_metadata` returns at `orchestrator.py:709` and `_judge_profile` is
skipped. Across all 19 run databases in the scratchpad, 279 jobs, the only
profile field ever written is `llm_aliases`:

    every run: drug_profile_fields -> [('llm_aliases', N)]

`llm_aliases` is an internal search-term payload, skipped from judgment at
`quality/profile.py:127`. So layer 2 has never produced a field in any measured
run, and the ten empty tables are empty in measurement rather than in
production.

And `backend/tests/test_shapes_holdout_is_held_out.py:130`:

    def test_cases_are_what_a_person_would_type():
        assert case["options"]["openfda"] is False
        assert case["options"]["product_metadata"] is False

A passing test pinning the eval to the opposite of the shipped default, and
naming that configuration "what a person would type".

### 0e. Two env overrides, both the wrong way

    sec_include_8k        default False -> true   (fetches cover pages
                                                   sources.py:10 calls worthless;
                                                   14 of 28 documents in a live repro)
    enable_profile_judge  default True  -> false  (leaves quality/profile.py,
                                                   233 lines, unreached)

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

### 1a. The quarterly chart plots the wrong number when a quarter has more than one scope

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

4 of 99 published (product, period) groups in run13 carry more than one value.

### 1b. Coverage is a row count, and reads 100% on a series with nothing in it

`quality/completeness.py:89`: `quarters = sum(1 for row in quarters_held ...)`
counts **rows**, not distinct periods, and excludes only `rejected` - so
`needs_review` and `corroborates` rows count as coverage. And `gaps` counts
only `unresolved_quarters` rows, which are written only when the issuer filed
nothing at all in the window (`orchestrator.py:1146`: `if covered: return []`).
An issuer that filed but whose quarters the extractor failed to read produces
zero gaps. So "100%" means "of the quarters we noticed were missing, none are
missing."

    AGAMREE    completeness=100.0   published_quarters=5
    ILUVIEN    completeness=100.0   published_quarters=0
    ORLADEYO   completeness= 47.6   published_quarters=8   <- longest clean run in the DB

The number is anti-correlated with the thing it names. It is on the Library
card (`LibraryPage.tsx:258`), the API (`main.py:271,323`) and the product
export sheet. The page headline averages it to "Coverage 65%" for run13.

Compounding: `_published_quarters` (`api/products.py:135`) counts distinct
published `period` of **any** `period_type`, so an annual `2025` or a
`2025Q4`-labelled annual counts as a quarter. It over-reports for 6 of 16
products.

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

### 1d. The deliverable named `quarterly_revenue.csv` is not quarterly

`export/builder.py:267-295` emits `for d in j.datapoints` - every datapoint, no
status filter, no period-type filter - into eight columns with **no
`period_type`**:

    drug_name, period, value_normalized_usd_millions, source_url,
    source_quote, confidence_score, validation_status, revenue_scope

For run13 that is 549 rows of which 31% are not quarterly (74 ytd, 44 annual,
41 nine_month, 12 six_month, 1 unknown), and 20+ (drug, period) keys carry a
quarterly *and* a cumulative figure under the same period string:

    FYCOMPA   2023Q3 -> quarterly 36.393 AND nine_month 98.8
    ILUVIEN   2025Q4 -> quarterly 19.8   AND annual     74.9
    Pombiliti 2024Q3 -> quarterly 21.136 AND nine_month 48.032

Anyone who loads this into Power BI and charts by `period` builds a curve
partly from cumulative figures. Also unfiltered by status: 355 `needs_review`
and 67 `corroborates` rows sit beside the 127 `auto_pass` ones.

(The per-job Excel workbook is the one genuinely traceable export -
`builder.py:121-240`, five sheets including a `Drug Profile` with a
`source_url` per field. The Power BI CSVs are the problem.)

### 1e. The product sheet cites a revenue filing beside characterisation fields

`export/builder.py:44-71` gives the product sheet one `source_url`, filled at
`:108` from `product.get("source_link")`, which `dashboard/series.py:193`
defines as *the first revenue datapoint's* URL. `therapeutic_area`, `moa`,
`pharmacologic_class`, `roa`, `approved_lot` come from openFDA and carry no
citation of their own.

So the README's "citations are mandatory on every source-derived field" holds
for layer 1 and not for layer 2, and the citation the analyst *does* see points
at a document that does not contain the field. Upstream the stored
`source_quote` is the literal string `f"openfda.{field}"` - a JSON path, not a
quote (`orchestrator.py:790`).

### 1f. Dashboard tiles and the Methodology tab assert machinery that never runs

- `dashboard/series.py:264` sums over `peak_products`, always empty, and
  returns `value: 0`; `DashboardPage.tsx:155` renders **"Aggregate selected
  peak $0M, Coverage 0/22"**. A confident zero where "not computed" is true.
- `DashboardPage.tsx:170` states "Launch uptake is a rolling-four-quarter
  revenue proxy divided by the typed selected annual peak" and "Competitive
  intensity uses the stored competitive_intensity_v1 peer cohort". Both
  describe code with no caller. It reads as "your data is thin" rather than
  "this was never built".
- The Launch-relative tab renders an empty chart with a 22-product legend:
  `buildChartData(payload, products, 'launch')` returns 0 rows but
  `names.length` is 22, so the "No analogs match the current filters" branch is
  skipped.
- 32% of published datapoints in run13 come from jobs that never finished -
  `build_dashboard_preview` (`dashboard/series.py:67`) queries every job
  regardless of status, and the mid-reconcile job is exactly where the
  contradictory duplicates are.

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

### 2f. Retrieval is EDGAR-only, and 41% of gold's evidence is not on EDGAR

Gold is 2,203 hand-researched quarters over 8 issuers - the closest thing to a
worked example of the series this product is trying to build. Its sources:

    hosts (distinct URLs)          rows citing that source
    www.sec.gov          266       SEC host      : 1,296 of 2,203
    s203.q4cdn.com        49       non-SEC host  :   907 of 2,203  (41%)
    www.gilead.com        36       a PDF         :   643 of 2,203  (29%)
    www.merck.com          3
    www.investor.jnj.com   1
    ir.unither.com         1

The pipeline retrieves EDGAR and openFDA. It has no path to an investor-
relations site, and 0 PDFs in 2,153 recorded sources.

The clearest case is the one CLAUDE.md rule 1 cites. Gold's pre-acquisition
Actelion quarters - Opsumit, Uptravi and Tracleer from 2016Q1 - come from
`Actelion_Historical_Sales_Schedule.pdf` on J&J's IR CDN, with
`derivation: direct_jnj_retrospective_table`, not from any SEC filing. So the
form-filter fix in 9c is a real rule-1 violation and is **not** what would
unlock that case; nothing in retrieval can reach that document at all.

This is a capability gap, not a defect - but it bounds what fixing sections 5
and 6 can deliver, and it is the reason `positional.py` looks dead.

---

## 3. Layer 2 emits attributes that are wrong, not missing

Layer 3 scores on four attributes. Layer 2 produces none of them under those
names, produces a substitute for one that is wrong for the products where route
is the whole story, and produces an approval date that is absent or wrong for 7
of the 20 seed products.

These are live-path defects: the defaults are on, and they were measured by
running layer 2's own functions against the live API, not by reading a run.

**There is no reference procedure to compare against.**
`scripts/build_independent_gold.py` never calls openFDA, drugsFDA or DailyMed -
`grep -in "openfda|dailymed|drugsfda|api.fda.gov"` over it returns nothing. Its
`read_product_attributes()` (`:2620`) reads `first_approval_year`, `moa_class`,
`route_of_administration` and `indication_area` straight out of the
hand-curated `seed/product_attributes.csv`. So gold never faced 3d's sibling
match or 3e's application ambiguity, because gold never resolved an application
at all. The pipeline's openFDA path is the only automated attempt in this
repository at a job gold did by hand, and nothing scores it.

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

### 5b. A footnote's scope is taken by first regex match anywhere in the note

`labels.py:363 _note_scope` takes the first `_NOTE_PERIOD_RE` hit. On ANI's
10-Q the note reads "no sales of YUTIQ during the quarters ended March 31, 2026
and June 30, 2026 ... as of the second quarter of 2025" - `_NOTE_SPAN_RE` does
not know "quarters ended", so the scanner runs past the claim and binds it to
2025Q2 from a subordinate clause about another product's label. The suppression
fires on the one quarter that was fine and not on the two that should be empty.

Two structural faults: a note naming several periods can return only one, and
"parsed nothing" is returned as "applies to the whole row" (567 of 680 corpus
notes). Fix: bind the period from the clause carrying the claim, return a set,
distinguish "no scope" from "whole row", and widen `_NOTE_SPAN_RE` to
`quarters?/years?/period ended` and hyphenated `N-month period` forms -
"quarters ended" appears in 145 corpus files.

### 5c. `_INCLUDES_RE` fires on "does not include"

`labels.py:414-420` matches claim and product name separately, so "Full year
2026 guidance does not include sales of YUTIQ" returns `names=('YUTIQ',)` and
flags the row combined. Fix: one pattern carrying claim and subject, as
`_no_sales_of` (`labels.py:378`) already does.

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

### 6a. The sentence splitter breaks table rows into "sentences"

`sentences.py:25` splits on `\s*\n+\s*`, and HTML-to-text puts each cell on its
own line, so `"EXONDYS 51\n$\n134,688"` is three sentences and
`value_and_product_in_different_sentences` fires. 326 rows, **212 with no other
objection** - the single largest contributor to the held rate.

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

### 7a. `detect_period_context` dates a filing by a year mentioned once

`periods.py:330` takes the latest year outright. Perrigo's FY2022 10-K is dated
**December 2040** from a single debt-maturity date; ANI's FY2025 as 2027. 15 of
235 datable documents pick a year named once over one named up to 180 times.
That string goes into the LLM extraction prompt and disables `prose.py`'s
forecast guard entirely for those documents. **The correct guard already exists
49 lines above at `periods.py:281`.** One line, highest blast radius in either
pass.

### 7b. `_declared_slack` and `HELD_FOR_BOUND` are dead because one key is dropped

42 of 42 derived rows across three runs are unbounded. The tagged reader emits
`rounding_uncertainty_usd_millions` and `_as_datapoint` reads it, but the
quarter inputs arrive through `_candidate_of`, which drops it (5d). One missing
key kills the bound guard, the "+/- n from input rounding" clause in every
derived quote, and the citation field.

### 7c. 190 six- and nine-month tagged facts are discarded

`xbrl.py:139` returns `None` for any span but 3 or 12 months, so the
span-difference rule can never be fed by tagged facts - only by tables. This is
also why Q4 has zero XBRL readings (2d).

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

### 9a. A module docstring cites a test that does not exist

`extraction/adjudicate.py:29-32` says "`test_no_real_gold_row_needs_review`
runs every quarter in `seed/gold` through here and requires all of them to
resolve. If a change to this file starts flagging real data, that test
fails..." That string appears in the repository exactly once: in the docstring.
A false safety claim in the module whose job is deciding when there is no
answer.

### 9b. Rule 4's own discovery glob misses a holdout set

`backend/tests/answer_keys.py:22`:

    return sorted(REPO.glob("seed/*/*.jsonl")) + sorted(REPO.glob("seed/*/*.json"))

Its docstring: "the keys are discovered from the tree instead, and a set added
later cannot be forgotten here." One was - `seed/holdout_foreign_xbrl.json`
sits **directly** under `seed/`, one level up from the glob: 17 cases, 8
foreign issuers (argenx, BioNTech, Grifols, HUTCHMED, Dr Reddy's, Alvotech,
Indivior, Genmab). Missed twice over: `products_in()` reads
`drug_name`/`member`/`expected`, and that file keys its product as `product`.

A future holdout drawn from those issuers would pass
`test_no_case_comes_from_a_scored_issuer` while reusing a spent issuer - the
exact failure the module was written to close. Fix: `seed/**/*.json{,l}`, and
read `product` too.

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

What *is* established is that the one careful hand-build of comparable data
never needed one. Gold's 2,203 quarters cite 284 accessions across 8 issuers:

    8-K 173   10-Q 75   10-K 34   6-K 1   20-F 1   amendments: 0

So fix these because rule 1 says a filter is part of the claim, not because a
measured series depends on them. The Actelion case that the rule-1 table cites
was solved a different way - see 2f.

### 9d. Four `SCRIPT_ONLY` reasons do not survive a grep

`tests/test_capabilities_are_wired.py` keeps a hand-maintained exception list,
which is fine - but four of its 30 entries carry a reason that is false:

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

A reason nobody re-checks is how a to-do becomes permanent.

### 9e. Stale docs that read as current

| where | claim | what is true |
|---|---|---|
| `README.md:78` | "the five evals" | `docs/evaluation.md:3` - "There is one eval". `1cc1db6` deleted the other eighteen three days after the sentence was written. |
| `README.md:83-87` | the headline "runs none of the twelve stages in `run_job`, so the LLM extractor, the evidence judge and conflict reconciliation are all absent from it ... The same script **without the flag**" | No such flag exists. `eval.py` goes in through `POST /runs` and runs every stage. The README tells the reader the headline excludes the judge and reconciliation; it includes them. |
| `AGENTS.md:15` | "backend `uv run pytest` (23 tests). Frontend has no test suite" | 711 tests; `frontend/package.json:10` has `vitest run` plus two page tests. |
| `docs/sourcing/excluded-products.md:1-7` | "the eight excluded products ... more than half of its own addressable surface" | `excluded_products.jsonl` holds 6; the catalog is 2,203 quarters, so 434:2203 is ~20%. The document's prioritisation conclusion no longer follows from its own arithmetic, and it carries no as-of date. |
| `docs/plan-after-the-full-sweep.md:14` vs `README.md:89` | 1,162/1,415 vs 1,070/1,415 | Two live docs, same denominator, different current score; the newer has the higher number and labels it "before any of the fixes below". |
| `CLAUDE.md:195` | `scripts/eval_*.py` | Only `scripts/eval.py` exists. |

`docs/pipeline.md` has 10 rows; `JobStep` has 13 members and `run_job`
(`orchestrator.py:416-447`) calls 14 things. Omitted: `_search_revenue_fallback`
(`:426`) and `_search_quarters_fallback` (`:436`), both gated on
`enable_llm_search` which is **`True` by default**; `_record_unfiled_quarters`;
`_record_quarters_only_reported_with_another_product`. And `pipeline.md`
contradicts itself on the bulk-tagged reader - `:141` says it is off unless
configured, `:163` says "`app/` calls all four". `config.py:41` defaults
`notes_dataset_dirs = ""`, so one of the five readers is off by default and
contributes nothing to any measured number.

Everything else spot-checked in `pipeline.md` holds (`PDF_COLUMN_GAP = 1.5`,
the 0.7 confidence gate, `EARNINGS_ITEM = "2.02"`, every EX-99 rather than the
first, `SOURCE_PRIORITY`/`CLAIM_STRENGTH` as two axes).

### 9f. Tests that pass on data the pipeline cannot produce

No assertion-free tests exist - hygiene is good. The failure mode present is
the other one. `tests/test_export.py:18-44` constructs `CanonicalProductORM`,
`MoAComponentORM` and `PeakSalesEstimateORM` by hand and asserts the exporter
reads them back (`assert row["peak_type"] == "consensus"`). It proves a SELECT
works over rows the pipeline cannot write. Same for `test_peak_sales.py`,
`test_uptake_metrics.py`, `test_competitive_intensity.py` (which also asserts
the meaningless distribution, 4a) and `test_analog_matching.py`.

### 9g. Holdout sets: which are spent, which are orphaned

    holdout/               referenced by nothing in the repository
    holdout2/              referenced by nothing in the repository
    holdout_labels         tests/test_product_disambiguation_holdout.py, CLAUDE.md
    holdout_members        scripts/eval.py, tests, CLAUDE.md  -- live, guarded
    holdout_foreign_xbrl   scripts/sourcing/build_foreign_xbrl_holdout.py only

`seed/holdout/` and `seed/holdout2/` are 280 KB of spent, orphaned cases -
CLAUDE.md names them, no code does. `seed/holdout_labels/product_labels.json`
**has become a test fixture**: `test_product_disambiguation_holdout.py:19`
loads it into the suite, so it is re-scored on every `pytest` run and is a
permanent tuning target. Rule 4 says stop tuning before the set is exhausted; a
set in the suite can never stop being tuned against. It is also the only
holdout with no `test_no_case_comes_from_a_scored_issuer` guard - the property
currently holds, checked by hand, but nothing checks it.

No key has become a pipeline input: `test_no_pipeline_input_carries_gold_evidence`
passes and is not vacuous, and `scripts/audit_gold.py` runs clean (2,203
quarterly and 75 annual rows, 0 findings). One gap:
`test_gold_is_not_an_input.py:132` reads only
`seed/gold/quarterly_revenue.jsonl`, so an input carrying URLs or quotes copied
from `annual_revenue.jsonl` (75 rows), `product_profiles.jsonl` (62),
`peak_sales.jsonl` (12) or `adjudication_cases.jsonl` (13) passes the
value-level check. That is CLAUDE.md's own rule-1 table row, "three answer-key
filenames".

Four of gold's answer keys are scored by nothing at all: `annual_revenue`,
`product_profiles`, `peak_sales`, `series_coverage`, `adjudication_cases`.

### 9h. `check_by_hand.py` verifies the weakest of the three things a citation claims

It runs. What it does not check: **the stored quote** (`source_quote` appears
nowhere in it - it searches for the *value* within +/-400 characters of any
>=4-letter word of the drug name, so a fabricated quote passes as long as the
number sits near the name); **the period** (`check_instance(raw, drug, None,
...)` at `:181` passes `period_start=None`, so the context-period filter at
`:94` never runs and a tagged fact for the wrong quarter passes); and annual
figures (`:178` skips them). Also `float(d.get("value_reported") or 0)` makes a
missing value search for 0.0, and it hardcodes a personal contact address as
the SEC user agent instead of reading `SEC_USER_AGENT`.

Period attribution is the defect class `docs/research/sec-table-period-context.md`
exists for, and the by-hand checker is blind to it.

### 9i. Three run options the API accepts and nothing reads

`pdfs`, `random_validation_sampling`, `use_uploaded_template`
(`domain/models.py:326,329,330`) have zero reads in `app/`. Sampling is always
on at `validation_sample_rate`. They are persisted into every run's
`options_json`, so a caller reading a run back believes PDF parsing or sampling
was toggled when it was not.

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
- `seed/holdout/` and `seed/holdout2/` (9g).

Decide rather than leave:

- `positional.py` (116 lines) was on this list for "0 PDFs in 2,153 recorded
  sources and 0 in the 553-file cache". That is a fact about retrieval, not
  about the reader - see 2f. **643 of gold's 2,203 quarterly rows cite a PDF.**
  The reader has no input because nothing fetches its input. Deleting it would
  remove the only thing able to read 29% of the evidence a careful hand-build
  used.
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
    Then turn `openfda` and `product_metadata` on and re-measure. **0b's profile
    judge is measured separately, after this**, because only now does it have
    fields to judge.
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
    9c's `8-K/A` and `10-K/A` filters are a retrieval change and carry a
    measurement, so they go with step 4 rather than here.
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

**Layers 2 and 3 need their own sets, and have none.** No existing key scores a
route, an approval date, a therapeutic area, a similarity ranking, a peak or an
uptake curve. Section 3's fixes can be scored against the curated columns in
`seed/product_attributes.csv` only if those columns stop being what
`build_independent_gold.py` reads to build `product_profiles.jsonl` - otherwise
it is the answer key wearing a different hat. Section 4's need a new set
outright.
