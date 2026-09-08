# What is known, what is quirky, and what to do next

Written at the end of a session that took deterministic coverage from
1,070/1,415 to 1,315/1,415 and, more usefully, found out what that number
actually measures. Read the second section before trusting any figure in the
first.

## 1. The pipeline, accurately

`PipelineOrchestrator.run_job` (`backend/app/pipeline/orchestrator.py:190`)
runs twelve stages:

    identity -> retrieve -> parse -> extract metadata -> judge metadata ->
    EXTRACT REVENUE -> (search fallback) -> EVIDENCE JUDGE ->
    RECONCILE CONFLICTS -> QUALITY CHECKS -> COMPLETENESS -> VALIDATION TASKS

Inside **extract revenue** there are four producers, tried in this order:

1. **Tagged XBRL facts** — `app/parsing/xbrl.py` + `app/extraction/tagged.py`,
   member identity via `app/extraction/members.py` and `seed/xbrl_members.csv`.
   A fact states its period, unit, currency and product, so nothing is
   inferred. Citation names element + context. Supplies ~404 of 1,415 rows.
2. **The LLM extractor** — `orchestrator.py:1001`, up to
   `llm_max_extract_sources = 6` documents per job, output passed through
   `filter_revenue_candidates`, which drops any quote not verbatim in the
   source.
3. **The deterministic table reader** — `app/parsing/documents.py` (grid),
   `app/extraction/fingerprint.py` (what the table declares),
   `app/extraction/extract.py` (which row is the product).
   `app/extraction/prose.py` fills only periods no table stated.
4. **The derivations** — `app/extraction/derive.py`: the quarter left implicit
   against a stated total, and the family total attributed to the sole
   formulation on sale before a sibling appeared.

Then **evidence judge** judges every datapoint against its own quote
(`app/quality/fast_judge.py` short-circuits the clear cases), and
**reconcile conflicts** groups candidates by (period, scope, formulation), has
the model pick a winner among disagreements, and falls back to
`SOURCE_PRIORITY` (`orchestrator.py:95`), which ranks `SEC_FILING` (10-K,
10-Q) **above** `EARNINGS_RELEASE`. Losers are marked `needs_review`, not
published.

## 2. What the evals measure, which is not that

Checked by AST, not by reading: `scripts/eval_coverage.py` exercises **none**
of the twelve stages and calls **no LLM entry point**. It calls
`candidates_from_instance`, `extract_revenue_candidates` and `complete_series`
directly.

So every coverage figure is **the deterministic extraction floor**: XBRL
reader + table reader + derivations + sourcing. And every "wrong value" count
is a figure a reader *emitted*, with two error-catching stages standing
between it and anything published.

**The pipeline's actual accuracy is unmeasured.** That is the single biggest
gap.

## 3. Verified numbers, with what each one measures

Recomputed from per-row artifacts, not from logs.

| figure | value | what it measures |
|---|---|---|
| deterministic coverage, current code | **1,307/1,415 (92.4%)**, 16 emitted-wrong | readers + sourcing, no LLM, no judge |
| deterministic coverage, without the prose reader | 1,315/1,415 (92.9%), 7 emitted-wrong | same, code before prose was wired |
| starting point this session | 1,070/1,415 (75.6%) | same |
| per issuer | Gilead 98.9%, J&J 92.0%, UTHR 87.8%, Merck 89.5%, Actelion 79.3%, Liquidia 80% | same |
| reading diagnostic | 922/1,415 (65.2%) | handed the document gold cites |
| table+prose provenance | 2,100/2,102 (99.9%) | quotes verbatim in the cited document |
| tagged provenance | 367/367 | citation resolves to a fact holding the value |
| held-out unit declaration | 82.6% -> 91.3% | four filers absent from gold |
| held-out period detection | 118 geometry / 80 ragged, unmoved | four filers absent from gold |
| PDF geometry agreement | 99.0% -> 99.1% | one document read two ways |
| tests | 343 pass | — |

A coverage run varies by about ±3 between identical invocations, because
EDGAR returns 503s under load.

## 4. Rules that hold

- **Gold is an oracle, never an input.** No module under `app/` may read
  `seed/gold/`; no script may both read gold and write a pipeline input.
  Enforced by `backend/tests/test_gold_is_not_an_input.py`. It exists because
  the member register was once built from gold's product list.
