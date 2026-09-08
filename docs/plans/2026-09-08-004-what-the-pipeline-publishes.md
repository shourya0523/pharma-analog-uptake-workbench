# What the pipeline publishes, and what it withholds

Successor to `003`, which is still accurate about the readers and the data.
This one is about the stages after them, which had never been measured.

Read `003` sections 5 and 7 first — the data quirks and the three habits — and
treat everything below as an addition rather than a replacement.

## 1. The gap that mattered

`003` said the pipeline's actual accuracy was unmeasured and that closing it
was the single biggest gap. It was, and the number was not close to what the
readers suggested.

`scripts/eval_pipeline_end_to_end.py` creates a run and a job per (product,
issuer, window), calls `run_job`, and scores what came back with
`validation_status = auto_pass`. First measurement, four products over two
issuers and two years, 32 gold quarters:

| | before | after two fixes |
|---|---|---|
| published, correct | 11/32 (34%) | **32/32 (100%)** |
| published, wrong | 0 | 0 |
| correct but withheld | 21 | 0 |
| judge catch rate | 3/3 | 4/4 |

The readers were finding the answers. The pipeline was refusing to hand them
over. **Both defects were in stages no reader-level eval can see**, which is
the argument for that eval existing.

## 2. The two defects, because the shape repeats

**A tautology treated as an estimate.** `deterministic_formulation_fill` writes
`"aggregate"` into `formulation` when `revenue_scope` is already
`"Product family"`. It restates the row; it cannot be wrong. It went through
`apply_field_enrichment`, whose contract — any fill forces `needs_review` and
caps confidence at 0.55 — is right for a model's guess at a blank field and
wrong for a restatement. The row was then disqualified twice: by the flag, and
by a confidence under the quality gate's 0.7 floor. "One line covering the
whole family" is the ordinary shape of a product's revenue, so this fired on
most rows. 27 of 41 datapoints carried the flag, 26 for this fill alone.

**A declined verdict read as a verdict against everyone.** Reconciliation reads
the model's `conflicts` entries. With no `winner_id` named, *every* id in the
entry was marked a loser, and the deterministic fallback then skipped the group
because it already contained losers. A schedule reading 54.0 beside a sentence
reading 13.4: both withheld, quarter unanswered.

Fixing the second alone would not have been safe. The fallback ranks by
`SOURCE_PRIORITY`, which ranks the *document*, and both candidates came from
one 8-K exhibit — so they tied, and the tie went to whichever was extracted
first. It could as easily have published 13.4.

## 3. `CLAIM_STRENGTH`, and why it is not a second `SOURCE_PRIORITY`

`SOURCE_PRIORITY` ranks the document a figure came from. It cannot separate two
readers of the same document, and a product-sales schedule and a sentence of
narrative sit in the same exhibit.

`CLAIM_STRENGTH` (`orchestrator.py`) ranks the *producer*, by how much had to be
inferred: tagged fact, schedule, exact derivation, model reading, sentence.
They are used together — document first, then claim, then confidence. One
ranking each, on two different axes, both in `app/`.

It lists both spellings that reach it: a candidate carries the reader's own
label (`table_fingerprint`, `prose_sentence`), a stored datapoint carries the
shorter one the export uses (`table`, `prose`).

## 4. The prose reader is the open question

Naming the reader honestly in the per-row detail — it used to stamp `"table"`
on the whole non-tagged branch — made this legible for the first time. Over the
corpus, by producer:

| producer | correct | wrong |
|---|---|---|
| tagged XBRL facts | 404 | 0 |
| schedules | 853 | 3 |
| derivations | 45 | 0 |
| **prose sentences** | **5** | **13** |

Thirteen of the sixteen wrong values are one reader's, and it contributes five
right ones. The mechanism is visible without reading code: Nebulized Tyvaso
2022Q2 reads 42.2 and Tyvaso DPI 2022Q2 reads 42.2 — one sentence attributed to
two sibling products, where gold is 198 and 3. Descovy 2021Q4 reads 5,800
against a gold of 473.

Ranking derivations above it (`50bb701`) does nothing on its own, and the
reason is worth keeping: `complete_series` reads any candidate for a period as
that period being answered, so a sentence misreading 2013Q1 as 1.0 does not
lose to the family total that derives to 94.645 — it stops that total being
computed, and there is no derived candidate for any ranking to prefer. The
corpus came back identical in every bucket, not one row moved.

The fix is to keep weak readings out of the derivation's *inputs* (`8b95356`),
after which the ranking decides between them. Measured over the 1,342 rows
readable in both runs:

| | base | with both | delta |
|---|---|---|---|
| read | 1,255 | 1,261 | **+6** |
| wrong value | 14 | 10 | **−4** |
| not found | 73 | 71 | −2 |

All four recovered wrong values are Nebulized Tyvaso — 2013Q1, 2013Q2, 2019Q4,
2020Q3 — each a `prose_sentence` misreading replaced by a
`derived_sole_formulation` correct one. That is the product `003` predicted
would move.

**Read that table's caveat.** The run's own headline was 1,261 against a 1,307
baseline, which looks like a large regression and is not one: it carried 30
connector errors and 31 extra `no_readable_filing` rows where the baseline had
zero and 12. EDGAR was throttling, almost certainly because this session had
been running back-to-back walks. A clean re-run is owed before the +6/−4 is
quoted as settled. It does not help a period no derivation reaches, which still
receives a sentence's answer.

**Under this project's own rule — a wrong value with a citation is worse than a
gap — 5 for 13 is a bad trade.** Deleting `app/extraction/prose.py` is the
obvious next experiment, and it is a scope decision rather than a bug fix, so
it was left for a person. `003` notes the reader was written to reach United
Therapeutics' 2002–2009 Remodulin quarters; the corpus says it currently earns
five rows anywhere.

