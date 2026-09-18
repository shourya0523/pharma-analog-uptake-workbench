
# Verification pass: F-A, F-B, F-C (read-only; no file in the repo was changed)

Scratch scripts and copies: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/verify-retrieval/` (`fa_filter.py`, `fa_docs.py`, `fa_fig.py`, `fb_repro.py`, `fb_dbg.py`, `fb_variants.py`, `fc_noans2.py`, `fc_fetched.py`, `fc_ratio.py`, `fc_10q.py`, plus `smoke.db` = a `cp` of the live DB with its `-wal`/`-shm`). Branch head read: `a736ac38` ("Register 12e: M11, choose filings by the quarters asked"); the four files another agent is editing were read with `git show HEAD:`.

---

## 1. Items whose meaning changed (report these first)

**1.1 F-B is false as stated.** "The coverage verdict read `names_only` on every document of all 14 held-out jobs" — it did not. Over the three smoke runs, **40 of 244 documents carry `partial`** (28 earnings releases, 12 10-K/10-Q pages), and over the whole database **64 of 330**.

```
sqlite: select source_type, json_extract(metadata_json,'$.coverage.verdict'), count(*)
        from source_documents join drug_jobs ... where run_id in (3f25070a…, c61b3799…, 440c853b…)
→ annual_report/names_only 25 | earnings_release/names_only 52 | earnings_release/partial 28
  openfda/names_only 16 | openfda/unreadable 3 | quarterly_report/names_only 72
  sec_filing/names_only 36 | sec_filing/partial 12                    (244 documents)
```
The true statement is narrower and sharper: **every 10-K page is `names_only`, and every XBRL instance is `names_only`, structurally, whatever they hold.** The register's own example survives (nbix-20251231.htm n=9, nbix-20241231.htm n=7, tmb-20251231x10k.htm n=7/6/3/1 are all `names_only`); the universal quantifier does not.

**1.2 The held-out run is 12 jobs, not 14.** `select run_id, count(*) from drug_jobs group by 1` → 5 (NBIX×3, EBS×2) + 6 (PTCT×4, ZVRA×2) + 1 (ETON) = 12, each with exactly 4 `sec_filing` rows. "88 expected figures across 11 scored cases" is consistent with 12 cases minus the failed MIPLYFFA job, so the case count is right and the job count in both `008 §7` and `12e` is wrong.

**1.3 The cause named for F-A ("inferred: the exhibit filter") is wrong at the item filter and right one layer down.** The item-2.02 filter passes for both issuers; the **exhibit filename rule** rejects every exhibit they file. See F-A below.

**1.4 The register's "two OLPRUVA Q4s and INCRELEX 2023Q4 are the FY-minus-nine-months class" is false for the OLPRUVA half.** In `seed/cases/holdout_2026_09.json`, only INCRELEX 2023Q4 carries a `derivation` ("year ended December 31, 2023 18,739 less nine months ended September 30, 2023 13,860 = 4,879"). Both OLPRUVA Q4s are cited to 8-K EX-99.1 earnings releases with no derivation — they are the exhibit-name class (F-A), not the FY-minus-nine-months class.

**1.5 `12e`'s premise 2 is wrong for the 8-K family.** An item-2.02 8-K's `reportDate` is the **event date (= the filing date in all 16 rows sampled)**, never the fiscal period reported. A set cover keyed on `reportDate` alone maps an earnings 8-K to the wrong quarter; it has to be paired with the periodic filing beside it, which is what the design's prose says but not what "an answer set from the period of report" implies.

**1.6 `12a`'s rule-5 citation is stale at HEAD.** `grep -rn "shapes-holdout\|shapes_holdout" backend/app/` returns nothing; the comment at the old `sources.py:822-826` that quoted an eval result has been rewritten. The rule-5 violation 12a names no longer exists in `sources.py`.

**1.7 The coverage verdict is still written and read by nothing.** `grep -rn coverage backend/app --include=*.py` shows `record_coverage` called once (`orchestrator.py:996`) and no reader anywhere. M11 step 3 proposes to route retrieval on a value that currently has no consumer and, per F-B, no signal on 40% of the corpus.

---

## 2. Findings

### F-A — Neurocrine and Eton got 0 earnings exhibits  `[U] → [V], cause changed`

**status_before:** inferred ("the exhibit filter; verify before touching").
**status_after:** `[V]` verified — it is the **exhibit filename rule**, `is_earnings_exhibit` at `/home/user/pharma-analog-uptake-workbench/backend/app/connectors/sources.py:263-278`. Not the item filter, not the window, not the issuers.

The filter as it stands, in order (`sources.py:660-685`): `form_family(form) == "8-K"` → `states_item(items, "2.02")` → `since <= filingDate <= until` (the **un-widened** window) → `_list_filing_documents` → `is_earnings_exhibit(name)`, whose test is `re.search(r"ex+(?:h(?:ibit)?)?9{2}", squashed_filename)`.

Live submissions index, applied stage by stage (`fa_filter.py`, `fa_docs.py`):

```
NBIX (CIK 914475), window 2024-04-01..2026-03-31:
  8-K family rows 96, in window 19; with item 2.02: 40 total, 8 in window   → item filter PASSES
  every one of the 8 filings' exhibit is named  q1-2024xearningsrelease.htm … q4-2025xearningsrelease.htm
  is_earnings_exhibit(...) → False for all 8                                → 0 exhibits
