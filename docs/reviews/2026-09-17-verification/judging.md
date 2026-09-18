# Verification: judging items J-1..J-4

Read-only. **No file in `/home/user/pharma-analog-uptake-workbench` was changed by me** (`git status --porcelain` at start and end shows only another agent's edits: `backend/app/extraction/derive.py`, `backend/tests/test_every_quarter_two_totals_determine.py`, `backend/tests/test_extraction_stack.py`). Scratch: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/verify-judging/` (`smoke.db`, `run13.db` copies; `closure2.py`, `closure3.py`, `colcheck3.py`, `edgarscan2.py`, `prose_scan.py`).

**Scope filter, stated once.** The smoke database holds 596 datapoints across 31 run ids. Section 7's smoke run is **three** run ids — `3f25070a…`(5 jobs), `c61b3799…`(6), `440c853b…`(1) = **12 jobs, 193 datapoints**. The 19:15 run `2f03db9a…` (32 jobs, 403 datapoints, many still `queued`/`running`) is the live server writing on the PAH/gold catalogue; I excluded it everywhere. Every smoke number below carries that filter.

---

## A. Items whose meaning changed — read these first

### A1. `[V]` Gold is wrong for Translarna 2024Q4. The pipeline's 93.7 is the issuer's own figure.

The audit's "**published, WRONG (1)**" is not a pipeline defect. It is a defect in `seed/cases/holdout_2026_09.json`.

Read from the filings (all from the cached corpus under `.../scratchpad/wt-audit2/backend/storage/cache/sec/`):

| what | value | where |
|---|---|---|
| FY2024 Translarna, as first reported | 339,900,000 (dec −5) | `000110465925…/tmb-20241231x10k_htm.xml`, `ptct:TranslarnaMember` FY2024 |
| 9M-2024 Translarna | 246,217,000 | `000107008125000028/tmb-20250930x10q_htm.xml`, `Duration_1_1_2024_To_9_30_2024 … TranslarnaMember` |
| FY2024 Translarna, restated | 321,071,000 | `000110465926017575/tmb-20251231x10k_htm.xml`, `ptct:Translarna1Member` FY2024 |
| FY2024 **Translarna France** | 18,806,000 | same instance, `ptct:TranslarnaMember` FY2024 (the member was re-pointed at the France line) |
| Q4-2024 Translarna France | 4,618 | Feb-2026 8-K exhibit income statement, row `Translarna France*` |

The Feb-2026 exhibit's own footnote settles it (`000110465926017546/tmb-20260219xex99d1.htm`):

> *"In the fourth quarter of 2025, PTC changed its estimates for its sales allowance related to Translarna revenues in France. The $98.6 million change in sales allowance estimate represents a life to date adjustment for the historical sales of Translarna in France. **The 2024 amounts relate to historical Translarna sales recorded in France.**"*

So `Translarna France` became a **separate revenue line** on the face of the income statement, and 2024 was recast to take it out of Translarna product revenue:

- 321,071 + 18,806 = **339,877 ≈ 339.9**, the originally-reported FY2024. Exact.
- Q4-2024 **as originally reported** = 339,877 − 246,217 = **93,660 → $93.7M**. Exact match to the Feb-2025 bullet the pipeline auto-passed.
- Q4-2024 **restated, ex-France** = **$89.1M**, stated by the issuer itself in the Feb-2026 bullet, and consistent with its own "Translarna net product revenues were … $321.1 million for full year 2024" in the same release.
- 93.718 − 4.618 = 89.1. The whole difference is the France line.

**Gold's 74.854 is a mixed-basis subtraction**: 321,071 (FY2024 **ex**-France) − 246,217 (9M-2024 **including** France). It subtracts the nine-month France amount (18,806 − 4,618 = 14,188) once too often. 89.1 − 74.854 = 14.246, which is that amount to within the rounding of "89.1". The case file's own `shapes` says `"fourth quarter from annual less nine months"` — the recipe is right, the two inputs are on different bases.

**What a specialty-pharma analyst would use**: either basis, consistently. On the as-filed-at-the-time basis (which is what gold's own 2024Q1-Q3 are — 103.584/70.350/72.283 all include France) Q4-2024 is **93.7** and FY2024 is 339.9. On the restated basis Q4-2024 is **89.1** and FY2024 is 321.1, and Q1-Q3 would have to be recast too. 74.854 is neither, and it is the only one of the three that no filing states or implies.

**Was the pipeline right to hold 93.7?** Yes, on the basis its sibling quarters are on. It also captured 89.1 and held it `needs_review` with `restated_in_later_filing` — which is the correct description of what happened.

**Why `filing_contradicts_itself` did not fire.** It is scoped to a single accession by construction (`orchestrator.py` ~2933-2946: `by_accession` built from `accession_of(r)`, then a pairwise `_agrees_within_declared_precision` **within** each accession). The 93.7 is accession `0001070081-25-000011` (8-K, 2025-02-27); the 89.1 is `0001104659-26-017546` (8-K, 2026-02-19). Different filings, so that check never looks at the pair. The cross-filing case is handled instead by `reports_own_period` (`orchestrator.py:513`) / `claim_tier`, which ranked the reporting filing above the later one and flagged the loser `restated_in_later_filing` (`orchestrator.py:3043`). That is the right machinery and it gave the right answer.

(Note: 4.618 *and* 89.1 **are** from the same accession and disagree, and `filing_contradicts_itself` still did not fire — correctly, because the 4.618 row was enriched to geography France / scope Unknown and lands in a different `series_identity` group. A genuinely different revenue line, correctly not treated as a contradiction.)

**Rank.** Larger than the register ranks it, and in the opposite direction. This is a **rule-4 hazard**: a wrong answer key does not produce a wrong figure, it produces a *fix aimed at a figure that was already right*. Anyone who "fixes" the pipeline until Translarna 2024Q4 reads 74.854 will have taught it to prefer a number no filing states.

**Action (not a design):** the item is a correction to `seed/cases/holdout_2026_09.json`, case 3, `2024Q4` — 74.854 → 93.7 (as-reported) or 89.1 (restated), with the basis named — plus a note on the case that the series changes basis at 2025Q4. Rule 3 permits this direction (reference data → answer key).

---

### A2. `[V]` The Sephience figure is **not** a comparative-column error. It is a research-and-development **expense** table read as product revenue.

`.../cache/sec/000107008126000017/tmb-20260630x10q.htm`. The table the quote `Sephience $ 22,078 $ 26,741` comes from, rendered:

```
['Three Months Ended June 30,']
['2026', '2025']
['(in thousands)']
['Sephience', '$', '22,078', '$', '26,741']
['Inflammation & Ferroptosis platform', '5,571', '9,186']
['Global DMD', '2,988', '8,318']
['Gene Therapy', '169', '3,465']
['Other development programs', '224', '3,440']
['Total Development', '31,030', '51,150']
['Research', '18,331', '15,691']
['Payroll, benefits, and share-based stock compensation', '40,096', '36,995']
['Facilities and other indirect costs', '9,693', '9,154']
['Total research and development', '$', '99,150', '$', '112,990']
```

Its caption, which the pipeline already passes into `_read_table(context=…)` and then ignores: *"The following table provides **research and development expense** for our most advanced principal product development programs, for the three and six months ended June 30, 2026 and 2025."*

**Answering the question as asked:** the two columns are `2026` and `2025` under `Three Months Ended June 30,`, i.e. 2026Q2 and 2025Q2. **The period assignment is correct.** 22,078 really is the 2026 column and 26,741 really is the 2025 column. The audit's mechanism ("the figure assigned to the comparative column") is wrong for this row.

Replayed at HEAD (`read_table` on grid 48 of that document) the reader still emits both:
```
ExtractedValue(product_label='Sephience', period='2026Q2', value_as_reported=22078.0, unit_label='thousands', …)
ExtractedValue(product_label='Sephience', period='2025Q2', value_as_reported=26741.0, unit_label='thousands', …)
```
and `try_deterministic_judgment` at HEAD auto-passes **both**:
```
2026Q2 22078.0 -> {'validation_status':'auto_pass', 'issues':['deterministic:product_quote_value_ok']}
2025Q2 26741.0 -> {'validation_status':'auto_pass', 'issues':['deterministic:product_quote_value_ok']}
```
Stored with `metric='revenue'`, `revenue_scope='Product family'`, `confidence 0.85`.

The same filing's **actual** product-revenue table (grid 38) prints Sephience's 2025Q2 as three em dashes:
```
['Sephience','$','127,550','$','23,760','$','151,310','$','—','$','—','$','—']
```
which is exactly what gold expects (`"the row prints an em dash in both scope columns"`). So the issuer says "no sales" in one table and the pipeline published $26.741M from another table in the same document.

**Cost is two wrong figures, not one.** 2025Q2 = 26.741 published `auto_pass`, and 2025Q1 = 24.141 derived from it (`'Sephience: 2025 six_month total 50.882 less reported 2025Q2 yields 2025Q1 24.141'` — 50,882 is the *six-month* R&D expense from the same schedule). Gold expects `None` for both.

**The screen the audit proposed would have missed it.** The carrying unit `Sephience ​ $ 22,078 ​ $ 26,741 ​` holds exactly **two** numeric tokens. On run13, 60 of the 293 auto-passes have exactly two, 23 have one. "210 of 293 hold more than two" (which I reproduce exactly — see B1) is not the predicate that catches this row.

**The class, verified on three more documents, one of them a fresh issuer:**
- `Avapritinib external expenses ​ $ 952 ​ $ 4,487 ​ $ (3,535) (79) %` — AYVAKIT 2025Q1 = 952 and 2024Q1 = 4,487 on run13. These are **2 of the 36 rows the model judges**, and the model/search path held both. The judge does catch this class when it is asked.
- Heron Therapeutics 10-Q `0001193125-26-341301`: an R&D table with rows `ZYNRELEF-related costs`, `SUSTOL-related costs`, `CINVANTI-related costs`, `APONVIE-related costs`, beside a genuine product-revenue table with the same four brands.
- Agios 10-Q `0001439222-26-000118`: `PK activator (PYRUKYND®/AQVESME™) $ 25,849 $ 29,809 $ 49,754 $ 51,130` in an R&D-by-programme table.
- Tarsus 10-Q `0001819790-26-000064`, in prose: *"we recognized $12.1 million and $6.2 million, respectively, in **cost of sales** for XDEMVY"*.

**What the item actually is** (one line, per the protocol): `_read_table` reads every table in the document and emits `metric='revenue'` for any row whose label names the product and carries figures; nothing consults the caption it was handed, the row's neighbours (`Total research and development`), or the sign convention. The auto-pass gate is downstream of that and cannot see it.

**Rank:** above the auto-pass gate. Closing the gate makes the model judge this row; fixing the reader stops the row existing. On run13 the model's two chances at this class (AYVAKIT) both went the right way.

---

### A3. `[V]` The two vetoes track A was said to have deleted are still at HEAD.

`git log -1` = `a736ac3`; `git diff HEAD` touches `alembic.ini`, `api/products.py`, `db/models.py`, `main.py`, `pipeline/orchestrator.py` — not `llm/client.py`. All eight vetoes are present:

`backend/app/llm/client.py` — `change_not_level:857`, `company_total_without_product:862`, `other_brand:865`, `ytd_language_as_quarterly:878`, `quote_states_a_different_period:894`, `milestone_or_license_revenue:899`, `product_missing_from_quote:904`, `value_and_product_in_different_sentences:854`; `_QUARTER_SPAN:799`, `_SPAN_OF_PERIOD_TYPE:802`.

`_spans_named_in` **was** fixed (`c8d47af`): `spans_named_in("For the Six Months Ended June 30,")` now returns `{6}`, not `set()`.

Veto census re-run at HEAD (`apply_judge_hard_vetoes`, generic and aliases supplied, `peer_names` **not** supplied — that is the filter):

```
                                                  run13  (549)      smoke (193)
                                                  fires  alone      fires  alone
value_and_product_in_different_sentences            124    118        13      6
milestone_or_license_revenue                          8      5         1      1
product_missing_from_quote                            8      1        22     15
quote_states_a_different_period                       3      1         0      0
company_total_without_product                         0      0         0      0
change_not_level                                      0      0         0      0
ytd_language_as_quarterly                             0      0         0      0
other_brand                                           0      0         0      0
```

Reproduces the audit's run13 table exactly, and **confirms the four dead vetoes on a second, independent dataset**. That is the strongest evidence the register has for deleting them, and it did not exist before this pass.

---

### A4. `[V]` The rule-4 guard falsely rejects any issuer written "X Pharmaceutical Inc." — a rule-1 defect in the answer-key module itself.

`backend/tests/answer_keys.py:133-138` `COMPANY_WORDS` is a hand-written list. It holds `"pharmaceuticals"` (plural) and `"pharma"` but **not** the singular `"pharmaceutical"`. `"Collegium Pharmaceutical Inc"` is in `seed/`, so `scored_words()` contains `pharmaceutical`, and:

```
FAIL Ultragenyx Pharmaceutical Inc.   ident=['pharmaceutical','ultragenyx']  overlap=['pharmaceutical']
ok   Vanda Pharmaceuticals Inc.       ident=['vanda']                        overlap=[]
```

Ultragenyx shares no issuer with any answer key, and the guard says it does. `biopharma`, `biotechnology`, `biopharmaceuticals`, `biopharmaceutical` are the same shape (`Xeris Biopharma` → `ident=['biopharma','xeris']`, which passes today only because no scored issuer happens to use the word). This is CLAUDE.md rule 1 in the module whose docstring cites rule 4. **It excluded Ultragenyx from my proposed set** (A4 is why J-4 below names Vanda/Heron/Xeris/Ardelyx instead).

---

## B. Per item

```
J-1  partly-verified -> [V] false in mechanism, true in effect, and worse in cost
J-2  unchecked       -> [V] holds; the affected population is 34 of the model's 36 calls
J-3  open question   -> [V] gold is wrong; 93.7 and 89.1 are both the issuer's own
J-4  n/a             -> [V] set proposed, every shape verified against a live filing
```

### B1. J-1 — the auto-pass gate

**(a) The document and its columns.** See A2. `Three Months Ended June 30,` over `2026` | `2025`; 22,078 → 2026Q2, 26,741 → 2025Q2; both are R&D expense. Command: `html_table_grids` + `column_periods` on `cache/sec/000107008126000017/tmb-20260630x10q.htm`, grid 48.

**(b) Auto-passed rows whose figure sits in a column of the wrong period.**
Scope: the 12-job smoke run; auto-pass set produced by replaying HEAD's `try_deterministic_judgment` over the stored rows (candidate rebuilt from the columns; `label_flags=[]`, no peers — that is the filter).

```
193 rows -> 157 deterministic:product_quote_value_ok
              15 hard_veto:product_missing_from_quote
              13 hard_veto:value_and_product_in_different_sentences
               5 <model judge runs>
               2 deterministic:non_quarterly_period_type
               1 hard_veto:milestone_or_license_revenue

of the 157 auto-passes, 135 have >2 numeric tokens in the carrying unit
  (llm 43, xbrl_fact 37, prose 34, table 13, derived 8)

located in a parsed grid that has column_periods:  42
    figure's column period == row period:          41
    figure's column period != row period:           1
not locatable:                                     93
    quote is not from an HTML table at all  38  (xbrl_fact 37, derived 1)
    prose sentence, no table row exists     34
    llm quote, row not found in any grid     14
    derived, synthetic quote                  7
```

Method: `column_periods(grid)` per table, row anchored on a first cell containing the product or generic, column found by the printed form of the value, covering column walked left to the nearest column with a period; a row counts as a match if **any** occurrence lands in the right period (generous to the pipeline).

**The one mismatch is real, and it is the case the coordinator flagged**:

```
Upstaza  period=2025Q2  value=11,163  column=2026Q2  table  needs_review
  'Upstaza/Kebilidi ​ ​ — ​ 11,163 ​ 11,163 ​ ​ ​ — ​ 11,889 ​ 11,889'
  cache/sec/000107008126000017/tmb-20260630x10q.htm, grid 38
  column_periods: {3,4,5,6,7}->(3, 6, 2026)   {10..15}->(3, 6, 2025)
  11,163 sits at columns 5 and 7 -> 2026Q2;  11,889 at 13 and 15 -> 2025Q2
```
Confirmed against `holdout_2026_09.json`: Upstaza `2026Q2` expects **11.163** and `2025Q2` expects **11.889**. The run has no 2026Q2 Upstaza row at all (no answer) and holds 11,163 as a `needs_review` 2025Q2 carrying a spurious `restated_in_later_filing`. The gate auto-passes it at HEAD:
`try_deterministic_judgment(Upstaza, 2025Q2, 11163, that quote) -> auto_pass / deterministic:product_quote_value_ok`.

Note the same document, same reader: Upstaza's genuine revenue row is read one year out, and Sephience's R&D row is read as revenue. Two different failures, one filing, both auto-passed.

**(c) The closure, simulated.** Closure as proposed: *the value must sit in the same unit as the product (`sentence_carrying` ∧ `quote_mentions_product` on that unit) and that unit must hold no other money figure, else fall through.*

Money figure defined as a filing prints one — `$`-marked, comma-grouped, or decimal — after stripping ISO dates, `(n)` footnote marks and `YYYYQn` labels, excluding bare years and percentages. (A looser definition that counts every numeric token gives 198/95 on run13 and 152/17 on smoke; it misfires on XBRL date ranges and on `2022Q1` inside a derivation quote. The definition is load-bearing and is itself a risk of the change.)

```
                        auto-pass  ->  still auto-pass   fall through   extra judge calls/job
run13   549 rows, 20 jobs    293            101              192              +9.6
smoke   193 rows, 12 jobs    157             53              104              +8.7

fall-through by method    run13: llm 121, table 49, derived 16, prose 6
                          smoke: llm 42,  table 23, prose 31, derived 8
still auto-pass by method run13: xbrl_fact 62, llm 33, prose 5, table 1
                          smoke: xbrl_fact 37, llm 12, prose 4, table 0
```

**How many of those the model path would then judge: all of them.** `try_deterministic_judgment` returns `None` on a fall-through, and `orchestrator.py:2615` then unconditionally awaits `self.llm.judge(...)`. No other branch intercepts.

**What it costs per job — the register's figure is an undercount.** `orchestrator.py:2637` escalates to `judge_with_search` whenever `support ∈ {partial, unsupported, inconclusive, misclassified}`. On run13, **36 of 36** rows that reached the model carry `llm_search_validated`, i.e. every one escalated. So the realistic cost is **~2 model calls per fall-through, one of them a web-search call** — roughly **+19 model calls per job on run13, +17 on smoke**, at the judge model. The register's "one extra judge call" framing is half the bill.

**The capability cost, which is the real risk.** The closure removes auto-pass from the `table` reader almost entirely — 49 of 50 on run13, 23 of 23 on smoke. Product-revenue schedules are comparative by construction. The audit's own measurement says 76 run13 rows survive a spurious period veto only because of the year-cross; those are the same rows. Under the closure every one becomes an `openai/gpt-4o-mini` judgment. Example, correct today and correct only if the model says so tomorrow:

```
FIRDAPSE 2023Q1 = 57,526  from  'FIRDAPSE ® $ 66,842 $ 57,526'   -> falls through
Aurinia  (fresh issuer, 10-Q 0001600620-26-000049), in prose:
  'For the three months ended March 31, 2026, net product sales of LUPKYNIS were
   $73.6 million, up 23%, from $60.0 million, in the same period of 2025.'      -> falls through (both figures correct)
```

**Both answers.**

*Must still auto-pass (one figure, one name).*
- `Net product sales of DAYBUE were $75.9 million for the first quarter of 2024.` (run13, prose)
- Harmony Biosciences 8-K `0001104659-26-090086`: `… today reported Q2 2026 net revenue for WAKIX® (pitolisant) of $261.3 million …` (one money figure; `30%` is a percent, `2026` a year)
- Every `xbrl_fact` quote: `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax [2024-07-01..2024-09-30] ProductOrServiceAxis=AgamreeMember = 15,046,000` (62 of run13's 101 survivors)

*Must not auto-pass.*
- `Sephience ​ $ 22,078 ​ $ 26,741 ​` — R&D expense; caught by the closure, but incidentally
- `Upstaza/Kebilidi ​ ​ — ​ 11,163 ​ 11,163 ​ ​ ​ — ​ 11,889 ​ 11,889` — comparative column
- `we recognized $12.1 million and $6.2 million, respectively, in cost of sales for XDEMVY` (Tarsus) — prose, cost of sales

**Verdict on J-1.** The gate is under-specified; verified. The closure closes both smoke failures; verified. But it closes the Sephience one for the wrong reason (token count, not metric), it misses the class where the expense row has a single figure, and it costs the table reader its entire auto-pass at ~19 extra model calls per job. **The metric gate is the cheaper and more exact instrument; the unit rule is a blunt one.** Both are rule-4 changes.

### B2. J-2 — `evidence_judge.yaml`

`[V]` **Line 11 still orders the deleted verdict.** `backend/app/prompts/evidence_judge.yaml:11`:
> `- period_type is quarterly but quote/header says six months, nine months, YTD, or year ended`

under the heading at `:7-8` *"you MUST return support_classification 'misclassified' and validation_status 'needs_review' (or 'rejected')"*. Lines `:30-34` say the opposite: *"Such a quote names every period its columns cover, not just the first one printed, and a comparative column is as much a part of it as the current one."* The code sides with :30-34 — `client.py:869-878` requires `carried & _YEAR_TO_DATE_SPANS and _QUARTER_SPAN not in carried`, so a heading naming both spans does not veto.

`[V]` **Four rules are applied deterministically after the model answers.** `:9` → `company_total_without_product` (`client.py:862`), `:10` → `other_brand` (`:865`), `:11` → `ytd_language_as_quarterly` (`:878`), `:12` → `product_missing_from_quote` (`:904`). `client.py:462` returns `apply_judge_hard_vetoes(...)` wrapping the model's result, so the model's opinion on all four is discarded either way.

`[V]` **`peer_names` never reaches the prompt.** `client.py:443-448` formats `product`, `candidate`, `quote`, `context` only; `peer_names` is passed at `:469` to the veto. Same in `judge_with_search` (`:744-751` / `:757-764`). The prompt's veto 3 has nothing to check against. Unchanged since the audit.

`[V]` **Exactly which rows are at stake — and the meaning here changed.** Replaying HEAD's deterministic judge over run13's 549 rows gives 36 rows to the model (reproduces the audit). Of those 36:

- **0** have any post-hoc hard veto fire at HEAD. The model's verdict is the final word on all 36 — every one is eligible to change with the prompt.
- **34** are two-span heading quotes (`spans_named_in` = `{3,6}` or `{3,9}`) — the exact population line 11 and lines 30-34 disagree about. That is **94% of the model's entire workload**. Products: FIRDAPSE, FYCOMPA, NUPLAZID, DAYBUE, Jornay PM, Thiola, Livmarli, Ocaliva, FILSPARI, ORLADEYO.
- **2** are the AYVAKIT `Avapritinib external expenses` rows (A2's class), held `contradicted` by the search validator.
- Stored outcome of the 36: `validation_status` 23 `needs_review` + 13 `corroborates`; **0 auto_pass**. `source_support` 34 `misclassified` + 2 `contradicted`.

**The 34 `misclassified` values cannot be used as a baseline.** They are run13-era output: those same 34 rows carry the stored flag `hard_veto:ytd_language_as_quarterly` (34 of 36), which the *code of the day* applied. At HEAD that veto fires 0 times on the whole database. So 6b silently moved 34 rows off a deterministic veto and onto a model that is still being told the deleted rule. Nobody has yet observed what the model says about them.

**What is measurable without the model, and what needs the eval.**

Measurable, done: the population (34 of 36), that no veto masks the verdict (0 of 36), that no row currently reaches `auto_pass`, and that the stored `misclassified` is the old veto.

Needs the eval, and only the eval: what the model returns on those 34 quotes under the current prompt versus a reconciled one. The measurable prediction to hold it to — **if the model obeys line 11, it returns `misclassified` on all 34, which reproduces exactly the veto 6b deleted and cancels 6b's value.** The metric is *"of N two-span comparative product rows whose figure is the current-quarter column, how many end `auto_pass`"*, run on the J-4 set, not on run13.

**Proposed reconciled prompt** (`backend/app/prompts/evidence_judge.yaml`, replacing `:7-12` and extending the candidate section). Delete the four vetoes rather than fix line 11: the code applies them regardless, so they buy nothing and one of them is now false.

```yaml
system: |
  You are an evidence judge for pharmaceutical revenue extraction.

  Four rules are applied to your answer afterwards, by code, whatever you say:
  a quote whose value-carrying sentence does not name the product; a quote
  about a peer brand; a milestone or licence payment; a quote naming a period
  the row does not claim. Do not spend your answer on them. Judge what code
  cannot: whether this figure, for this period, on this basis, is this
  product's own revenue.

  The candidate is the row as the pipeline filled it, and it says more than
  the figure. Use it:
  - metric and the surrounding caption say what kind of number this is. A
    schedule of research and development expense by programme prints the
    product's name beside a money figure and is not revenue; so do a segment
    expense schedule, an inventory table, an intangible-asset table, and a
    "cost of sales for X" sentence. If the caption or the table's own total
    row says expense, cost, asset or allowance, say unsupported.
  - unit and currency say what the number is denominated in. A quote printing
    "34,974" under a caption in thousands and a candidate saying millions do
    not agree, and neither do "$" and a candidate in euros.
  - geography and revenue_scope say what the figure covers. A quote for one
    region does not support a worldwide figure, and a worldwide quote does
    not support a region's.
  - extraction_method and source_type say who read it and out of what.
  - footnote, when present, is the filer's own note about this figure,
    carried out of the table with it. A note saying the figure covers part of
    the period - a launch or an acquisition inside it - means the row is not
    that period's full revenue, however clean the quote looks. Say so.
  - label_flags are what the label reader could not settle: combined_line
    (the label named more than one product), label_not_understood (words of
    the label were not accounted for), partial_period (a note says the figure
    covers less than the period).
  - label_residue is those unaccounted words, verbatim.

  A quote lifted from a table arrives as the heading plus the row: the
  heading states the spans ("Three Months Ended June 30," beside "Six Months
  Ended June 30,") and the years stand under it, one per column. Such a quote
  names every period its columns cover, not just the first one printed, and a
  comparative column is as much a part of it as the current one. On such a
  row say which column the figure came from and whether that column is the
  period the candidate claims. That is the one question nothing else asks.

  Classification guide: (unchanged, minus "misclassified: failed a hard veto
  above" -> "misclassified: the quote is about something other than this
  product's revenue for this period")
```

Also, per the audit and still true at HEAD: `label_residue` reaches the model twice — as a `{candidate}` key and again prepended to `{context}` (`orchestrator.py` ~2588-2591, `context = f"Row label words not accounted for: {residue}\n\n{context}"`). Pick one.

**Preserves:** every deterministic behaviour (the vetoes are unchanged code). **Risks:** it is a prompt change on 100% of the model's workload, so rule 4 forbids scoring it on run13, on `holdout_2026_09` or on gold. It must be run on the J-4 set, and its number reported with the configuration header.

### B3. J-3 — see A1. `[V]`, gold is wrong, the pipeline was right, `filing_contradicts_itself` was correct not to fire.

### B4. J-4 — the rule-4 set

**`[V]` The issuers every existing key spends.** Derived, not written down — `answer_key_paths()` rglobs `seed/`, 23 files, 13 of which name issuers: `gold/annual_revenue.jsonl`, `gold/quarterly_revenue.jsonl`, `holdout/quarterly_revenue.jsonl`, `holdout2/quarterly_revenue.jsonl`, `holdout_foreign_xbrl.json`, `holdout_labels/product_labels.json`, `holdout_members/combined_name_members.json`, `cases/{gold_all,gold_sample,holdout_2026_09,shapes_holdout,unseen,foreign_xbrl}.json`. **59 distinct issuers, 192 distinct products.**

```
ANI · ARGENX · AbbVie · Acadia · Actelion · Actelion/J&J · Alkermes · Alnylam ·
Alvotech · Amgen · Amicus · BioCryst · BioMarin · BioNTech · Biogen · Blueprint ·
Bristol-Myers Squibb · Catalyst · Collegium · Corcept · Dr Reddy's · Deciphera ·
Eli Lilly · Emergent BioSolutions · Eton · Exelixis · GENMAB · GSK · Gilead ·
Grifols · HUTCHMED · Incyte · Indivior · Insmed · Intercept · Ionis · Jazz ·
Johnson & Johnson · Krystal · Liquidia · Merck · Mirum · Neurocrine · Novartis ·
Novo Nordisk · Organon · PTC · Perrigo · Pfizer · Regeneron · Sanofi · Sarepta ·
Supernus · Teva · Travere · United Therapeutics · Vertex · Viatris · Zevra
```

**Proposed set — 11 products, 6 issuers, none of them spent** (checked with `scored_words()` ∩ `identifying()` = ∅ for each). Every shape below was read out of a live EDGAR filing in this pass; the accession is given so the next reader can fetch it.

| # | issuer (CIK) | products | filing | the shape it holds |
|---|---|---|---|---|
| 1 | **Vanda Pharmaceuticals** (0001347178) | Fanapt, HETLIOZ, PONVORY, NEREUS | 10-Q `0001628280-26-053855` | Two-year comparative product table, three **and** six-month spans. `NEREUS ™ net product sales 1,029 — 1,029 —` → **a product with no sales in a quarter its table names** (2025Q2). Also gives the must-auto-pass answer: NEREUS 2026Q2 = 1.029 is a single distinct figure in its row. `HETLIOZ 5,576 16,192 21,523 37,064` gives the must-fall-through answer. |
| 2 | **Heron Therapeutics** (0000818033) | CINVANTI, ZYNRELEF, APONVIE, SUSTOL | 10-Q `0001193125-26-341301` | Two-year comparative product table **and**, in the same document, `ZYNRELEF-related costs / SUSTOL-related costs / CINVANTI-related costs / APONVIE-related costs` in an R&D expense table — the Sephience shape on a fresh issuer. `SUSTOL-related costs — 75 — 75`. |
| 3 | **Xeris Biopharma** (0001867096) | Recorlev, Gvoke, Keveyis | 10-Q `0001867096-26-000065` | Two-year comparative product table; `Other product revenue — 1,312 — 1,312` (nil in the current quarter); **and** a results-of-operations table with `Change $` / `Change %` columns, which is the `states_a_change_not_a_level` layout the closure's money count must not mistake for two periods. |
| 4 | **Ardelyx** (0001437402) | IBSRELA, XPHOZAH | 10-Q `0001437402-26-000044` | Two-year comparative product table, plus the same two brands again in a `Change 2026 vs. 2025 $ / %` table — two readings of one figure that must reconcile, not contradict. Income statement also carries `Product supply revenue` and `Licensing revenue` beside `Product sales, net`. |
| 5 | **Agios Pharmaceuticals** (0001439222) | PYRUKYND | 10-Q `0001439222-26-000118` | The **opposite** answer: revenue rows are `Product revenue, net - U.S.` and `- Rest of world` and never name the product, while `PK activator (PYRUKYND®/AQVESME™) $ 25,849 $ 29,809 …` names it in the R&D table. A name-anchored reader must come back empty for revenue and must refuse the expense row. |
| 6 | **Harmony Biosciences** (0001802665) | WAKIX | 8-K EX-99.1 `0001104659-26-090086` | The clean must-auto-pass: `reported Q2 2026 net revenue for WAKIX® (pitolisant) of $261.3 million`. Also a trap in the same exhibit: `full-year net revenue guidance of $1.0 billion to $1.04 billion` must not become a quarterly figure. |

Supplementary prose cases, both verified, from issuers already in the set's spirit and outside the spent list — include if 11 is short:
- **Aurinia Pharmaceuticals** (0001600620), 10-Q `0001600620-26-000049`, LUPKYNIS: *"net product sales of LUPKYNIS were $73.6 million, up 23%, from $60.0 million, in the same period of 2025"* — two correct figures in one sentence; the closure's cost case in prose.
- **Tarsus Pharmaceuticals** (0001819790), 10-Q `0001819790-26-000064`, XDEMVY: *"we recognized $12.1 million and $6.2 million, respectively, in cost of sales for XDEMVY"* — must be refused.

**Requirements the caller set, met:** 11-13 products; 6-8 issuers, all outside the 59; **four** issuers with two-year comparative 10-Q product tables (Vanda, Heron, Xeris, Ardelyx) against a required two; **two** products with no sales in a quarter their table names (Vanda NEREUS 2025Q2, Xeris `Other product revenue` 2026Q2); both answers for J-1 present in a single table (Vanda) and again across the set.

**Do not build it from Ultragenyx** even though it is genuinely unspent — the guard will reject it until A4 is fixed (`identifying("Ultragenyx Pharmaceutical Inc.")` contains `pharmaceutical`, which `Collegium Pharmaceutical Inc` already spends).

---

## C. Checks 4 and 5 — the five rules and the analyst

**Rule 1 (derive the list).** One violation found, A4: `COMPANY_WORDS` in `backend/tests/answer_keys.py:133-138` is a hand-written snapshot that already excludes a correct issuer. The producer exists — the singular/plural and the `bio-` prefixes are morphology, not a name list. Second, smaller: `TOTAL_REVENUE_RE` in `app/parsing/evidence.py:23` (`\btotal\s+revenues?\b`) is a written-down phrase standing in for "this is not a product line"; it matched 0 of 549 run13 quotes and 0 of 193 smoke quotes, while the caption `"research and development expense"` — the thing that would have caught A2 — is already in hand and unread.

**Rule 2 (a claim needs its command).** The register's J-1 claim carried a predicate ("more than two numeric tokens") that excludes the failure it was written about — the Sephience unit has exactly two. Stated as a count it is exactly right (I reproduce 210 of 293); stated as the screen for this defect it is the filter's absence, not the data's.

**Rule 3 (nothing the pipeline reads may come from the answer key).** Nothing proposed here creates a path. A1's correction runs reference-data → answer key, the permitted direction. J-4's set is built from live EDGAR, not from `seed/`. The prompt in B2 names invented shapes and no real brand — check it again before it lands, because a prompt is the one file in this repo that the model reads at run time.

**Rule 4 (scored on a set it was not built from).** Three of the four items are behaviour changes on the judge and none may be scored on run13, on `holdout_2026_09`, on gold, or on the smoke run — all four are now spent on diagnosing them. A1 additionally shows the failure mode rule 4 exists to prevent arriving from the other end: a wrong entry in an answer key, treated as a defect, drives a change that makes a correct figure wrong.

**Rule 5 (a comment explains the code).** No new violations found in the files I read. `fast_judge.py`'s comment block and `client.py:790-793`/`824-905` describe shapes, not results.

**What it costs the specialty-pharma analyst, ranked.**

1. **A2 — a number with the shape of a measurement and none of the meaning.** Sephience 2025Q2 = $26.741M and 2025Q1 = $24.141M are R&D spend published as revenue, `auto_pass`, with a citation an analyst can click and a quote that reads correctly. Two fabricated points at the base of a launch curve — the steepest and most load-bearing part of an uptake analog — for a product the same filing says had no sales. This is the worst thing in the register: a wrong figure that looks checked.
2. **A1 — a wrong answer key is worse than a wrong figure.** It costs nothing today (nothing is published from it) and costs everything the moment someone tunes against it.
3. **B1(b) — Upstaza.** One quarter missing (2026Q2), one correct quarter (2025Q2 = 11.889) carrying a false `restated_in_later_filing`. A gap the analyst will notice; a flag they will mistrust the series for.
4. **B2 — 34 rows whose verdict nobody has observed.** Comparative-column quarters are how a series gets its first two years. If the model obeys line 11 they all go to review, and the review queue is where series go to not be delivered.
5. **B1(c) — the closure's cost.** +19 model calls per job and no auto-pass on any table row, to catch a class the metric would catch for free.

---

## D. Verified vs inferred

**Verified by command, in this pass:** the R&D caption and both grids of `tmb-20260630x10q.htm`; that `read_table` and `try_deterministic_judgment` at HEAD still produce and pass both Sephience rows and the Upstaza row; the 293/124/86/36/8/1/1 deterministic distribution on run13 and 157/15/13/5/2/1 on smoke; the eight-veto census on both databases; 210 of 293 with >2 tokens and 60 with exactly 2; the closure's 101/192 and 53/104 under a stated money definition; that a fall-through is exactly one `llm.judge` call and that 36/36 of run13's model rows escalated to `judge_with_search`; the 42 locatable / 41 match / 1 mismatch column measurement; the five Translarna XBRL facts and the France footnote; that `filing_contradicts_itself` is scoped per accession and the two Translarna readings are different accessions; `evidence_judge.yaml:11` versus `:30-34`, the four post-model vetoes, and that `peer_names` never reaches the prompt; that all 34 two-span rows carry the run13-era `hard_veto:ytd_language_as_quarterly` that fires 0 times at HEAD; the 59-issuer / 192-product answer-key census; the `pharmaceutical` guard defect; every EDGAR table and sentence quoted in J-4.

**Inferred, and labelled as such:** that the 0.058 gap between my computed 89.042 and the issuer's stated 89.1 is rounding in the release's own bullet rather than a further reclassification (the direction and the magnitude — the 9M-2024 France amount — are verified; the last decimal is not). That the closure's fall-throughs would not change outcome in ways beyond the extra model calls — that needs the model. That no other auto-passed row in either database sits in a non-revenue table: I scanned table- and llm-method rows in the smoke run against their tables' captions and found one (Sephience) plus two false positives (segment tables whose rows read `… net product sales`), but 34 prose and 37 xbrl rows have no caption to scan, so the scan's reach is the filter, not the population.

**Could not be verified with what is here:** what `openai/gpt-4o-mini` actually returns on the 34 two-span quotes under either prompt. That is one `scripts/eval.py` run on the J-4 set, with the configuration header, and it is the gate on B2 and on B1(c) alike.
