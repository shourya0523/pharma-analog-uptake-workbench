# Plan: what the full sweep found, and the order to fix it in

Written after the first full run of the pipeline through the API on every
product-year in gold (372 runs, 1,415 quarters) and on a fresh unseen set
(15 quarters), with every published figure checked by hand against its cited
document. Branch `claude/gold-dataset-gaps-rd67js`; the run's own numbers are
in the commit messages from `47b6ba5` to `7261eed`, and this file exists so
the next session does not have to rediscover them.

The measured state at the end of the sweep, before any of the fixes below
and after the ones already pushed (the SQLite lock, EDGAR retry, period
dating, tagged-name veto, milestone veto):

    gold      1,162 / 1,415 correct   98 wrong   63 conflicting   83 no answer   9 held
    unseen       10 /    15 correct    0 wrong    1 conflicting    2 no answer   2 held
    by hand   3,589 published figures fetched; 3 were wrong publications

Everything below is a scored change. Under rule 4 none of it may be measured
on gold or on `seed/cases/unseen.json` - both found these defects. Item 0
comes first for that reason.

---

## How to run what this plan refers to

    cd backend && MAX_CONCURRENT_JOBS=6 DATABASE_URL="sqlite:////tmp/run.db" \
        ./.venv/bin/uvicorn app.main:app --port 8000
    python scripts/eval.py --cases seed/cases/<set>.json [--attach]
    python scripts/check_by_hand.py --run <run_id>

`--attach` rejoins runs already on the server. A job that failed is
re-run as a fresh run of only those cases; read the outputs together.
`docs/evaluation.md` has the detail. Keep concurrency at six or below: the
lock diagnostic (`write_lock_held_by` in the log) names any job that holds a
write across an await - if it ever fires, that is a defect to fix before
anything else.

Standing rules, from `CLAUDE.md`, that every item below is written to obey:
derive the list (rule 1); a claim about data needs the command (rule 2);
nothing the pipeline reads may come from gold (rule 3); measure on a set the
change was not built from (rule 4); comments explain the code, with invented
names - Calderon, Calderon XR, Nebulized Calderon, NuVessa, `acme:`, `beta:`
(rule 5). Test by running the pipeline as a user, through the API, and
validate published values by hand; no harnesses.

---

## 0. Draw and freeze the next held-out set

Before touching a reader. Issuers none of the answer keys use (`seed/gold`,
`seed/holdout*`, `seed/cases/unseen.json`); the guard test
`backend/tests/test_combined_name_holdout_is_held_out.py` is the shape to
copy. Choose by *shape*, not brand - the set must contain at least one of
each of the following, read by hand from the printed filing into
`seed/cases/<name>.json` with the citing URL:

- a row label with a footnote that says the line includes another product
- a row label that names several brands joined by `/`
- a `Product - Region` table with a labelled `Total` row, and one whose total
  row is unlabelled
- an issuer that both tags a figure (thousands) and prints it (one decimal)
- a release whose table headings are written `2Q 2026` / `June YTD 2026`
  rather than "three months ended"
- a table whose product name sits on its own row above `US / Intl / WW` rows
- a prose-only release stating an increase ("sales increased by $X")
- a filing whose text names a cost or a milestone beside the product
- a 52/53-week filer (quarter ending April 1 / January 3) and an annual
  report - to score the period fixes already pushed
- a filer whose recent 8-Ks carry inline-XBRL cover pages
- a product owned by a non-SEC filer for part of the window
- at least two quarters that must come back empty

Both answers represented; about twenty product-years. Then stop tuning
before it is exhausted, and leave what still fails documented.

---

## 1. A row label the reader cannot account for is a question, not a formulation

The largest open group. `backend/app/parsing/tables.py` (`_matches_product`,
`_scope_for`, `extract_revenue_rows`) and the multi-row reader in
`backend/app/extraction/extract.py`.

What it produced on gold: a litigation accrual for bictegravir published as
Biktarvy 2021Q4 (`Accrual for settlement related to bictegravir litigation
(1) 1,250`); an inventory row (`Remodulin: Raw materials`); UTHR's combined
`Tyvaso ®(1)` line published as Nebulized Tyvaso *and* as Tyvaso DPI (7
quarters); `Lynozyfic - Global` made a "formulation" that never reconciled
with its tagged twin; `INVEGA SUSTENNA / XEPLION / INVEGA TRINZA / TREVICTA`
rejected as naming a competitor in years J&J spelled TRINZA with its prefix
(10 quarters); Merck's `Alliance Revenue - Adempas` and `Adempas (6)` as two
rows of the same product.