ETON (CIK 1710340), window 2024-01-01..2026-03-31:
  8-K family rows 151, in window 45; with item 2.02: 30 total, 9 in window  → item filter PASSES
  every one of the 9 filings' exhibit is named  ex_606409.htm … ex_889836.htm  (RDG filing-agent naming)
  is_earnings_exhibit(...) → False for all 9                                → 0 exhibits
```
The three issuers that got 7-12 all happen to name theirs with the digits: `ebs2024-06x30ex99earningsr.htm`, `tmb-20250506xex99d1.htm`, `zvra-20250812xex991.htm` (from `source_documents` in the smoke DB). So the rule is a written-down naming convention (rule 1's shape) standing in for "the exhibit in this filing", and two of five issuers do not follow it.

**Is the missed document worth having?** Verified by printing the span, not the name (`fa_fig.py`):
- NBIX `q4-2025xearningsrelease.htm`: *"INGREZZA fourth-quarter and full-year 2025 net product sales were $657.5 million and $2.51 billion"* — the Q4 figure gold cites, in prose. **Worth 4 of the 15 no-answers** (INGREZZA 2024Q4/2025Q4, CRENESSITY 2024Q4/2025Q4).
- ETON `ex_889836.htm`: names INCRELEX 10 times, and its only figures are company totals (*"fourth quarter 2025 product sales of $21.3 million"*, income statement "Product sales and royalties, net 21,281 11,647"). **No product-level figure for INCRELEX. Fixing the name rule buys Eton nothing.** A name in a document is not a figure in a document, and here the two issuers differ on exactly that.
- ZVRA's older exhibits are the same RDG naming: gold cites `ex_716268.htm`, `ex_725374.htm`, `ex_762500.htm`; none were fetched. `is_earnings_exhibit` returns False for all three. So the rule costs **three** issuers, not two.

**A second, smaller defect found on the way:** the exhibit pass is handed the raw window (`sources.py:1049-1060`, `since=earnings_since, until=earnings_until`) while the primary pass widens by `REPORTING_LAG` both ways (`:952-953`). For NBIX the release filed 2026-05-05 is excluded by the un-widened bound. It costs no *asked* quarter here, because `quarters_reported_in` (`backend/app/parsing/periods.py:650`) only admits quarters whose earliest report lands inside the window — but the asymmetry is unstated and one changed lag makes it a lost quarter.

**Rule check:** rule 1 violated (a filename pattern where a derivation exists — see §3). Rule 5: the docstring at `:264-271` describes the shape with real filer conventions and no measurement; it is clean, it is just wrong about coverage.
**Product cost:** the analyst loses the fourth quarter of every year for any issuer whose filing agent names exhibits `ex_NNNNNN.htm`, and loses it silently — the run reports "no relevant filings" nowhere; the exhibit simply never appears. Q4 is the quarter most often only in the release. Ranked correctly by the register.

---

### F-B — the coverage verdict  `[V] → false as stated; three causes, none of them the one named`

**status_before:** "`names_only` on every document of all 14 jobs… 12d's row-grouped-tables limitation is, on real documents, the whole population."
**status_after:** `[V]` measured, **claim false**, and the row-grouped-table hypothesis is not the cause of a single document I could find in this population.

Distribution as shipped is in §1.1. Re-running `coverage()` myself on the documents with `relevant_datapoints_found > 0`, with the job's own merged aliases (from `drug_profile_fields.llm_aliases`) and `quarters_the_run_asked_for` (`backend/app/quality/completeness.py:47`) — `fb_repro.py`, `fb_dbg.py`:

**Cause 1 — the asked set holds only quarters, so an annual column can never be a member** (`backend/app/connectors/coverage.py:172-173`, `if key not in per_period … continue`). On `nbix-20251231.htm` the product row *is* found and the columns *do* resolve:
```
period keys: {3:'2025', 4:'2025', 5:'2025', 9:'2024', …, 15:'2023', …}
asked:       ['2024Q1' … '2025Q4']
row:         ['INGREZZA','','','$','2,513.7','','','','','$','2,313.5', … '$','1,836.0']
```
Every 10-K page in the run fails this way (NBIX, PTCT, EBS, ZVRA, ETON). It is not a row-grouped table and not a figure test failure; it is a set-membership mismatch between the asked periods and the periods the document states.

**Cause 2 — a row is its own sibling, so a labelled row reads as a combined line.** `coverage.py:159-164` passes `_row_labels(grid)` — which includes the row being read — into `read_label`. Reproduced with invented names:
```
read_label('Calderon net product sales', ['Calderon'], siblings=['Calderon net product sales', 'NuVessa net product sales'])
  → names_product=False, combined_with=('Calderon net product sales',)