- **Do not fit to gold.** Six issuers is small enough that a rule tuned until
  their documents pass is fitted to them. `scripts/eval_period_generalization.py`
  and `scripts/eval_pdf_geometry.py` score filers absent from gold; a change
  that moves gold a lot and these barely is fitted. A "FIRST QUARTER" heading
  reader once lifted gold by 18 rows, was worth zero here, and was deleted.
- **Refuse rather than guess.** A wrong value with a citation is worse than a
  gap. Seven emitted-wrong against 100 misses is the intended ratio.
- **Rank sources, never pool them.** Two candidates that disagree are not an
  answer. A later source answers only the periods an earlier one did not.
  `SOURCE_PRIORITY` is the pipeline's ranking; do not write a second one.
- **Every capability must be called by `app/`.**
  `backend/tests/test_capabilities_are_wired.py` fails the suite if an entry
  point is reachable only from a script. Three readers had been written,
  tested, measured and never called.
- LLM calls use non-Claude OpenRouter models (`openai/gpt-4o-mini` for both
  extract and judge). SEC User-Agent from `SEC_USER_AGENT`; no real address in
  committed code. No paid vendors are scraped.

## 5. Quirks of the data, all of them load-bearing

- **52/53-week fiscal years.** J&J closes fiscal 2022 on 1 January 2023 and
  fiscal Q2 2018 on 1 July 2018. Labelling a period by its end date mislabels
  1,038 of 5,397 product facts (338 by year, 700 by quarter). The label comes
  from the period's **midpoint**.
- **A table declares its unit directly above itself.** A Gilead exhibit says
  "(in millions)" over the guidance table and "(in thousands)" over the
  product sales summary 17,000 characters later. Searching the document finds
  the wrong one. `table_caption` walks backwards and stops at the previous
  table.
- **A currency symbol occupies its own column, on the first line of a block
  only.** So "Harvoni - U.S." sits one grid column right of "Harvoni -
  Europe" while both are the same quarter. Subtotal arithmetic must group by
  **period**, not by column.
- **An em dash in a value column is zero.** Read as "no number", a line that
  sold nothing everywhere looks like a heading and its label is carried onto
  the next product: "Atripla - Europe Viread - U.S.".
- **Gilead prints the worldwide total as an unlabelled row** beneath the
  regional lines. It is identified by arithmetic, not by a list of region
  names.
- **iXBRL detail tagging phases in by filer class** — large accelerated for
  periods ending on/after 15 June 2019, accelerated 2020, everyone else 2021.
  No date is hardcoded: a filing before its issuer's cutoff simply tags no
  product facts. Liquidia is a non-accelerated filer.
- **A member means different things to different filers.**
  `us-gaap:ProductMember` is Yutrepia at Liquidia and a meaningless total at
  Gilead, so the register is keyed by `(issuer, member)` at write *and*
  lookup. Matching is whole-word suffix, longest wins — substring matching
  made "Tyvaso" resolve to `TyvasoDPIMember`.
- **The least-qualified fact is the total.** UTHR tags Adcirca's year twice,
  plainly at $41.3m and under the Eli Lilly arrangement at $1.0m. J&J tags
  every product under its segment and that takes nothing away. Which axes
  subset a figure is not knowable from a list of axis names.
- **A filer extension is not a revenue element.**
  `uthr:GrossProfitExcludingOtherRevenue` matches the word "Revenue"; so does
  a percentage and a contract term. Only the standard taxonomy's revenue
  elements count.
- **A primary filing carries dozens of tables that mention a product for some
  other reason.** An 8-K earnings exhibit is a product-sales schedule and
  nothing else. Adding 10-K/10-Q HTML tables to the pool gained 0 rows and
  lost 9 — a verdict on the reader, not the filings, whose *tagged facts*
  outrank everything.
- **Post-2022 UTHR prints one combined "Tyvaso" line** that includes DPI, so
  "one line for the brand" cannot be attributed to a formulation.
- **J&J reports "INVEGA SUSTENNA / XEPLION / INVEGA TRINZA / TREVICTA" as one
  franchise line** — four names, and the product asked for is one of them.

## 6. Quirks of the environment

