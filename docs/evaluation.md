# How this pipeline is measured

Five evals. They do not measure the same thing, and the difference between two
of them is where most of the mistakes in this project have been made.

## What these evals do not measure

`eval_coverage.py` and `eval_extraction_documents.py` call the readers
directly. Checked mechanically, they exercise **none** of the twelve stages in
`orchestrator.run_job` and call no LLM entry point at all. What they measure is
the deterministic extraction floor: the XBRL reader, the table reader and the
derivations, with sourcing included.

Four things the pipeline does are therefore absent from every number below.

* **The LLM extractor** (`orchestrator.py:1001`), run over up to six documents
  per job, its output gated by `filter_revenue_candidates` so a quote that is
  not verbatim in the source is dropped.
* **The evidence judge** (`JobStep.EVIDENCE_JUDGE`), which judges each
  datapoint against its own quote, with a deterministic fast path and hard
  vetoes before any model is called.
* **Reconciliation** (`JobStep.RECONCILE_CONFLICTS`), which groups candidates
  by period, scope and formulation, has the model pick a winner among those
  that disagree, and falls back to `SOURCE_PRIORITY` - which ranks
  `SEC_FILING`, the 10-K and 10-Q, above `EARNINGS_RELEASE`. Losers are marked
  `needs_review`, not published.
* **The search fallback**, quality checks, and completeness.

So a "wrong value" here is a figure a reader *emitted*, not one the pipeline
*published*: two stages stand between them, and most disagreements would be
routed to review. Read these numbers as a floor on what can be found without a
model, and an upper bound on the error rate that would survive.

## The score

**`scripts/eval_extraction_documents.py --discover`.** Gold supplies only the
product, the issuer and the quarter. The pipeline resolves the CIK, walks EDGAR
for earnings exhibits around that quarter, reads what it finds, and is asked for
that product's revenue. Finding the right filing is part of the job, so this is
the only number that describes the pipeline.

```bash
python scripts/eval_extraction_documents.py --discover --limit 40   # a sample
SEC_CONTACT='...' python scripts/eval_coverage.py /tmp/coverage.json  # the corpus
```

`eval_coverage.py` answers the same question for every row. What the pipeline
finds is decided by the issuer and the quarter rather than the product, so it
does one EDGAR walk per (issuer, quarter) pair — 275 of them behind 1,415 rows —
and writes a per-row record that the next run can be diffed against.

Measured over the whole corpus — all 1,415 gold rows, deduplicated to the 275
(issuer, quarter) pairs that decide what the pipeline finds:

| | rows | share |
|---|---|---|
| read correctly | 1,070 | 75.6% |
| not found | 329 | 23.3% |
| no readable filing | 13 | 0.9% |
| **wrong value** | **3** | **0.2%** |

| issuer | | | from tagged facts | from tables |
|---|---|---|---|---|
| Johnson & Johnson | 364/424 | 85.8% | 56 | 308 |
| Merck | 16/19 | 84.2% | 15 | 1 |
| Liquidia | 4/5 | 80.0% | 4 | 0 |
| Actelion/J&J | 46/58 | 79.3% | 15 | 31 |
| United Therapeutics | 268/368 | 72.8% | 105 | 163 |
| Gilead | 372/541 | 68.8% | 110 | 262 |

Reading the filer's tagged facts before its tables took this from 998 to 1,070
and added no wrong values. The headline understates what changed: 305 answers
now come from a declared fact, of which only 72 are new, so 233 rows that
already worked no longer rest on inferring a period from a heading's geometry.

Every gained row falls in 2019 or later, which is where detail tagging begins.
Nothing in the code knows that date - a filing from before an issuer's own
cutoff simply yields no product facts.

Two things in that table are worth stating plainly. The pipeline sourcing for
itself (75.6%) scores **higher** than the same pipeline handed the document
gold cites (65.2%), because gold cites investor-relations PDFs and press
releases while EDGAR's 8-K exhibits are better structured — so the reading
score was never an upper bound on the real one, and treating it as the headline
understated the pipeline while pointing the work in the wrong direction. And
Johnson & Johnson, which read 0/424 for this entire project, is now the best
covered issuer in the set, from HTML that was always on EDGAR.

What is left has two shapes. By era:

| | read | |
|---|---|---|
| 2002–2009 | 7/99 | 7.1% |
| 2010–2019 | 606/799 | 75.8% |
| 2020–2026 | 385/517 | 74.5% |