read_label(same, siblings=['NuVessa net product sales'])
  → names_product=True
```
Live consequence: `nbix-20260331.htm` yields `coverage → names_only` with **zero grids naming the product**, while the extractor published `INGREZZA net product sales $ 656.9 $ 545.2` for 2026Q1 and 2025Q1 from that same document. `backend/app/extraction/extract.py:620-625` already gets this right and says why in its comment ("a row is not its own sibling, or every label would read as a combined line over itself"), and also handles the row-group heading case at `:637-643`. `coverage.py` is a second implementation of the same idea with both guards missing — the register's own "one idea, two implementations" shape.

**Cause 3 — 97 of 244 documents are XBRL instances, which parse to zero table grids.** `annual_report` + `quarterly_report` sources (`nbix-20250930_htm.xml` etc.) parse `status=success, grids=0`, so the figure test has nothing to run on and the verdict falls to the name test every time. 40% of the fetched corpus is outside the predicate's reach by construction.

**What the predicate would need, measured** (`fb_variants.py`, re-parsing all 244 documents and scoring three variants):
```
as shipped                                  names_only 201  partial 40  unreadable 3
+ a row is not its own sibling              names_only 190  partial 51        (+11)
+ the asked set also holds the annual keys  names_only 170  partial 71        (+31)
```
The 10-K pages the register cites move only in the third variant: `nbix-20251231.htm → partial, carries ['2024','2025']`; `tmb-20251231x10k.htm → partial ['2024','2025']`; `eton20251231_10k.htm → partial ['2023','2024','2025']`.

**Rule check:** rule 1 — `_names_the_product` is fed the job's merged alias list, which for 7 of 12 jobs contains a bare corporate suffix (`'Inc.'`, `'S.A.'`). `_names_the_product(doc_that_says_nothing_about_the_drug, ['Calderon','Inc.']) → True`. `absent` occurs **0 times in 330 documents**; the verdict cannot distinguish "about somebody else" from "about us", which is the one thing the docstring at `coverage.py:251-257` says the name test is for.
**Product cost:** today, none directly — nothing reads the verdict. The cost is that M11 step 3 is specified to cascade on it, and on this evidence it would cascade on a value that is wrong for every 10-K, every instance, and every row whose label carries a noun phrase.

---

### F-C — the M11 premises

**(i) `reportDate` on every periodic row, already in hand.  `[V]` holds.**
```
NBIX recent page: 8-K 96 rows / 10-Q 26 / 10-K 9 — blank reportDate 0
NBIX shard CIK0000914475-submissions-001.json: 8-K 191 / 10-Q 66 / 10-K 22 — blank 0
ETON recent page: 8-K 151 / 10-Q 24 / 10-K 10 — blank 0
(blank reportDate occurs only on 144, SC, S-8, 424B5, UPLOAD, CORRESP rows)
```
The shard carries the same key, so `_filings_covering` (`sources.py:569-619`) merges it into `recent`; `retrieve` then reads only `form`, `accessionNumber`, `primaryDocument`, `filingDate` (`sources.py:935-938`). `grep -rn reportDate backend/app/` → nothing. The index states the period of report on every row the picker looks at, and the picker never looks.

**(ii) The answer sets.  `[V]` with two corrections.**
- *10-Q states P and the prior-year P* — holds. `tmb-20250930x10q.htm` (fetched live): `Translarna` row, columns resolve to 2025Q3 and 2024Q3, figures 50,665 and 72,283. `nbix-20250930.htm`: `INGREZZA` 2025Q3 = $686.6M, 2024Q3 = $612.9M. A Q3 10-Q states no other quarter of its own year.
- *10-K states FY and the prior FY* — **understated**. The product-revenue table states **three** years: NBIX `2,513.7 / 2,313.5 / 1,836.0` for 2025/2024/2023; PTCT and ETON the same shape. A 10-K is worth three annual periods per document, not two.
- *Q4 by subtraction* — needs a second document, not the 10-K alone. The nine-month column lives in the Q3 10-Q: `nbix-20250930.htm` column keys include `2025M9` (1,856.2) and `2024M9` (1,698.4). Gold's own INCRELEX derivation is exactly FY minus nine months.
- *An item-2.02 8-K's reportDate is the period it reports* — **false**, it is the event date. All 16 item-2.02 rows sampled have `reportDate == filingDate` (NBIX 2026-02-11/2026-02-11, ETON 2026-03-19/2026-03-19, …). The pairing the design describes does work: for both issuers the earnings 8-K is filed the same day as, or within days of, the 10-Q/10-K whose `reportDate` is the period.

**(iii) 12d's two numbers as they stand today** (`fc_ratio.py`; *documents* = `source_documents` with retrieval success; *answered* = distinct asked quarters with a `confirmed`/`auto_pass` quarterly datapoint):

```
job                 asked  docs  sec  ans  ans/doc  doc/ans  ans/sec  sec/ans
BioThrax                8    22   21    0     0.00        -     0.00        -
CRENESSITY              8    13   12    1     0.08    13.00     0.08    12.00
Emflaza                 8    26   24    8     0.31     3.25     0.33     3.00
INCRELEX                9    15   13    0     0.00        -     0.00        -
INGREZZA                8    14   12    1     0.07    14.00     0.08    12.00
MIPLYFFA                8    21   19    0     0.00        -     0.00        -
NARCAN Nasal Spray      8    23   21    5     0.22     4.60     0.24     4.20
OLPRUVA                 8    20   19    5     0.25     4.00     0.26     3.80
ONGENTYS                8    14   12    0     0.00        -     0.00        -
Sephience               8    26   24    6     0.23     4.33     0.25     4.00
Translarna              8    25   24    8     0.32     3.12     0.33     3.00
Upstaza                 8    25   24    6     0.24     4.17     0.25     4.00
TOTAL                  97   244  225   40     0.16     6.10     0.18     5.62
```
Baseline: **0.16 quarters answered per document fetched; 6.10 documents fetched per quarter answered** (0.18 / 5.62 counting SEC documents only). Note for whoever measures the change: four jobs answer zero quarters, so `doc/ans` is undefined for a third of the set — the pair must be reported as totals (sum documents / sum quarters), never as a mean of per-job ratios, or a fix that takes an issuer from 0 to 2 will look like a regression.

**(iv) Would M11 as written change the fifteen?** I re-derived the fifteen independently from the smoke DB and the case file (`fc_noans2.py`): 96 expectations, 60 with a figure and 36 expecting silence; excluding the failed MIPLYFFA job → 88 expectations, **15 no-answers**, 33 correctly silent, 2 answered anyway, 1 published-wrong (Translarna 2024Q4: 93.7 against gold's 74.854). Exact match with `008 §7`. Then, for each, whether the document gold cites was fetched (`fc_fetched.py`):

| class | n | what it needs |
|---|---|---|
| **10-Q page never fetched, accession already fetched as an instance** — INGREZZA 2024Q1/Q2/Q3, 2025Q2/Q3; CRENESSITY 2025Q2/Q3 | **7** | M11's set cover over `reportDate` — **and it costs no extra request**: the job already spent a fetch on `nbix-20240331_htm.xml` … `nbix-20250930_htm.xml`, six instances, `relevant_datapoints_found=0` each, while the page beside them in the same accession holds the table |
| **earnings exhibit rejected by the filename rule** — INGREZZA 2024Q4/2025Q4, CRENESSITY 2024Q4/2025Q4, OLPRUVA 2024Q4 | **5** | F-A, not M11. A cover over `reportDate` cannot even address these: the 8-K's `reportDate` is the event date |
| **acquired-business financials in an 8-K/A** — INCRELEX 2023Q4 (`ex_790801.htm`, item 2.01/9.01, FY-minus-nine-months) | **1** | 12c tier 4; excluded today by the item-2.02 gate *and* by the filename rule |
| **document fetched and read; nothing published** — Upstaza 2026Q2, OLPRUVA 2025Q4 | **2** | not retrieval at all (Upstaza is the `Upstaza/Kebilidi` alias case) |

So: **M11's own mechanism changes 7 of 15**; 5 more need F-A; 1 needs tier 4; 2 are not retrieval. The FY-minus-nine-months class is **one** quarter, not three.

**Why the 7 happen, stated as the bound that binds:** with a window given, the primary pass is the *only* capped pass. `sources.py:969` `if picked >= max_filings: break` applies always (`sec_max_filings = 4`, `backend/app/config.py:30`), while the instance pass (`:800`) and the exhibit pass (`:661`) apply their caps only when `bounded` is false. The INGREZZA job's 4 primary documents were three 10-Ks and the newest 10-Q; `reading_order` (`:339`) puts annuals first, so the cap is spent on annual reports before any quarter is reached. `unclassified_budget = 25` (`:792`) and `MAX_SUBMISSION_SHARDS = 4` (`:453`) never bound anything in this run; `sec_max_earnings_exhibits = 6` is passed as the instance pass's `max_filings` (`:1043`) and is dead whenever a window is given.

---

## 3. Proposed implementations (proposals, with what each risks)

### P-A. Read the exhibit's type from the filing, not from its filename
*Touches:* `backend/app/connectors/sources.py` (`is_earnings_exhibit:263`, `_list_filing_documents:521`, `_retrieve_earnings_exhibits:682`), its guard test under `backend/tests/`.
*Mechanism:* the filing's own header page lists every document with the type the filer declared, at the same request cost as the `index.json` call the pass already makes:
```
GET /Archives/edgar/data/914475/000091447526000006/0000914475-26-000006-index-headers.html
  8-K        nbix-20260211.htm
  EX-99.1    q4-2025xearningsrelease.htm
  EX-101.SCH nbix-20260211.xsd      …     XML  nbix-20260211_htm.xml