- `SEC_CONTACT` is documented by the eval scripts but the connector reads
  `SEC_USER_AGENT`. Set both.
- Document cache `/tmp/discovered`, held-out corpus `/tmp/holdout`. A cold
  coverage run over 275 (issuer, quarter) pairs takes ~11 minutes warm and
  ~50 minutes with `include_primary=True`.
- `uv run --project backend` from the repo root; `cd backend && uv run pytest -q`.

## 7. How this session went wrong, so it does not repeat

Six reported figures were wrong. The pattern is one thing three ways.

1. **I read a harness's label without reading its assignment site.** `via` is
   stamped on the whole non-tagged branch, so I told the user prose caused no
   wrong values when prose *was* the regression. Same shape: an inverted era
   table, a `NameError` that marked 487 documents unreadable, a provenance
   checker keyed on `context_id` alone, a register credited with +72 rows it
   never produced.
   **Rule: any field used to draw a conclusion, read its assignment site in
   the same turn.**
2. **I added capability without checking what existed.** A PDF reader for
   documents the pipeline cannot source. A deterministic prose reader beside
   an existing LLM extractor. A source ranking contradicting `SOURCE_PRIORITY`.
   **Rule: before writing anything in `app/`, grep for the capability's nouns
   and say what already exists.**
3. **I probed for the failure I had already imagined.** Searching for periods
   ending in early January found 338 mislabelled facts; asking what the rule
   *changed* found 1,038.
   **Rule: measure the change, not the hypothesis.**

And one structural cause behind all of it: **the eval re-implements the
pipeline instead of running it**, so it can drift silently, and did, in four
places at once.

## 8. Next steps, in order

1. **Build `scripts/eval_pipeline_end_to_end.py`.** Create a run and a job per
   (product, issuer, window), call `orch.run_job`, score the *published*
   datapoints against gold, and report the `validation_status` split so the
   judge's catch rate is visible. Start with 3-4 products over 1-2 years
   before spending the LLM budget on the corpus. This is the number that
   should become the headline; the deterministic run is demoted to the floor.
2. **Delete `_AUTHORITY` from `scripts/eval_coverage.py`** and use
   `SOURCE_PRIORITY`. Two rankings that disagree is the copy-drift this repo
   warns about elsewhere.
3. **A weak reading pre-empts an exact derivation. This is the next fix.**
   `coverage_v7` (ranked precedence, `include_primary=True`) finished at
   1,307/1,415 with 16 emitted-wrong: zero gained against the 1,315 run and
   eight lost, seven of them Nebulized Tyvaso. The mechanism is not sourcing.
   Derivations run only over rows left `not_found`, so any earlier reader that
   produces *something* for a period blocks the derivation — and a prose
   sentence or a 10-Q table producing 1.00 for a quarter whose family total
   derives exactly to 94.64 turns a correct answer into a wrong one.

   Ranking by document was the wrong axis. A derivation over published figures
   is a stronger claim than a sentence, and the order should be by the
   strength of the claim: tagged fact, then schedule, then exact derivation,
   then prose. Primary-filing HTML is now off again in the eval — measured
   twice, zero gain, forty minutes a run — but that is a speed decision, not
   the fix.

   Note the honest consequence: the deterministic floor for the **current**
   code is **1,307**, not 1,315. The higher figure belongs to code without the
   prose reader. Do not quote 1,315 for code that has it.
4. **The remaining ~100 misses**, by cause: Remodulin 2002-2009 (29, prose in
   10-Qs — the LLM extractor may already handle these), Invega Sustenna (21,
   franchise line naming four brands), Actelion 2017 acquisition year (19,
   the annual total does not cover the year), Tyvaso DPI and Nebulized Tyvaso
   split-quarter cases (16).
5. **Re-run every held-out gate after any reader change.** They are the only
   defence against fitting to six issuers.

## 9. Open questions worth a decision

- Should the franchise-line case (Invega Sustenna) be answerable at all? Gold
  treats the four-brand line as the product; the pipeline refuses it. Refusing
  may be correct and the row simply unreachable.
- Is the deterministic floor worth keeping as a separate eval once the
  end-to-end one exists? It is fast, offline and model-free, which makes it a
  good regression gate even if it is not the score.
