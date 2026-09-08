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

Ranking derivations above it (commit `50bb701`) recovers the quarters it was
blocking. It does not help a period no derivation reaches, which still receives
a sentence's answer.

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

**Justified but thinly measured.** Both fixes in section 2 were found by
reading the failures of *one* 32-quarter sample and then measured on that same
sample, which is how a fix gets fitted to its own evidence. Their defence is
that both arguments are structural — a restatement cannot be evidence, a
declined verdict is not a verdict — and a held-out run over Xarelto, Stelara
and Uptravi was started for exactly this reason.

**Not measured at all.** Any corpus-wide end-to-end figure. 32 quarters is a
sample; the corpus is 1,415 rows and would cost real money to run. Do not quote
100% as the pipeline's accuracy.

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

1. **Finish the held-out end-to-end run** (Xarelto, Stelara, Uptravi, 2018–19)
   and compare it to sample 4. If it does not look like it, section 2's fixes
   are fitted and this document's headline is wrong.
2. **Decide the prose reader**, on section 4's numbers.
3. **`003` section 8 item 4** — the remaining misses by cause — is still open
   and unchanged: Remodulin 2002-2009, Invega Sustenna's franchise line,
   Actelion's 2017 acquisition year, the Tyvaso split-quarter cases.
4. **A corpus end-to-end run**, once someone decides the LLM budget is worth
   the number. Everything before that is a sample.