```
So "the earnings exhibit" becomes "the document this filing declares under an EX-99 type", derived per filing (rule 1), and the `ex_NNNNNN.htm` / `q4-2025xearningsrelease.htm` conventions stop mattering. The header also lists the derived `_htm.xml`, so `_instance_document` could be fed from the same single request rather than a second listing.
*Preserves:* every exhibit the current rule already finds (EX-99.1 and EX-99.2 both, which the "every exhibit, not the first one" comment at `:686-693` exists to keep); the item-2.02 gate; the filings-not-exhibits budget.
*Risks:* one more parse of an HTML-escaped header per filing; a filing whose header lists an EX-99 that is a press release with no table is fetched and parsed for nothing (already true); `<TYPE>` spellings vary (`EX-99`, `EX-99.1`, `EX-99.01`) so match on the family prefix, derived, not on a list of spellings.
*Also fix here, or say why not:* widen the exhibit window by `REPORTING_LAG` as the primary pass does (`:952-953`), so the two passes disagree about nothing.

### P-B. Give the coverage predicate the two guards `extract.py` already has, and an asked set that admits what the documents state
*Touches:* `backend/app/connectors/coverage.py` only (`:157-183`), plus the caller's `periods=` argument in `backend/app/pipeline/orchestrator.py:987-997`.
*Mechanism:* (a) exclude the row's own label from `siblings`, the way `extract.py:620-625` does, with its reason; (b) adopt `extract.py`'s row-group `section` fallback so a row-grouped schedule is read rather than declared unreadable — 12d's stated limitation, still unmeasured in this population; (c) let the asked set carry the annual key of each asked year, so a 10-K's FY column is a period the predicate can carry rather than a period it cannot see. Measured effect on the 244 documents: `partial` 40 → 51 → 71.
*Preserves:* the figure test (nothing here makes a name into a figure); the per-period `carries/refutes/silent` shape; `refutes` from `detect_period_context`.
*Risks:* (c) changes what `answers` means (a document can no longer answer "every period asked" unless it states the annual key too) — the verdict enum needs `answers` computed over the quarters only, with annual carried as its own key. Nothing reads the verdict today, so the blast radius is the log line and the `metadata_json` blob.
*Not fixed by this and worth saying out loud:* 97 of 244 documents are XBRL instances with zero grids; the predicate is inapplicable to them, and if M11 cascades on it, every instance will read as "carried nothing" and trigger an escalation whether or not the facts are there. Either the instance path gets its own coverage answer (from the parsed facts) or instances are excluded from the cascade's evidence.
*Third thing the same file needs:* `_names_the_product` should not be fed `'Inc.'`. `absent` is unreachable for 7 of 12 jobs and occurs 0 times in 330 documents.

### P-C. M11, scoped by what (iv) shows
*Touches:* `backend/app/connectors/sources.py` (`retrieve:960-1033` — pick index rows by `reportDate` cover of the asked quarters instead of `reading_order` + `max_filings`; `_retrieve_xbrl_instances:750` — stop fetching an instance for an accession whose page is being fetched, or fetch the page instead), `backend/app/config.py:30` (`sec_max_filings` becomes a per-quarter ceiling), `backend/app/quality/completeness.py:47` (the asked set is already there and is the right input).
*Preserves:* the widened window (it is what pulled in the one 10-Q that answered INGREZZA 2025Q1); `_filings_covering`'s shard merge; the annual-first reading order as a tiebreak within a quarter.
*Risks:* (1) the 8-K answer set cannot come from `reportDate` — it must be the pairing rule, and an issuer whose earnings 8-K is not same-day with its 10-Q (a January preliminary-revenue release, e.g. `tmb-20250113xex99d1.htm`) will be assigned the wrong period by a naive lag window; (2) a set cover will happily pick one 10-K for three FYs and then need three 10-Qs anyway for Q4-by-subtraction, so the cover must be over *quarters*, with the 10-K entering only through the subtraction rule; (3) the largest single win — the 7 — is not really "choose by coverage", it is "fetch the primary document of an accession you are already fetching", which is a smaller and much safer change to land first and measure separately.

### Rule 4: the set M11 must be scored on
`seed/cases/holdout_2026_09.json` has been read against this diagnosis (its five issuers appear throughout §2) and is spent. A new set must avoid every issuer any key already names:
```
cd /home/user/pharma-analog-uptake-workbench/backend && ./.venv/bin/python -c \
 "import sys;sys.path.insert(0,'.');from tests.answer_keys import spent_issuers;print(len(spent_issuers(excluding=[])))"
