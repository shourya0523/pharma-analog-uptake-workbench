---
title: "plan: what twelve reviews found, and the order to fix it in"
date: 2026-09-17
type: plan
status: in-progress
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

## How to read this now

An item reading

    Shipped -> `<sha>`, <what that commit did>

has been fixed, and the commit is where its evidence now lives: what was
measured, before and after, and the probe. The body that was here said what was
wrong at the time it was written, which the commit message says better and
keeps current. An item that still carries its body is open, or shipped under a
commit that does not name it - `git log -S` on the symbol is the way to tell,
and this index is not evidence that anything is unfinished.

The items indexed are the ones a commit subject names:

    git log --format=%s 744864d..HEAD | grep -oE '^[0-9]+[a-z]' | sort -u

plus section 8, which shipped under its own name (`2a1cf0d`). Sections 4, 9,
10, 11 and 12, and "What rule 4 requires", are whole.

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

Shipped -> `87c9d47`, the eval cases ask for the configuration the product ships

### 0e. Ten env overrides, all session-only - and nothing measured is reproducible  `[V]`, fixed in M0

Shipped -> `8943dbc`, ten overrides, not four, and M0 has landed

### 0f. The product's own example file has never been run  `[V]`

Across all 18 run databases in the scratchpad, every job is drawn from the
same 24-product rare-disease set (FILSPARI, ORLADEYO, NUPLAZID, ELEVIDYS,
Galafold, ...). `select ... where lower(drug_name) in ('opsumit','uptravi',
'tracleer','veletri','ventavis')` returns nothing in any of them. **No run
has ever processed a product in `seed/example_drugs.csv`** - the PAH catalog
that `_product-brief.md` says is the analyst's, and that gold's competitive
intensity is computed for. Every claim in this document about what the
pipeline does to the analyst's own products is inferred from a different
issuer set. 2g is the first consequence found.

**Nothing below is scored until 0a-0d are done, and nothing about the PAH
catalog is known until it has been run.**

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

### 1c. A two-product line is published as one product's revenue, because the alias step disarms the guard  `[V]`, site found

Shipped -> `33d3b01`, a slash between two different names is a pair, not two spellings

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

**Every count in this section is on a spent set.** run13's 22 job names are
the 22 products of `seed/cases/shapes_holdout.json`, the file drawn to score
the previous sweep's fixes. None of these numbers may score a fix (rule 4),
and `shapes_holdout` is missing from 9g's inventory of spent sets - that gap
is this section's doing.

### 2a. There is no selected figure per quarter  `[V]`

Shipped -> `21d20a0`, a quarter of a series has one figure, and the series says which

### 2b. The scope label is noise, not a scope  `[V]`, cost lower than ranked

Shipped -> `21d20a0`, a quarter of a series has one figure, and the series says which
Shipped -> `e19f2e5`, the uptake scope test compares places, not punctuation

### 2c. Two series swapped at a switch-over, and nothing that could notice  `[V]`, corrected, ranked first in this section

Shipped -> `21d20a0`, a quarter of a series has one figure, and the series says which

### 2d. The holes land in the ramp, not the tail  `[V]`, first class smaller at the surface

Shipped -> `2e7ae9a`, a series says where it ends, and what a partial quarter is

### 2e. Life events are detected and then dropped  `[V]`, two attributions corrected

Shipped -> `21d20a0`, a quarter of a series has one figure, and the series says which
Shipped -> `2e7ae9a`, a series says where it ends, and what a partial quarter is

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

    J&J 3Q2021   8-K 0000200406-21-000067  items 2.02,7.01,9.01
                 a2021q3exhibit992.htm  prints  OPSUMIT  US 299  Intl 159  WW 458
                                        under THIRD QUARTER 2021 / 2020, $ millions
    Gilead 3Q22  8-K 0000882095-22-000022  items 2.02,9.01
                 exhibit991earningspressrel.htm prints  Biktarvy - U.S. $ 2,286 $ 1,875
                                        under Three Months Ended September 30, 2022 2021

(An earlier draft's evidence for this was a list of brand names found in each
exhibit - the name test the protocol forbids. The figures above are printed
beside the label, which is the test that answers the claim. 1Q2020 and 2Q18
were also checked; the quarters above are the second ones.)

So gold cited the IR copy because it is the readable one, not because EDGAR
lacked it, and **no investor-relations fallback is needed for the bulk of it**.
The existing SEC path reaches both issuers today.

**The Actelion bridge is genuinely IR-only, and it is small.** The 8-K/A that
CLAUDE.md rule 1's table names is real and reachable, but it is not the source
of the quarters:

    8-K/A 0000200406-17-000046  filed 2017-08-29  items 2.01,9.01
      exhibit991actelionfinancia.htm  (1.2 MB, 51 tables through html_tables)
      -> 0 table rows, raw or parsed, name any Actelion product; all 13
         product-name windows containing a digit are narrative (milestones,
         royalty rates, licence dates, inventory)
      -> "Consolidated Income Statement - Twelve months ended December 31, (in
         CHF thousands)": Net revenue / Product sales 23 / 2,412,198 - one line
      -> 137 mentions of CHF, 0 "six months", 0 "June 30", 0 "$"
      exhibit992actelionproformas.htm (466 KB, not opened by an earlier draft)
      -> the only product-labelled numeric row is a VALCHLOR & Ponesimod
         contingent-consideration liability in USD millions; not revenue

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

### 2g. The EDGAR copy 2f relies on is not readable by the table path  `[V]`, end-to-end `[I]`

2f retired the investor-relations question on the grounds that J&J's product
schedule is on EDGAR. It is, with figures. **The pipeline's table reader
cannot attach them to the product.** J&J prints the product as a row-group
header and puts the figures on rows labelled by geography:

    OPSUMIT
      US       299   244   22.8
      Intl     159   148    7.4
      WW       458   392   17.0

    read_label("OPSUMIT", ["Opsumit","macitentan"])   -> matched='Opsumit'
    read_label("US" | "Intl" | "WW", ...)             -> matched=None,
                                                         scope='United States' | 'International' | 'Worldwide'
    parsing.tables.extract_revenue_rows(tables, product="Opsumit", ...) -> 0 rows
    (also 0 for Uptravi and Tracleer; re-fetched live, 22 tables, table 13 rows 15-22)