Mechanism. Compute the label's residue: strip the alias that matched, the
scope tokens in `SCOPE_PATTERNS`, `Total`, trademark marks, footnote markers,
and the names of the other products the pipeline tracks (`load_products()`
plus the run's own drugs - inputs, never the key). Then:

- residue empty: the product's own row; scope from the geography token or
  family. `Lynozyfic - Global` becomes family and meets the tagged fact.
- residue is only sibling names joined by `/`: a combined line of the named
  products; scope `Product family (combined: ...)`, published for the asked
  product as the combination it is, never rejected as a competitor.
- residue is anything else: candidate carries `label_not_understood`, is
  never auto-passed, and the judge is shown the residue words.
- a footnote marker is read, not stripped: the `(n)` text under the table
  (the parser has captions; footnotes follow the table as lines beginning
  `(n)`). A footnote naming a sibling product ("includes Tyvaso DPI") makes
  the row a combined line as above, and it cannot be the sibling's own. A
  footnote saying the period is partial ("since the acquisition date") makes
  the value `partial period`, never the quarter.

Tests, invented names: `Calderon (1)` with footnote "(1) includes Nebulized
Calderon" is family-combined and not Nebulized Calderon's; `Calderon: Raw
materials` is held; `Calderon / Calderon XR / NuVessa` is a combined line;
`Calderon - Global` reconciles with `acme:CalderonMember`.

## 2. One figure, one publication

`backend/app/pipeline/orchestrator.py`, `_reconcile_with_llm` and the
equal-strength rule. Gilead tags 427.623 (thousands) and prints 427.6; both
were published, 17 quarters. Extend the within-declared-precision test across
tiers: a lower-tier claim agreeing with the winner within the coarser of the
two declared precisions (`_declared_slack`) becomes `corroborates` - cited,
not published. Test: `acme:CalderonMember = 427,623,000` beside a table row
`Calderon 427.6` publishes once with two citations; `427.6` against `431.0`
still conflicts.

## 3. `Product - Region` tables, and a column mapping the table itself refutes

Two related table defects.

(a) Region rows. Gilead 2021 prints `Descovy - U.S. / - Europe / - Other
International` and then an unlabelled total; Regeneron prints `Libtayo -
U.S. / - ROW / Total Libtayo - Global`. Region rows take their region as
scope and are never the family figure. The family figure is the row labelled
`Total <product>`, or, when the total row has no label, the row directly
after the block whose values equal the block's column sums within rounding -
the table proving which row is its total. Region rows publish only when no
total exists, flagged as regions. On gold: 10 quarters; on unseen: Libtayo
and Praluent.

(b) The mapping. J&J's Q4 exhibit puts `OPSUMIT` on its own row above `US /
Intl / WW` rows; the grid mapping placed the quarter's values in columns
9-11 while the figures sit in columns 3 and 6, so the reader recorded
`several_lines_no_total` and read nothing - while the annual table beside
it, where the columns happened to align, was read. Before a mapping is used,
check it against the rows: value columns that hold no figures on any product
row while other columns do means the headings were mis-covered; fall back to
the phrase-and-years path, and note it. Repro: run `read_tables` on
`https://www.sec.gov/Archives/edgar/data/200406/000020040621000006/a2020q4exhibit992.htm`
for Opsumit and print `skipped_reason`.

## 4. Table headings written in quarter notation

`backend/app/extraction/fingerprint.py` `_named_periods`/`_periods_named_in`
recognise "three months ended" and "Second Quarter 2024" and nothing else.
Merck heads its product table `2Q 2024 | 2Q 2023 | % Change` under `Global |
U.S. | International`, and its year-to-date table `June YTD 2024`; the table
is then dated from the document, which - because the comparative `2Q 2023`
is named more often - was dated **2023**. Adempas and Winrevair lost every
quarter that only that table states (7 on gold).

Mechanism: one vocabulary for period spellings. `backend/app/parsing/periods.py`
already has `_QUARTER_FORMS` (`Q2 2024`, `2Q 2024`, `second quarter 2024`)
for documents; the heading parser should read the same forms, plus `<Month>
YTD <year>` as a span ending in that month. Geography column groups above a
period row are scope, read from the grid's covering headings. And the
document-level rule that picks the year for quarter notation must prefer the
quarter the document reports over the comparative it names more often - the
same reasoning `detect_period_context` already applies to "months ended".

## 5. Retrieval budgets that silently exclude filings

Two written-down numbers in `backend/app/config.py` decided what the pipeline
could read, which is rule 1's failure in its most expensive form.

(a) The XBRL instance budget. Since every 8-K carries an inline-XBRL cover
page, `isXBRL` is true for cover pages, DEF 14As, S-8 filing-fee exhibits and
11-Ks; the budget fills with them and the 10-Qs are never fetched. Measured
on the sweep's database: 205 of 430 jobs stopped exactly at the budget; 788
of the instances fetched were 8-K cover pages; 29 jobs held no 10-Q instance
at all. This is why Erleada 2019 Q2-Q4, Adempas 2025 and others have no
tagged figure although the issuer tagged one. Derive the budget from the
window instead: one instance per fiscal period the window covers, and an
instance that carries only `dei:` facts is not a periodic report and does not
count - the SEC's own `primaryDocDescription` and form say which filings
are periodic, and the instance's own element namespaces say whether it holds
financial facts.

(b) The earnings-exhibit budget of six, taken newest-first, drops the oldest
quarter of a thirteen-month window whenever an issuer files other 8-Ks with
EX-99s (J&J does; Simponi 2015Q3's release was never fetched). Same
derivation: one earnings 8-K per quarter in the window.

(c) `over_source_budget` on the LLM step is a budget too; keep it, but the
deterministic readers must never be starved by it - check they are not.

## 6. Prose grounding: the value and the product in the same sentence

`backend/app/quality/fast_judge.py` passed a 573-character block of a
release's headline bullets as the quote for Yutrepia 2025Q1 = 100.0: the
product is in one bullet, "$100 million" (a financing facility) in another.
Published auto_pass. The same header-blob quotes produced company totals
(Invega Sustenna 23,300; Simponi 17,400; Adempas 15,500) that the judge
happened to hold. Rule: a quote is a sentence; a multi-sentence quote is
split and the sentence carrying the value must also carry the product, or
the candidate is not grounded and cannot auto-pass. Plus two language-level
vetoes beside the milestone one in `apply_judge_hard_vetoes`, snapshot
comments as there: an amount governed by *increased/decreased … by* with no
*to $X* in the sentence is a change, not a level (Orenitram, Remodulin,
Tyvaso DPI 2026Q1); `cost of (product )?sales|cost of goods|cost of revenue`
joins `NON_PRODUCT_REVENUE_RE` (Yutrepia 2025Q2/Q3).

## 7. A filing that contradicts itself publishes nothing

UTHR's 2019 10-Q tags Adcirca 2018Q1 as 52.2m and prints 97.6 in the same
filing; the tagged fact won on tier and the printed figure was held as a
conflict it had not lost. Extend the equal-strength hold to key on
**accession**: two claims from one filing for one period that disagree
beyond declared precision are both `needs_review` with
`filing_contradicts_itself`. The accession is already in every citation.
This is the only defence against a filer's tagging error; the earlier
Orenitram 2018Q1 case was the same shape.

## 8. Derive every quarter two published totals determine

`derived_from_period_total` does H1 - Q2 -> Q1. Add the rest of the closed set
over the spans the pipeline recognises: Q3 = 9M - H1, Q4 = FY - 9M,
Q4 = FY - Q1 - Q2 - Q3, with precision propagated through `_declared_slack`
so a derived figure states what it is worth and is held when the slack
exceeds the tolerance. Remodulin 2002-2007 (14 quarters) is where nothing
else will ever help: six-month and annual tables only.

## 9. Who filed for this product in this window

Tracleer, Uptravi and Opsumit 2016 were Actelion's, a Swiss filer with no
SEC filings; J&J's Q2 2017 release shows two weeks after the June 16 close.
Fifteen gold quarters. There is no SEC figure to have, so the fix is to say
so: when the given manufacturer's filings cover none of the window, ask EDGAR
full-text search who mentioned the brand in it; a US filer found is
retrieved, none found becomes an explicit unresolved reason - *no SEC filer
of record for this product in this window*. Partial-period footnotes are
item 1's footnote reader. Ask the user whether gold's Actelion quarters should
stay in the answer key at all; that is a definition, not a defect.

## 10. Review-queue reason prose served beside its producer

From the frontend session: `REASON_HELP` in the review queue is prose keyed
by the reasons `backend/app/validation/sampling.py` attaches - a
hand-maintained mirror of a producer, rule 1's shape, commented as a snapshot
for now. Serve the prose from the queue endpoint next to the producer and
delete the frontend copy. Small, and it touches `sampling.py`, so do it as
its own commit.

## 11. The eval should score a re-run itself

`scripts/eval.py` scores what it is given; folding a failed job's fresh run
into the sweep's score was done by hand this time (a scratch merge script).
Add it to the eval: a case whose job failed is scored from the newest
finished run for that drug and window, and the summary says how many were.
No `app` imports, as before.

---

## Order and expected yield

Item 0 first. Then 1, 2, 3 - one file's worth of table logic covering about
fifty gold quarters and every wrong publication the hand-check found. Then 5
(retrieval budgets), which is cheap and lifts many "no answer" quarters
before any reader changes are scored. Then 4 and 6 as small parsers and
vetoes; 7 and 8 next; 9, 10 and 11 last. Measure each on the item-0 set,
re-run the hand-check on every run, and put the numbers in the commit
message - not in a comment.

Open definitional questions to put to the user before scoring: Actelion's
pre-acquisition quarters (item 9); whether a combined `Tyvaso` line is
Nebulized Tyvaso's figure or nobody's; whether Gilead's rows printed under
generic names (`Ledipasvir/Sofosbuvir`) are the brand's figure.