→ 59
```
The 59, from `seed/gold/{annual,quarterly}_revenue.jsonl`, `seed/holdout*`, `seed/holdout_labels`, `seed/holdout_members`, `seed/holdout_foreign_xbrl.json`, and `seed/cases/{foreign_xbrl,gold_all,gold_sample,holdout_2026_09,shapes_holdout,unseen}.json`: AbbVie, ANI Pharmaceuticals, Acadia, Actelion (and Actelion/J&J), Alkermes, Alnylam, Alvotech, Amgen, Amicus, ARGENX, Biogen, BioCryst, BioMarin, BioNTech, Blueprint Medicines, Bristol-Myers Squibb, Catalyst, Collegium, Corcept, Deciphera, Dr Reddy's, Eli Lilly, Emergent BioSolutions, Eton, Exelixis, Genmab, Gilead, Grifols, GSK, HUTCHMED, Incyte, Indivior, Insmed, Intercept, Ionis, Jazz, Johnson & Johnson, Krystal Biotech, Liquidia, Merck, Mirum, Neurocrine, Novartis, Novo Nordisk, Organon, Perrigo, Pfizer, PTC Therapeutics, Regeneron, Sanofi, Sarepta, Supernus, Teva, Travere, United Therapeutics, Vertex, Viatris, Zevra. The set drawn from outside this list must contain, per 12e's own requirement, at least one acquired product (to exercise the 8-K/A path the INCRELEX quarter needs) and one issuer whose product detail is not in its periodic reports (to exercise the exhibit path) — and, given F-A, at least one issuer whose filing agent names exhibits `ex_NNNNNN.htm`, since that convention is the defect and a set of issuers who all name theirs `…ex991.htm` would score a fix that changes nothing as a success.

### What I did not verify
- Whether the row-grouped-table case (12d) binds anywhere in this population: I found no document where it was the cause, and I did not scan the cached `run7` corpus for one. `[U]`.
- Whether P-A's header-page parse holds for a paper/`.txt`-only filing or a foreign issuer's 6-K: not tested. `[U]`.
- The throttle, the LLM-search fallback, `openfda`/`clinicaltrials` on the revenue path, and `resolve_cik`'s behaviour on a generic drug name — in my brief but outside these three findings; not examined in this pass. `[U]`.