An earlier draft wrote `-> None` for the geography rows. `read_label` reads
their *scope* correctly; only the product is missing - so the row-group
heading is the sole missing piece and the scope it would need is already in
hand.

`extraction/fingerprint.py:450 _covering` walks *left* for column headings
(`column -= 1`); nothing anywhere in `app/` walks *up* for a row-group heading
(`grep row_group|group_header|section_label|row_header|heading_row|group_label`:
no hits). `parsing/tables.py:73 _row_labels` collects sibling first cells only
to feed `names_a_competing_product`. So the `carries`
branch of 12b's predicate - "a figure whose row label resolves to P" - holds
for row-labelled tables and does not hold for row-grouped ones. The gap is in
`parsing/`, not in the cascade.

Whether the prose or model path recovers Opsumit from this exhibit is
**unmeasured and unmeasurable from existing output**, because of 0f. Opsumit,
Uptravi and Tracleer are 4 of the 20 rows in `seed/example_drugs.csv` and the
centre of the PAH analog set; their quarterly figures are on EDGAR, free,
under item 2.02, already fetched by the existing path - and the table reader
returns nothing for them. Existence is not extraction. This outranks 9c and
outranks 2f as previously written.

---

## 3. Layer 2 emits attributes that are wrong, not missing

Layer 3 scores on four attributes. Layer 2 produces none of them under those
names, produces a substitute for one that is wrong for the products where route
is the whole story, and produces an approval date that is absent or wrong for 7
of the 20 seed products.