## 5. What is measured, and what is only justified

Be strict about this distinction; most of this session's near-misses came from
blurring it.

**Measured.** The deterministic floor, 1,307/1,415 with 16 wrong, reproduced
today and matching `003`'s amended figure exactly. The end-to-end sample above.
The producer split in section 4.

**Measured on held-out products.** Both fixes in section 2 were found by
reading the failures of *one* 32-quarter sample and then measured on that same
sample, which is how a fix gets fitted to its own evidence. So they were re-run
over Uptravi, Stelara and Xarelto in 2018–19 — two issuers, no row any fix was
derived from:

| | |
|---|---|
| published, correct | 15/24 |
| published, wrong | 0 |
| **held for review, correct** | **0** |
| no datapoint at all | 9 |

Of everything the pipeline found there, it published all of it and got all of
it right. On the tuned sample the same pipeline had withheld 21 of 32 correct
answers. The fixes generalise.

**Not measured at all.** Any corpus-wide end-to-end figure. 32 quarters is a
sample; the corpus is 1,415 rows and would cost real money to run. Do not quote
100% as the pipeline's accuracy.

## 5a. The exhibit budget was cutting quarters in half

The 9 held-out quarters with no datapoint were not a reading failure. Every
product missed exactly Q2 2018, Q2 2019 and Q3 2019, identically across two
issuers, which is too regular to be about products.

`sec_max_earnings_exhibits` is 6 and counted **exhibits**. Johnson & Johnson
files two EX-99 documents per earnings 8-K — the press release and the
product-sales schedule — so six exhibits bought three quarters and the rest of
the window was dropped. Worse, the budget cut mid-filing: 2019Q3 got
`a2019q3exhibit991.htm` and stopped, leaving `a2019q3exhibit992.htm` behind.
That quarter was retrieved and could not be read.

This is the failure the connector already carries a comment about. "Every EX-99
exhibit, not the first one" was bought with a wrong answer once, and capping by
document count reintroduced it for any filer whose window outlasts its budget.

The budget now counts filings and a filing brings all of its exhibits. Same J&J
window: 6 documents over 3 quarters became 11 over 6.

**The lesson is the shape of the evidence, not the fix.** A failure that lands
on the same periods across unrelated products is a property of retrieval. Per-
product theorising would have chased J&J's franchise lines and Actelion's
acquisition year, both of which are real and neither of which was this.

## 5b. EDGAR throttles, and a throttled run looks like a regression

`003` says a coverage run varies by about ±3 between identical invocations
because EDGAR returns 503s under load. That understates what happens when a
session runs walks back to back for an afternoon. The run scoring the
derivation pair came back with **30 connector errors and 43 rows with no
readable filing**, against zero and twelve in the baseline an hour earlier —
same code path for retrieval, same cache, 46 fewer rows read.

Nothing about the change could produce that. A derivation cannot make a filing
unreadable.

So, two habits:

* **Read `connector_error` and `no_readable_filing` before reading the score.**
  They are printed and they were being skipped. If either has moved, the run is
  measuring EDGAR's availability and the comparison is void.
* **Compare on rows both runs could read.** Restricting to the intersection
  turned an apparent 46-row regression into the +6 / −4 the change actually
  made. The per-row artifact exists so this is a filter, not a re-run.

Leave a few minutes between corpus walks. A run costs about twelve minutes; a
contaminated one costs that plus the time spent believing it.

## 6. Tools worth knowing about

- `--rescore FILE` re-runs the scoring over a stored run without running
  anything or calling a model. A stored run keeps every candidate with its
  status, scope and value. **Use it before believing a change to how a gold row
  is matched.** The first scope rule written here was coherent, wrong, and
  would have taken the sample from 11 correct to 1; re-scoring the runs already
  on disk caught it before it reached a report.
- `backend/tests/test_the_eval_runs_the_pipeline.py` fails the suite if no
  script drives `run_job`, if the end-to-end eval stops scoring persisted
  datapoints, or if any script under `scripts/` restates a source ranking
  instead of importing `SOURCE_PRIORITY`.

## 7. Three ways this session was wrong, all the same way

`003` section 7 said: any field used to draw a conclusion, read its assignment
site. Every error below is that rule, unlearned and relearned.

1. **Counting something that looked like the hypothesis.** Claimed
   reconciliation can never compare the deterministic readers against the
   model, and cited "2 of 5 disagreements never compared". Looking at the
   pairs: almost all were a U.S. line against a worldwide line — different
   series, both right, and comparing them is what reconciliation *should not*
   do. Counted over pairs that answer the same gold row, it compares 3 of 3 and
   3 of 4. The real miss is one pair separated by `formulation`.
2. **Scoring a right answer as wrong.** Reported that the pipeline published
   two wrong values and the judge caught neither. Gilead prints Truvada by
   region and in total; the eval compared a correct U.S. figure against the
   worldwide gold row. Both were the measurement.
3. **Reading a field whose writer had changed.** Computed a per-issuer table
   showing zero tagged reads for every issuer, from a JSON written before the
   `via` label was renamed. Caught only because zero was impossible.

## 8. Next, in order

1. **Re-run the held-out set with the retrieval fix in.** 15/24 was measured
   against a connector that dropped three of its six filings; the ceiling on
   that number was retrieval, not reading, and it should now move.
2. **Decide the prose reader**, on section 4's numbers.
3. **`003` section 8 item 4** — the remaining misses by cause — is still open
   and unchanged: Remodulin 2002-2009, Invega Sustenna's franchise line,
   Actelion's 2017 acquisition year, the Tyvaso split-quarter cases.
4. **A corpus end-to-end run**, once someone decides the LLM budget is worth
   the number. Everything before that is a sample.