The 2000s are all but unreachable — EDGAR's older filings are `.txt` submissions
and the 8-K earnings exhibit as a separate HTML document is a later convention.
The rest concentrates in products an issuer folds into a franchise line rather
than reporting separately: Nebulized Tyvaso (68), Atripla (37), Truvada (36),
AmBisome (34).

## The diagnostics

**`scripts/eval_extraction_documents.py`** (no flag) hands the pipeline the
document gold cites. It measures reading with sourcing removed. It is fast,
deterministic and offline, which is why it is easy to start treating as the
score — and doing so has a specific cost, recorded here because it was paid:
gold cites Johnson & Johnson's investor-relations PDFs, so this eval reported
J&J at 0/424 and a PDF reader was built to fix it. The pipeline cannot source a
PDF at all — `is_earnings_exhibit` accepts only `.htm`, `.html`, `.txt`, and no
EDGAR filer in a sample of sixteen publishes a PDF earnings exhibit. Sourcing
for itself over the same quarters the pipeline scored 0/24, because the
connector took one exhibit per 8-K and J&J puts its schedules in the second.
Deleting `[:1]` took it to 23/24, from HTML on EDGAR, where it had always been.
A measurement that removes the step that is broken cannot report that it is
broken. The script now prints that warning before it prints a score.

**`scripts/eval_column_alignment.py`** replays each gold row's `source_quote`
and asks whether the figure can be recovered from it. That measures column
alignment and nothing else — it is scored on a passage a human chose, so
widening the quotes moved it 1.5 points with no code change. Diagnostic only,
and the script says so.

## The gates against fitting to gold

Gold is 39 products from six issuers. A rule tuned until those documents pass
is fitted to them, and the way to notice is to keep a corpus that cannot be
tuned against.

**`scripts/eval_period_generalization.py`** scores period detection on earnings
exhibits from Pfizer, AbbVie, Amgen and Eli Lilly — none of which appears
anywhere in gold. A change that moves gold a lot and this barely is fitted. That
is not hypothetical: a "FIRST QUARTER" heading reader lifted gold by 18 rows and
was worth exactly zero here, even though three quarters of these documents use
that phrasing. It was deleted on the strength of that number.

**`scripts/eval_pdf_geometry.py`** renders those same held-out exhibits to PDF
with a browser and requires the geometry to recover what the markup states. One
document, read two ways, must say the same thing, so it needs no answer key.
EDGAR carries no PDF earnings exhibits to hold out — checked across sixteen
filers — so the corpus is made rather than found.

Both read the corpus built by `scripts/sourcing/fetch_holdout.py`.

## The gate on provenance

**`scripts/eval_tagged_provenance.py`** does the same for the tagged path,
which the audit below cannot check: a fact has no prose to quote, so its receipt
is the element, the context and the period instead. The check goes back to the
instance the citation names and confirms the fact it points at says what the
datapoint says. Without it the tagged path publishes unaudited citations, which
was true for one run and is the reason this exists.

Currently **367/367** across 29 instances: every citation resolves to a fact,
holds the value claimed, and names the member.

Its first run said 262/367, and the 105 failures were the check's own. A
citation was looked up by context id alone, and a context is a period and a set
of dimensions rather than a fact - Johnson & Johnson tags a product's revenue and
its percentage change against the same one. So a revenue figure was compared
against a growth rate of -0.215 and called a disagreement. Keyed by context and
element, nothing disagrees.

**`scripts/eval_provenance.py`** takes each datapoint the pipeline published,
opens the document it cites, and checks the quote is verbatim in it and the
value present in the quote. It uses no gold answer at all — it audits the
pipeline's own claim against the pipeline's own source. Coverage can be argued
about; a citation that does not stand up cannot.

## Reading a number honestly

Three habits earned their place here:

- **A number that moves when you work on it is the one you start believing.**
  The reading score went up all session and the sourcing score was run once and
  left alone.
- **A low score is a claim about the measurement until proven otherwise.**
  Three times a bad number was a harness bug — a fingerprint context that
  production passes and the harness did not, an eval calling a function it had
  stopped importing, a discovery label that said "found nothing" when it meant
  "found nothing readable".
- **Report the wrong answers, not just the misses.** Twice a change raised the
  score *and* added wrong values in the same step — a rectangle that described
  nothing, and a row cap that cut a product's table in half. Neither is visible
  in "read correctly".