These are live-path defects: the defaults are on. **All 18 run databases have
zero openFDA sources and have written exactly one profile field ever,
`llm_aliases`** - not just no PAH product (0f), no characterisation run at
all. Every claim below is measured from exactly two sources: layer 2's own
functions called against the live API, and `jr/meta.db`, a reviewer's live run
of Tyvaso and Opsumit with both options on - the only run in this repository
that has ever exercised layer 2. openFDA result order is not guaranteed, so
which *application* is selected (3d's sibling, 3e) can differ between
sessions; which *field path* is read (3a, 3b, 3c) cannot.

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
a ready-made oracle for most of what layer 2 should produce** - 62 products
carrying `route_of_administration`, `first_approval_year`, `moa_class`,
`indication_area` and `approval_era`, declaring `attribute_provenance:
curated_reference`, and `competitive_intensity_at_launch` for the 20 in the
PAH catalog only (null on the other 42, by design - see the gold README). An
earlier draft said "each carrying six"; it is 62 x 5 + 20 x 1, and a scorer
that treats the 42 nulls as failures is wrong. Two more things about the
oracle: `moa_class` carries both `sglt2_inhibition` and `sglt2_inhibitor`, two
spellings of one class inside the curated file; and `first_approval_year` is
not always the FDA year - Veletri is curated `2010`, drugsFDA `NDA022260` has
exactly one ORIG/AP submission, `2008-06-27`, and the pipeline reads it
correctly. Where they disagree the oracle can be the one that is wrong. It is
currently scored by nothing. Scoring the openFDA path against it is the right direction under rule
3 - reference data flows into gold, and gold scores the pipeline - and it makes
3a-3h measurable rather than anecdotal. What the pipeline must not do is *read*
`seed/product_attributes.csv`, because that file is what builds this oracle.

A first score already exists and is in 3a: over the 20 seed products,
`openfda.route` is wrong against the curated value for 5 and absent for 3.

### 3a. Route is read from a field that contradicts the same document  `[V]`

Shipped -> `5b173f6`, read an openFDA record by the keys the record carries
Shipped -> `168c4c0`, read a product's fields from the application it launched on

### 3b. `openfda.dosage_form` does not exist  `[V]`, one sub-claim false

Shipped -> `5b173f6`, read an openFDA record by the keys the record carries

### 3c. A whole class of products is invisible, including the oldest analogs  `[V]`, run13 count corrected

Shipped -> `5b173f6`, read an openFDA record by the keys the record carries

### 3d. The substring fallback attaches a sibling's application and its approval date  `[V]` mechanism, `[I]` examples

Shipped -> `4cb5fe4`, the molecule exclusion compares against the molecule the record declares

### 3e. Among a brand's own applications, the first returned wins  `[V]`, order-dependent, 5 brands not 1

Shipped -> `1fc8e6d`, date the product from every application it matched, and cite the key read
Shipped -> `168c4c0`, read a product's fields from the application it launched on

### 3f. `fda_approval_date` is always empty, exactly when resolution succeeds  `[V]`, ranked too low

Shipped -> `cf5fbb2`, write the launch anchor, from the record that carries it

### 3g. There is no launch anchor in the database at all  `[V]`

Shipped -> `cf5fbb2`, write the launch anchor, from the record that carries it

### 3h. `therapeutic_area` is a verbatim copy of `indication`, so nothing groups  `[V]`

Shipped -> `6621e13`, group an indication under the disease it names

### 3i. `moa_class`, `approval_era` and `competitive_intensity_at_launch` have no producer  `[V]`

Shipped -> `b69741e`, the judging order is a judgement; its membership is derived

### 3j. Six of seven openFDA citations point at field paths that do not exist  `[V]`

Shipped -> `1fc8e6d`, date the product from every application it matched, and cite the key read

---

## 4. Layer 3's method is unsound independently of the wiring

The unwiredness is declared - `tests/test_capabilities_are_wired.py:43` lists
the analytics layer under `SCRIPT_ONLY` as "open work", and 13 of the product
sheet's 28 columns are structurally always null. What is not declared is that
the method is wrong in four places, three of which produce a confident number
rather than a refusal.

**So: do not wire this layer to fix the blank columns.** A blank column costs
the analyst less than a confident wrong one.

### 4a. Competitive intensity is a rank within the batch, not a property of the market  `[V]`

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
crowded over thirty years (peer count 0,1,2,3,4,5,6,6,6,8,9,9,9,12,13,14,14,
16,17,18 for 1995..2025; gold's rule reproduces gold's label on all 20 with
zero mismatches). The percentile method produces **7 low / 6 medium / 7 high**
on the same 20 - state it as that rather than "~1/3", so a fix can be scored
against a number. Filter for the 9/20: the 20 gold rows with a non-null
intensity label, which are exactly the PAH catalog (null on the other 42 by
design); two reconstructions - a registry from gold's rows, and gold's own
peer counts injected as `raw_score` - both give 9/20, and the 11
disagreements are a contiguous block from Remodulin (2002) to Orenitram
(2013): the method agrees only where rank coincides with the extremes.
Veletri's curated year (3e) does not move it.

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

### 4b. The observed peak can be confidently wrong in three shapes and silently absent in a fourth  `[V]`

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
  issuers routinely publish both. Run: `[2020=300, 2021=400, 2021=400,
  2022=200]` returns `observed 400`; the same series with the duplicate
  removed returns `None`. The duplicate row *is* the confirming later year -
  `later = [2021-dup, 2022]` and `later[1] <= later[0]` passes. Gold's rule
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

### 4c. Similarity's stated defence against unknown attributes is inverted  `[V]`, two wordings corrected

`analytics/analog_matching.py:8-15` says scoring `None` as similar "would
quietly promote products we know least about", so unknowns are dropped from the
denominator. Dropping them from the denominator promotes those products too,
and less visibly - a candidate cannot lose credit on an attribute it never
declared. Over the 62 gold profiles:

    TARGET Revatio    Cialis   0.8889 n=3 unknown=[intensity]  <- #1
                      Adcirca  0.8750 n=4 unknown=[]  (moa, route AND era match)
    TARGET Winrevair  Mounjaro 0.5556 n=3 unknown=[intensity]  <- #1
                      Zepbound 0.5556 n=3 unknown=[intensity]  <- #2 (tie)
                      Emgality, Taltz 0.4444 n=3
                      Liqrev   0.4167 n=4 unknown=[]           <- 5th

Revatio's best analog is an erectile-dysfunction drug; Winrevair's are two
mass-market GLP-1s. Both win by not knowing something (both blocks reproduce
to four decimals over the 62 gold profiles, verbatim fields). A candidate
known only by route and era, both matching, scores **1.0** on two attributes
and outranks every four-attribute match **that is not itself exactly 1.0** -
on an exact tie the sort key's depth term does break it in the four-attribute
candidate's favour. An earlier draft said "outranks the genuine
four-attribute match" absolutely and "depth never rescues it"; a fix's
acceptance test written to that wording would be wrong. And this shape is
**not reachable from layer 2's real output today**: with the literal
attribute names layer 2 supplies 1 of 4, `score_analog` gives 1.0 on one
attribute, and `rank_analogs(minimum_attributes=2)` returns `[]` - silently,
no `AnalogMatch`, no reason. The analyst gets an empty set, not a wrong one.
It is reachable from curated profiles or from 3a's generous mapping.

`minimum_attributes: int = 2` is documented as "what stops a single coincidental
agreement from ranking above a genuine four-attribute match" - two coincidental
agreements out of two known is still coincidental, and the sort key at `:147`
is `(-score, -attributes_compared, candidate)` with score primary, so depth
rescues only an exact tie. `_similarity("route_of_administration", "ORAL",
"Oral")` is `0.0` - on an otherwise-identical pair the case difference gives
`score=0.75, attributes_compared=4, attributes_unknown=[]`, a confident zero
counted in the denominator.

**`indication_area` is in the data and is not scored.** Both
`seed/product_attributes.csv` and `seed/gold/product_profiles.jsonl` carry it;
`ProductProfile` has no such field. Nothing in the similarity model knows what
disease a product treats - the first thing any analyst filters on.

Smaller: `rank_analogs:142` silently drops candidates below
`minimum_attributes`, so the analyst never learns one was considered;
`_era_start("Pre-2000")` returns 2000, so an unbounded pre-1999 bucket earns
the same half-credit against `2000-2004` whether the approval was 1998 or 1982
(*left as is at implementation (M8)* - `_era_start` still reads the head of
the label; the era attribute is now one of five and `initial_approval_date`
carries the year, so the bucket's half-credit is bounded by weight, not fixed);
and `_similarity` compares with `==` case-sensitively, so `ORAL != Oral` scores
a confident **0.0**, not an unknown - fixing route correctness without
normalisation buys nothing.

### 4d. Uptake divides by a number whose scope it never checks  `[V]`

`analytics/uptake.py:61` takes `selected_annual_peak: float | None` and `:110`
`selected_peak: float` - currency, geography and revenue scope discarded at
the call boundary; `_has_compatible_scope` checks the four rows against *each
other* and nothing checks them against the denominator. U.S. quarters over a
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

(Distinct from 2a: that is `rows[index-3:index+1]` over a duplicated series;
everything here was run on one-row-per-quarter fixtures. The two compose
badly - 2a's duplicates are what makes `nonconsecutive_quarters` fire on
consecutive quarters - but they are separate defects in separate lines.)

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

### 4e. Three competitive-intensity methods, none reconciled  `[V]`; the LLM module's refusals `[I]`

`competitive_intensity_v1` (rank-banded, 4a), gold's
`marketed_peer_count_at_launch_v1` (absolute, gated, the sound one), and
`llm_competitive_intensity_v1`. They agree 9/20 where two can be compared, and
nothing computes that disagreement - `compare_to_rule`
(`competitive_intensity_llm.py:159`) takes `rule_label` as an argument and has
no caller in `app/`. Stronger: `grep -rn "app\.analytics" backend/app/`
outside `app/analytics/` returns nothing - no `app/` module imports the
analytics package at all, so nothing in 4a-4e has a production caller.

The LLM module is the most disciplined code in the layer: it refuses on
`inconclusive`, on an out-of-vocabulary label or confidence, and - the good one
- when the model cites a peer that is not on the roster it was given
(`:134-146`). It is the only one of the three that returns a reason instead of
a value when it cannot answer, and it is the one with no path to the product.
(Its refusal *behaviour* is `[I]` - read from the source and its unit tests,
not run, since it needs a model.)

**Rule 3, corrected.** An earlier draft ended "`seed/product_attributes.csv`
is the correct source" for `peers_at_launch` (`:175`). It is not: that
file's attribute columns are what `scripts/build_independent_gold.py:2653-2664`
uses to build every field of `product_profiles.jsonl`, and `peers_at_launch`'s
dict keys are exactly that column set. Reading them from `app/` would make
the oracle a pipeline input - 3i and 3j say so. The correct source is a
*procedure* that derives class, era and indication area; no file in the
repository may be it.

### 4f. There is no eval for any of this  `[V]`

`find . -name "eval_*.py"` returns nothing; `scripts/eval.py` is layer 1 only.
Every held-out set is about revenue extraction, printed row labels or XBRL
members. So there is no number, held-out or otherwise, for similarity, peak
selection, uptake or intensity. Under rule 4 that is not a shortfall to fill
with an existing set - those are spent - but a new set drawn from issuers none
of them use, with both a crowded and an empty market represented.
`seed/gold/product_profiles.jsonl` cannot be the scorer for 4a or 4c: it is
the oracle those rules would be fitted to, and it is built from
`seed/product_attributes.csv`, so neither file can serve.

**Rule 4 - three tests assert a section-4 defect as the spec.**
`test_competitive_intensity.py:49` asserts `count("low") == 2`, `count("high")
== 2` and `not any(item.low_coverage)` - 4a's forced distribution and its
inverted flag, written down as the expected result; a fix fails it by
construction. `test_analog_matching.py:55-71` asserts `score == 1.0` with
`attributes_unknown == ['competitive_intensity_at_launch']` under a docstring
saying the opposite is what an analog search should avoid - the Revatio ->
Cialis profile, scoring 1.0-on-three and winning. `test_analog_matching.py:84-92`
("one coincidental agreement must not outrank a real four-way match") uses a
*one*-attribute thin candidate and so tests the `>= 2` arithmetic, not the
property claimed; the two-attribute case is uncovered. And `:95,108` are the
only real-data analytics tests and they load gold - `:108`'s `assert ranked`
for every profile passes only because gold carries 3-4 attributes; layer 2's
real output would fail it.

**Rule 1.** `analog_matching.py:28-33 WEIGHTS` and `:35 _INTENSITY_ORDER` are
four names and four weights hardcoded with `ProductProfile`'s field list as
the producer three lines below - `score_analog` iterates `WEIGHTS`, so adding
`indication_area` to the profile silently leaves it unscored, which is 4c's
last paragraph as a rule-1 failure. `competitive_intensity.py:7-12 WEIGHTS`
and the cut-points `2`/`5` (`:122-125`) and `33`/`67` (`:144-146`) are bare
literals with no snapshot note, unconnected to gold's `0-1/2-4/5+`.
`peak_sales.py:13-18 ALLOWED_PRODUCT_SCOPES` is the good shape - enum members
with a reason - and is the one `time_to_ninety_percent_peak` forgets to use.

**One shape, three costumes.** 4a, 4c and 4d are the same failure: **an
absence is silently converted into a value.** Zero registry entries becomes
`low`; an unknown attribute becomes a smaller denominator and a higher score;
a missing scope becomes a clean ratio. Gold gates all three
(`not_assessed_outside_catalog_universe`, `peak_eligible: false`,
`benchmark_identity`). The code has no gate of any kind: `CompetitiveSnapshot`
has no refusal field, `AnalogMatch.reason` is set only for `same_product` and
`no_comparable_attributes`, and `UptakePoint.missing_reason` - the layer's one
refusal channel - has its precedence wrong.

---

## 5. Wrong figures published

From the first pass, kept. These break "never publish a figure that is not this
product's own" and "publish it with a quote a person can check".

### 5a. Derived rows cite a document that does not contain their input  `[V]`, harder than stated

Shipped -> `1aba1c2`, a derivation says what it subtracted, and is cited where it was read

### 5b. A footnote's scope is taken by first regex match anywhere in the note  `[V]`

Shipped -> `b257270`, bind a footnote's scope to the clause that makes the claim, and to every period it names

### 5c. `_INCLUDES_RE` fires on "does not include"  `[V]`

Shipped -> `26b96ca`, one pattern carries the "includes" claim and its subject

### 5d. Derivation launders provenance at four sites  `[V]`

Shipped -> `1aba1c2`, a derivation says what it subtracted, and is cited where it was read

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

### 5f. The combined-line demotion is overwritten  `[V]`, provably

Shipped -> `aece086`, a row held for what its label said is not published by what its quote says

---

## 6. Correct figures withheld

20 of 75 expected figures were found, correct, and not published. Each cause is
a bug rather than caution.

### 6a. The sentence splitter breaks table rows into "sentences"  `[V]`

Shipped -> `cbc7ed6`, a table row printed cell-per-line is one unit, not one per cell

### 6b. `quote_states_a_different_period` cannot match a non-quarterly key, and reads only one period  `[V]`, cause changed

Shipped -> `ff97066`, one period namespace on both sides of the judge's period checks

### 6c. `ytd_language_as_quarterly` reads the whole quote - and cannot be fixed before 6a  `[V]`, cost changed

Shipped -> `ff97066`, one period namespace on both sides of the judge's period checks

### 6d. `filing_contradicts_itself` holds the right figure beside a wrong one - but the tier axis is not what separates them  `[V]` on code and test; `[U]` on the scores

Shipped -> `11253f9`, a sign is read under the total it belongs to

### 6e. A corroborator is never promoted when the winner is held  `[V]`, two shapes

Shipped -> `2fc055b`, the group is looked at again, in both directions

### 6f. `conflicting_values` rejects both sides, by period  `[V]`, one detail false, magnitude larger

Shipped -> `ca4c79b`, a check rejects the cell it was about, and only rows that made a claim

### 6g. SOURCE_PRIORITY ranks tagged facts below a model reading the same filing  `[V]`, and the non-revenue leak is located

Shipped -> `b68671c`, what produced a reading ranks before where the reading sits

### 6h. Reconciliation groups by period label and ignores `period_type`  `[V]`

Shipped -> `ae403fd`, the span a figure covers is part of what a group is a group of

---

## 7. Signal computed and discarded

### 7a. `detect_period_context` dates a filing by a year mentioned once  `[V]`, re-measured

Shipped -> `1221ac0`, date a document by the period it names throughout, not the latest one

### 7b. `_declared_slack` and `HELD_FOR_BOUND` are dead because one key is dropped  `[V]`

Shipped -> `1aba1c2`, a derivation says what it subtracted, and is cited where it was read

### 7c. 75 six- and nine-month tagged facts are discarded  `[V]`, re-measured

Shipped -> `36dbe13`, a six- or nine-month tagged fact is a period, not a None

### 7d. The judge is blind to what the pipeline knows  `[V]`, ranked too low

Shipped -> `05498fc`, the judge is shown the row, the filing's own product list, and the footnote

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

Shipped -> `2a1cf0d`, read the label before asking EDGAR whose filings to download

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
  `sources.py:197`, built for exactly this, and is not used here.
- `sources.py:289,832`: `PRIMARY = {"10-K","10-Q","20-F","40-F"}` /
  `SECONDARY = {"6-K","8-K"}`, applied as `if form not in allowed`. **1,308
  filings** in the cache are forms the module's own `reports_a_period()`
  accepts and this set drops - including 317 `10-K/A` and 188 `10-Q/A`, the
  amendments that carry restated financials.

Both counts re-run over the 280 cached submissions indices (269 CIKs,
241,226 filing rows, `filings.recent` only - so lower bounds): 108 and 1,308,
exact. But **762 of the 1,308 are the 11-K family** (753 `11-K`, 8 `11-K/A`,
1 `11-KT`) - employee-benefit-plan reports, in the count only because
`ANNUAL_FORMS` (`sources.py:49`) admits them. The pharma-relevant remainder is
546.

`test_retrieval_is_bounded_by_the_window.py:61-65` asserts
`reports_a_period("10-Q/A")` and `("10-K/A")` are True - "`8-K/A` is the shape
that started this rule" - while `sources.py:832` drops both. The derived
helper and the live literal contradict each other and no test exercises
`:832`. `test_earnings_sources.py:76` asserts `EARNINGS_ITEM == "2.02"`, which
pins the literal rather than covering the filter. A fourth literal sits at
`sources.py:833`: `pri = {"10-K":0,"20-F":0,"40-F":0,"10-Q":1,"6-K":2,"8-K":3}
.get(form, 5)`, keyed on the raw form so every amendment sorts last.

**The open question is answered, and it narrows the fix.** 34 filings were
opened - every filing either filter drops, for every issuer in
`seed/example_drugs.csv` or in a run database, excluding the 11-K family.
Four carry a product-level revenue figure for a period, printed beside the
label:

    Travere 8-K/A 0001438533-25-000030  2.02,9.01   FILSPARI $ 55,881 / $ 19,834
                                                     Three Months Ended March 31, 2025 / 2024
    Amicus  8-K/A 0001104659-22-057365  2.02        "Global revenue for Galafold in the first
                                                     quarter of 2022 was $78.7 million"
    Travere 10-Q/A 0001438533-18-000022             Thiola 19,924 / 17,884, 3m to 2018-03-31
    Travere 10-K/A 0001438533-18-000020             Thiola $82,311 / $71,199 / $54,923

**In all four, a filing the filters keep carries the same figure.** The
Travere 10-Q filed the same day carries `FILSPARI $ 55,881 $ 19,834`; the two
2018 amendments say in their own note they were filed "solely to correct a
clerical error in Exhibits 32.1 and 32.2" and their revenue tables are
identical to the original's; Amicus filed an original item-2.02 8-K the same
day. Over the corpus, all 108 `8-K/A` item-2.02 filings have an `8-K`
item-2.02 by the same filer within 120 days (97 within 30). **Zero of 34
carried a figure no kept filing carries.** The filters cost a corroborating
second reading, not a quarter - and 6e/2d show corroborators are discarded
anyway.

(One shape worth recording, out of scope: Indivior `6-K/A`
`0001625297-25-000009` prints `SUBLOCADE 194 176 756 630` for Q4/FY 2024/2023
- a foreign issuer files no Q4 10-Q, so a 6-K/A is the natural home for its
quarterly split. It too has a same-day original 6-K.)

Gold's own rows cite no amendment. Predicate matters: accessions parsed from
`source_url` in `quarterly_revenue.jsonl` alone give 267 across 6 issuers;
with `annual_revenue.jsonl` too, 285 across 9. Either way:

    8-K 173   10-Q 76   10-K 34   6-K 1   20-F 1   amendments: 0

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

## 10. Delete or decide  `[V]`, and half the list came off it

Every candidate re-checked against the tree after M0, M2 and M9 landed, with
callers reported three ways - `app/`, `scripts/`, `backend/tests/` - and any
route. Three counts an earlier draft gave carried predicates it did not state
and do not reproduce: "0 PDFs in 2,153 sources" (1 of 4,956 over 18
databases, a Deciphera press-release PDF that parsed and found nothing), "9
of 10 `llm_search` sources are sec.gov across five runs" (50 of 91 over 10
runs; the 9/10 is run12+run13 only), "10 of 22 hold mechanisms never fire"
(9 of 22, and the 22 was never named - it is the 12 `QualityIssue` types, the
8 `hard_veto:*` labels and the 2 gate clauses).

**Delete - no caller in `app/`, `scripts/` or a route, and no effect:**

- `_search_revenue_fallback` (`orchestrator.py:1112`): ran on **20 jobs**
  across 9 databases, not twice, and produced 0 datapoints in all 18.
- `ValidationTaskORM.issues / judge_status / deterministic_results`: 0 of
  1,914 rows over 18 databases; the sole constructor (`orchestrator.py:2593`)
  omits all three and nothing reads them. `db/migrations.py:93-94` lists them
  in `BASELINE_001_COLUMNS`, so a drop needs a migration. The review queue's
  374 rows carry a `reason` string and nothing else - the three columns built
  to say *why* a row is held are the ones never filled (9h's absence, in the
  schema).
- Two orphan prompts, not one: `lot_extractor.yaml` (referenced nowhere) and
  `competitive_intensity_assessor.yaml` (referenced only by the unwired
  `analytics/competitive_intensity_llm.py:29`) - 14 `load_prompt` names in
  `app/`, 16 files.
- `Settings.llm_search_max_queries` (`config.py:54`): never read - and
  documented as an operator knob in `deploy/env.example:61` and
  `backend/.env.example:31` beside `LLM_SEARCH_MAX_URLS`, which works. An
  operator capping search volume gets no cap and no error.
- `FileStore.public_uri`: only its three definitions. Its docstring is
  "Logical URI for audit" - the affordance the citation promise would use.
- `tables.py`'s `extract_revenue_rows` half: `app/` imports only
  `clean_label`; eight functions reachable only from `test_tables.py` and
  `test_periods.py`. Its `_scope_for` duplicates `candidates.py:38` minus a
  `.strip()`.
- ~~`ExtractionOptions.pdfs`, `random_validation_sampling`,
  `use_uploaded_template`~~ - **gone**, M9 removed them; confirmed.

**Struck - the earlier draft was wrong:**

- ~~`_search_quarters_fallback`, `_quarters_no_filing_covers`,
  `_record_unfiled_quarters`, `NO_FILER_OF_RECORD`, the `LLM_SEARCH` branch~~
  - **no.** "Never ran in six runs" is false and contradicts 12c's own
  correction. Over 18 databases the search path yielded 5 datapoints, all in
  run3, all via `_search_quarters_fallback` (flag
  `llm_search_quarters_fallback`), for **FYCOMPA - an acquired product,
  Eisai to Catalyst**: 2024Q4 38.2 and 2023Q4 39.3 `auto_pass` from press
  releases, plus 5 `[no_filer_of_record]` unresolved rows telling the
  reviewer in words that the gap is an ownership change. run13 has the same
  product, window and issuer, 0 published for those quarters, and does not
  contain 2023Q4 or 2024Q4 at all - the path is dormant there only because
  retrieval now finds 30 in-window sources instead of 4, so
  `_quarters_no_filing_covers` returns `[]`. And `NO_FILER_OF_RECORD` has a
  **live consumer**: `api/products.py:69` maps it to the queue prose an
  analyst reads. This is one mechanism, it is the acquired-product bridge the
  brief's analyst needs most, and it has a demonstrated yield. The
  `SourceType.LLM_SEARCH` branch at `:2157` fired on 2 of the 5 search-sourced
  rows; the other 3 never reached the judge and auto-passed - a search-found
  figure can publish without that branch seeing it, which is a defect in the
  branch's reach, not a reason to delete it.
- ~~`seed/holdout/` and `seed/holdout2/`~~ - **no** (9g). Re-verified: exactly
  8 issuers sole-sourced there. Residual: their `products.csv` files are not
  reached by the `.json{,l}` glob; a future `.csv`-only key would be invisible
  to `spent_issuers()` - 9b's shape.
- **`judge_with_search`** was never a candidate: 368 `llm_search_validated`
  rows in run13 (exact), 1,484 over 18 databases.

**The hold mechanisms, named.** Never fired in 18 databases - 9:
`missing_source_url` (`checks.py:88`), `missing_source_quote` (`:98`),
`annual_classified_as_quarterly` (`:129`), `company_total_as_product`
(`:139`), `negative_revenue` (`:150`), `unclear_currency` (`:160`),
`hard_veto:change_not_level` (`client.py:818`),
`hard_veto:company_total_without_product` (`:823`),
`hard_veto:other_brand` (`:826`). Fired once, in one database:
`unclear_unit` (run6), `missing_formulation` (run2b). **In run13 alone only 6
of 22 fire.** The 4 unreachable, each with the upstream site that makes it so
(`filter_revenue_candidates`, `candidate_filters.py:267`, runs before the
judge on both paths): `missing_source_quote` <- `:296-297`;
`hard_veto:company_total_without_product` <- `:333-334`, the
character-identical expression; `hard_veto:other_brand` <- `:329-330`, same
`quote_mentions_other_brand`; `company_total_as_product` <- `:313/334/342`
and `apply_auto_pass_gate:225`. Four checks that look like defence in depth
are one check written twice; a change to the upstream predicate silently
removes the "second" one. No wrong figure reaches the analyst through them.

**Decide rather than leave:**

- **`positional.py` (116 lines) - the delete reason was wrong, and it
  inverted.** The premise holds: no PDF arrives (1 of 4,956; 0 in the SEC
  cache). But 2f + 2g established that J&J's product schedule is on EDGAR and
  the table path cannot attach its figures because the product is a row-group
  header over geography rows. `read_positional_block` is the only code in the
  repository that reads that shape - run on 2f's own printed figures it
  returns `PositionalRow(scope='United States', values=(299, 244, 22.8))`,
  `International`, `Worldwide`. It is dead *as wired* - its input is
  flattened PDF text - and what it knows is not. The item is "the reader is
  attached to the wrong input", and J&J is 770 of gold's 2,203 rows. Deleting
  it also falsifies `parsing/evidence.py:35-36`. **Rule 5:** its docstring at
  `:14-16` asserts "issuer product-sales exhibits are mostly PDFs" - a data
  claim, in a docstring, that 2f says is wrong.
- **`adjudicate.py` (256 lines)** has no caller anywhere (`orchestrator.py:1687`
  is prose). `seed/gold/adjudication_cases.jsonl` (13 lines) is replayed at
  `test_extraction_stack.py:705`, and `real_rows_that_trip()` (`:1348`) runs
  all of gold through `adjudicate_total_against_parts` as its false-positive
  guard - so **both sides of its only score come from the answer key**. Legal
  for a test (rule 3), but it means the thresholds are tuned on gold and have
  no held-out number. It is a **rule-4 dependency**, not a wiring toss-up:
  wire it only with a new set. (`test_extraction_stack.py:1377-1380` carries
  "it caught it here first" - rule 5.)
- **`analytics/` (846 lines, 5 modules) - do not delete, and it is
  mis-filed.** Four tables, not three, are 0 rows in all 19 databases:
  `competitive_snapshots`, `peak_sales_estimates`, `uptake_metrics`,
  `analog_families`. No `app/` importer, no script, no route; 7 test files;
  `test_capabilities_are_wired` now marks all 11 entry points `NOT_WIRED`.
  `export/builder.py:57-107` already writes seven product-sheet columns out of
  those tables. The brief says layers 2 and 3 *are* the product; this is all
  of layer 3, and it is the one unbuilt thing on this list whose absence the
  analyst can name - they open Export and find the columns blank. Section 4
  fixes the method first; section 3 fixes what feeds it; neither is an
  argument for deletion.

---

## 11. Infrastructure  `[V]`

- **No per-job deadline.** `grep -rn "wait_for|asyncio.timeout|TimeoutError|
  timeout" app/pipeline app/jobs app/main.py` returns zero hits.
  `jobs/handler.py:22` awaits `run_job` bare, and `jobs/queue.py:48-51` holds
  the semaphore permit for the whole handler, so a hung job holds it forever.
  726s is exact: `TRANSPORT_ATTEMPTS = 3` (`llm/client.py:46`) x `timeout=240`
  (`:692`) + sleeps 2 + 4; httpx's timeout is per operation, not wall clock.
- **Startup recovery throws away finished work.** `main.py:106-137
  recover_stranded_jobs`, `:129` sets `FAILED` with "server restarted while
  this job was running". run13's FIRDAPSE: `current_step='completeness'` -
  the **12th of 13** stages by execution order (the `JobStep` enum declares
  `COMPLETENESS` before `VALIDATION_TASKS`, which is not the order they run),
  with only the status flip left; 28 candidates, 7 `auto_pass`, 14
  `needs_review`, 16 open tasks, 8 quality checks, and a clean 2022Q1-2023Q4
  series - marked `failed`, `completeness_pct 0.0`. `_completeness`
  (`orchestrator.py:2604-2700`) adds only `UnresolvedQuarterORM`, never a
  datapoint, so re-running it cannot double-publish. The other three failed
  jobs died at `identity_resolve` or `source_retrieve` with 0 candidates.
  **Cost:** the Library says the product failed while its seven figures are on
  the Dashboard (1f), and the one missing quarter of that series, 2023Q2, sits
  in the same database as a clean `xbrl_fact` corroborator at 64.898 with two
  readings agreeing - the hole in the ramp is in the database, twice.
- **Unfinished jobs publish.** run13 published rows by `job.status`:
  `ready_for_review` 86, `running` 34, `failed` 7 = 127; 41/127 = 32.3%. The 7
  `failed` rows are FIRDAPSE's entire series.
- **Write amplification** - `[V]` on the call sites, `[I]` on the physical
  count. One row is inserted at `orchestrator.py:1357` (or `:1990`) and
  committed at `:2060`; updated and committed again at `:2282` (`_judge`),
  `:2515` (`_reconcile_with_llm`) and `:2602` (`_quality_and_validation`).
  Four commits per row on a sync `create_engine` (`db/models.py:548`) called
  from `async def` with no `run_in_executor` anywhere in `app/`, so every
  commit blocks the shared loop; WAL (`:594`) softens readers, not the
  single-writer lock. What would verify the count: a `before_cursor_execute`
  hook over one real job.
- **`fetch_page` skips a throttle that exists ten lines away.**
  `sources.py:982-1005`: no `_sec_throttle`, no retry, and it branches on
  `"sec.gov" in url.lower()` at `:995` to set a header - it knows where it is
  going. `SECConnector._get_with_retry` awaits `_sec_throttle()` at `:373`
  with the adaptive pace. Two fetchers against one host, one polite.
  `fetch_page`'s callers: `llm_search.py:138` and `ManualURLConnector`
  (`:1018`). run13's 5 `llm_search` sources are 5/5 `sec.gov/Archives` URLs,
  all through `fetch_page`. The guard test, `test_earnings_sources.py:306`:
  a hand-written tuple of four names (rule 1), `getattr(..., None)` so a
  rename passes silently, and only `vars(SECConnector)` so module-level
  functions are invisible; its prose says "the three reads" over four names.
  The derived version - every function in `sources.py` whose source contains
  `client.get(` - returns `_get_with_retry` (the throttled wrapper) and
  `fetch_page` (the one the list cannot see).
- **Review is one click per row.** Six write routes in the whole API;
  `POST /validation-tasks/{task_id}/actions` is now `main.py:541`, takes one
  id, closes one task. `ReviewPage.tsx:158-160` has three buttons per row and
  no selection state. run13: 374 `validation_tasks`, all `open`, across 19
  products. `validation_action` sets one datapoint's status and never looks at
  sibling rows in the same `(job, period, scope)` group, so confirming both
  the Worldwide and ex-U.S. AYVAKIT rows publishes both.
- **The Export page needs a UUID typed by hand.** `ExportPage.tsx:5` reads
  `localStorage.getItem('lastRunId') || ''`; `:39` is a free-text "Job ID"
  input, button `disabled={!jobId}`; no picker. A fresh browser renders
  `Run: none` and a disabled button.

**Rule 5, this section:** `parsing/evidence.py:40-41` "as the four issuers
whose schedules were read spell them" - a past run in a comment.

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

`sources.py:822-826` justifies the window with a count from a named eval -
"across two shapes-holdout runs those fetches produced four figures, three of
them already read..." - in a comment, naming a holdout set in application
code (rule 5).

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
  `_search_revenue_fallback` (`orchestrator.py:1108`, flag
  `llm_search_revenue_fallback`) and has produced **0 datapoints in all 18
  run databases** - 91 `llm_search` source rows, 5 datapoints from them, all
  5 on the separate unfiled-quarters path (`:434`) in run3. An earlier draft
  said "two runs". Tier 5 is justified by 58 rows of gold, all Actelion/J&J. Both are the
  narrow tail; neither is the reason to build this.

### 12d. What it is worth, and what would show it

Unknown, and that is the point of stating it as a design. What can be said:

- The predicate is worth something on its own, before any cascade, because it
  turns "we fetched 28 documents" into "14 of them carried nothing", which is
  measurable and currently is not.
- Tier 4 has no quarter behind it: 34 dropped filings opened, 0 carry a
  figure no kept filing carries (9c). Tier 5 has 58 rows of gold behind it.
  Tiers 2 and 3 are already reached today.
- **The predicate's premises are verified on a live filing** - `read_label`
  on `FILSPARI`, `fingerprint.column_periods` returning three-month periods
  ending March 2025/2024, `quarters_reported_in(window)` producing `Q` - with
  one gap: the `carries` branch does not hold for row-grouped tables (2g). The
  predicate should be built and measured on a row-grouped document before the
  cascade is ordered around it. And `Q` must come from the run window, never
  from gold's `unresolved_quarters.jsonl` (rule 3).
- So the honest expectation is that this section improves the *tail* - thin
  issuers, acquired products, foreign filers - and does nothing for the median
  case, where sections 5 and 6 show the pipeline already holds figures it
  refuses to publish.

### 12e. M11: choose filings by the quarters asked, not by recency and count

**Status: DESIGN, premises verified, two corrected.** Written after the first smoke run
(`docs/plans/2026-09-17-008-what-the-audit-found.md` section 7) showed where
the fifteen no-answers on the held-out set came from.

**What the picker does today.** Inside the widened window, `sources.py`
sorts the index rows annual-first, then newest-first, and takes the first
`sec_max_filings` (`reading_order` at `:339`, the cap at `:967`). It never
asks which quarters remain unanswered. With the shipped default of 4, every
held-out job fetched three 10-Ks and the newest 10-Q (`select drug_name,
sum(source_type='sec_filing') from drug_jobs join source_documents ... group
by 1` on the smoke database: 4 for all 14 jobs). The 10-Q pages for 2024 and
2025 were never fetched; their XBRL instances were, and for an issuer that
does not tag product revenue they carry nothing (`grep -ci ingrezza
nbix-2024*_htm.xml` -> 0, 0, 0). Raising the cap to 25 - which every measured
run did - works only because it sweeps in the 10-Q that answers each quarter.

**What the index already knows and the code does not read.** EDGAR's
submissions feed carries a period of report on every row; `grep -n reportDate
app/connectors/sources.py` returns nothing. Live, for one issuer:

    ('10-Q', reportDate 2026-06-30, filed 2026-07-31)
    ('10-Q', reportDate 2026-03-31, filed 2026-05-05)
    ('10-K', reportDate 2025-12-31, filed 2026-02-11)
    ('8-K',  reportDate 2026-05-05, filed 2026-05-05)   # earnings 8-K, same day as the 10-Q

Verified over the full index for two issuers: every 10-K, 10-Q and 8-K row
carries a `reportDate`, and `_filings_covering` already has it in `recent`.
**Corrected:** an item 2.02 8-K's `reportDate` is the event date, equal to
the filing date on all 16 rows sampled - never the period reported. The 8-K
answer set cannot come from `reportDate`; it comes from pairing the 8-K with
the periodic filing beside it in time, which is the prose above and not what
"an answer set from the period of report" implies. And a 10-K's product table
states three fiscal years, not two; Q4 by subtraction needs the Q3 10-Q's
nine-month column as a second document.

**The mechanism.** Ask per quarter, not per job.

1. *Each form has an answer set that needs no document opened.* A 10-Q for
   period P states P and the prior-year P as its comparative; a 10-K for FY
   states FY and the prior FY, and Q4 by subtraction; an 8-K item 2.02 exhibit
   filed within a reporting lag of a 10-Q or 10-K states that same period.
   The set is a rule over `form_family` and `reportDate`, derived, not a
   literal per form.
2. *Pick the smallest set of index rows whose answer sets cover the asked
   quarters.* A twelve-quarter window is four 10-Qs and two 10-Ks, chosen,
   instead of four documents, unchosen. The count cap becomes a coverage
   target; `sec_max_filings` survives only as a ceiling per quarter.
3. *Verify, then cascade per quarter.* After parsing, the coverage predicate
   (12b, wired at `_parse`) says per quarter whether the document carried
   the figure. For any quarter still uncovered, fetch the next candidate for
   that quarter only, in 12c's tier order: the earnings exhibit, the following
   year's 10-Q comparative column, the IR page. Stop when every asked quarter
   is carried or every candidate is spent, and record which.

**What has to be true first, both verified on the smoke database (12
jobs, not 14 as an earlier draft said).**
- The coverage verdict is `names_only` on every 10-K page and every XBRL
  instance, structurally, and `partial` on 40 of 244 documents - not on all,
  as an earlier draft said. Three causes, none of them the row-grouped case
  12d named: the asked set holds only quarters, so a 10-K's annual columns
  can never be members; `coverage.py` passes a row as its own sibling into
  `read_label`, so a labelled row reads as a combined line over itself (the
  guard `extract.py:620-625` has and says why); and 97 of 244 documents are
  XBRL instances that parse to zero grids. Measured: giving the predicate the
  extractor's two guards and the annual keys moves `partial` 40 -> 51 -> 71.
  `absent` occurs 0 times in 330 documents because the alias list carries a
  bare corporate suffix. Nothing reads the verdict today.
- Neurocrine, Eton and, on its older releases, Zevra got 0 earnings exhibits
  because `is_earnings_exhibit` (`sources.py:263`) matches a filename pattern
  (`ex99`) that their filing agents do not use (`q4-2025xearningsrelease.htm`,
  `ex_889836.htm`). The item 2.02 gate passes for all of them. The filing's
  own header page declares each document's type (`EX-99.1`), at the same
  request cost as the index call the pass already makes. The exhibit pass is
  also handed the un-widened window while the primary pass widens by the
  reporting lag.

**What the design changes, on the fifteen.** Traced per no-answer against
the document gold cites: 7 are 10-Q pages never fetched whose accession the
job already fetched as an instance with nothing found - the set cover fixes
them at no extra request, and the smallest safe first step is "fetch the
page of an accession you are already fetching"; 5 are exhibits the filename
rule rejected; 1 is an acquired-business 8-K/A (tier 4); 2 are not retrieval.
Only one of the fifteen is the FY-minus-nine-months class.

**How to measure it.** 12d's two numbers, reported as totals, never as a
mean of per-job ratios (four jobs answer zero quarters). Baseline on the
held-out run: 0.16 quarters answered per document fetched, 6.10 documents
per quarter answered. A change that lowers the second without lowering the
first is working. Rule 4:
scored on a set drawn from issuers none of the keys use; the 2026-09 held-out
set has been read against this diagnosis and is not that set.

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

**run13's 22 job names are identical to the 22 products in
`seed/cases/shapes_holdout.json`. run13 is a run of that holdout.** Every
number in sections 1, 2, 5, 6 and 7 measured on run13 - and the 6f
re-measurement that used `shapes_holdout` as its oracle - is on a set that is
already spent. Gold's 55 drugs and the shapes holdout's 22 are disjoint, and
so are their issuers, so **gold remains available as an oracle for finding
further defects of this kind** - it is spent only as a scorer for what it
found. Step 1 makes
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
