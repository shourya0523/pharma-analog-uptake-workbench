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
after which the ranking decides between them. Confirmed on a clean corpus run:

| | base | with both |
|---|---|---|
| read | 1,307 | **1,313** |
| wrong value | 16 | **12** |
| not found | 80 | 77 |

Seven rows changed state and **no row regressed** — nothing that read correctly
stopped doing so.

All four recovered wrong values are Nebulized Tyvaso — 2013Q1, 2013Q2, 2019Q4,
2020Q3 — each a `prose_sentence` misreading replaced by a
`derived_sole_formulation` correct one. That is the product `003` predicted
would move.

The first attempt to measure this reported 1,261 and looked like a large
regression. It was a throttled run — 30 connector errors, 31 extra
`no_readable_filing` rows — and restricting to rows both runs could read
predicted exactly the +6 / −4 the clean run then produced. Section 5b is about
that.

It does not help a period no derivation reaches, which still receives a
sentence's answer: prose is now 5 correct against 9 wrong rather than 5 against
13.

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

| | first run | with the retrieval fix |
|---|---|---|
| published, correct | 15/24 | **24/24** |
| published, wrong | 0 | 0 |
| **held for review, correct** | **0** | **0** |
| no datapoint at all | 9 | 0 |

Of everything the pipeline found, it published all of it and got all of it
right — in the first run, and again when retrieval stopped losing nine
quarters. On the tuned sample the same pipeline had withheld 21 of 32 correct
answers. The fixes generalise.

The nine gaps were section 5a's exhibit budget and nothing else: fixing it took
the held-out set to 24/24 with no change to any reader.

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

## 5c. The held-out gates, re-run

`003` item 5 requires these after any reader change. Nothing here changed how a
number is read — the changes are to labels, gating, ranking and retrieval — and
the gates agree:

| gate | recorded in `003` | now |
|---|---|---|
| period detection, four filers absent from gold | 118 geometry / 80 ragged | 118 / 80 |
| PDF geometry agreement | 99.1% | 1319/1331, 99.1% |

`eval_pdf_geometry.py` needs `--render` on a fresh container. Without it every
document counts `not_rendered` and the script prints

    held-out documents rendered and read both ways: 0

which is a gate that did not run, not a gate that passed. The corpus itself
comes from `scripts/sourcing/fetch_holdout.py`, which
`eval_period_generalization.py --refresh` will call for you.

## 5d. A burst of jobs trips the key limit, transiently

An end-to-end run died with every job reporting

    403 Key limit exceeded (monthly limit)

and minutes later the same key answered a call with 200. It was not out of
credit: `GET /api/v1/key` reported `limit_remaining` just under 10 of a 90
monthly limit at the moment of failure. The limit is evaluated against usage
that lags, so a burst of concurrent jobs near the ceiling gets refused and then
succeeds again once the accounting settles.

Treat it as back-pressure rather than as an outage, and **check
`GET /api/v1/key` before concluding anything** — `limit_remaining`,
`usage_monthly` and `usage_daily` are all there. `run_job` calls the model in
`_identity` before it does anything else, so a job that meets this dies at
`identity_resolve` and reports zero for every quarter of its window.

**A run in that state prints `0/32` and looks exactly like a measurement.** It
is not one. Check the per-job `step` column — `identity_resolve` or
`judge_metadata` with an ERROR beside it means the run never happened. The one
that exhausted the budget has been deleted rather than left on disk where it
could be mistaken for data.

Everything that does not call a model still works and is how the corpus
numbers in this document were produced: `eval_coverage.py`,
`eval_period_generalization.py`, `eval_pdf_geometry.py`,
`eval_provenance.py`, `eval_tagged_provenance.py`.

What a session costs, so the next one can budget rather than guess. This
session's seven end-to-end runs of 6-8 jobs came to **$5.98** against a $90
monthly limit, most of which was already spent by others before it started. A job is not one call — `_identity` expands
aliases, `_judge_profile` judges *every* content field with search
(`profile_judge_max_fields = 0` means no cap), and the evidence judge runs per
datapoint where the deterministic fast path does not short-circuit. The
revenue extraction itself is the cheap part.

Two ways to spend less next time. `--no-metadata` skips the profile stages,
which answer no revenue question and are most of the per-job cost. And record
every field you might want *before* the first run: this session re-ran the same
32-quarter sample largely to add one field to the output, which `--rescore`
cannot recover after the fact.

## 5e. Which model to run, and why the two calls differ

The extract call and the judge call are not symmetric, and that decides this
more than price does.

**Extract is deterministically gated.** `filter_revenue_candidates` is called
with `source_text`, and drops any candidate whose quote is not verbatim in the
document. A weak model there can omit a figure; it cannot invent one.

**The judge is not gated.** Its verdict writes `validation_status`, and it is
the only thing between a reader's error and a published wrong value.

And the extract call is buying very little. Over all 56 quarters of the sample
and held-out runs, the LLM extractor answered **zero** gold quarters that a
deterministic reader had not already answered; it added six duplicates and two
wrong values, both of which the judge held.

That argues for a cheap extract model, and **the measurement says not to
bother**. Three runs of the same six held-out jobs, cost read off
`GET /api/v1/key` either side:

| configuration | result | cost |
|---|---|---|
| `gpt-4o-mini` extract, metadata on | 24/24 | ~$0.85 |
| `gpt-4o-mini` extract, metadata **off** | 24/24 | **$0.1077** |
| `gpt-oss-20b` extract, metadata off | 24/24 | $0.1116 |

The cheaper extractor saved nothing — it came out marginally *dearer*, which is
noise, and the point is that the difference is noise. **The entire saving is
`--no-metadata`**, about eight times, and the extract model is not a meaningful
cost component because the extract calls are a small share of a job.

So: **keep `openai/gpt-4o-mini` for both.** Switching the extractor buys no
money and spends a known quantity for an unknown one. The lever is the profile
stages, not the model.

| call | model | blended $/M | note |
|---|---|---|---|
| extract | `openai/gpt-4o-mini` | 0.200 | no cheaper model measurably reduces a run |
| judge | `openai/gpt-4o-mini` | 0.200 | 4/4 catch, 0 false publishes — no headroom above, real risk below |

All three runs published `{'table': 30}` and nothing else: **not one LLM
datapoint survived the verbatim gate on any of them**, whichever extractor ran.
On this era the deterministic table reader is doing all of the work.

Blended at roughly 8:1 in:out, which is this pipeline's shape. The judge is
where a model choice still costs real money, because it serves the majority of
calls including the web-search ones: `openai/gpt-5.6-luna` is 0.311 and
**`openai/gpt-4o` is 3.333**, 16.7× the judge model actually used. An
environment setting `gpt-4o` as judge is the single most expensive thing about
a run.

Any replacement must support `response_format: {"type": "json_object"}`, which
the client sends on every call. Filtering OpenRouter's model list on that plus
a 32k context is the shortlist; several models cheaper than `gpt-oss-20b` do
not support it.

**Do not generalise "the extractor adds nothing" past 2018-19.** Both runs sit
in the era where the earnings exhibit is well structured. The extractor is
exactly what might reach the `.txt`-era Remodulin quarters in next step 3, and
that has never been tested.

The larger saving is not a model. `_judge_profile` judges *every* profile field
with web search and `profile_judge_max_fields = 0` means uncapped. For any
revenue question, `--no-metadata` removes that stage entirely; aliases come
from `_identity`, which still runs, so revenue extraction is unaffected.

## 5f. The pipeline is unsafe before about 2010, and the judge is not the guard

`003` asked whether the LLM extractor already reaches Remodulin 2002-2009, the
largest block of misses. It was run, for eleven cents, and the answer is worse
than "no".

| | |
|---|---|
| published, correct | **3/32** |
| **published, WRONG** | **9/32, 28%** |
| no datapoint at all | 20/32 |
| **judge catch rate** | **1 of 10 wrong datapoints held back** |

Compare the same eval on 2018-19: 32/32 and 24/24, no wrong values, 4 of 4
caught. **The era is the variable, not the pipeline's quality.** Every
reassuring number this document reports comes from years where a
well-structured earnings exhibit exists.

Three mechanisms, all verified from the per-row artifact.

**The prose reader reads a total and calls it the product.** Eight of the ten
wrong values are its, and every one is high by 4-8%:

    2005Q1  gold 21.465  published 23.2   (+8.1%)
    2006Q1  gold 31.304  published 33.2   (+6.1%)
    2008Q3  gold 72.081  published 75.0   (+4.1%)

Never low, always a little high, which is what reading total revenues instead
of one product's net sales looks like. This is section 4's question answered
from a second direction: on the corpus the reader trades 5 correct for 9 wrong,
and in the era it was written for it publishes a systematic overstatement.

**The search judge corroborated them.** Those rows carry
`llm_search_validated` and `search_corroborated`. `judge_with_search` did not
merely fail to catch the error, it affirmed it — a figure close to the right
one is exactly what a search will find support for.

**A 1000× unit error was published.** Remodulin 2009Q3, gold 87.4, published
**87,400**, flagged `deterministic:product_quote_value_ok`. The reason it
passed is structural: `try_deterministic_judgment` checks
`candidate["value_reported"]` against the quote, and the figure a consumer gets
is `value_normalized_usd_millions`. The quote says 87.4, `value_reported` is
87.4, the check passes — and **nothing anywhere validates the normalized value
against the quote**, so a scaling error between the two is invisible to every
gate. A missing unit was already known to produce 1000× errors; this is the
same failure surviving the judge that exists to catch it.

The scale check added for the last of these was verified not to hold anything
back: the held-out set re-run with it in place is 24/24 again, and it fired on
zero of the 30 datapoints. The unit shapes the deterministic reader actually
emits — 1,514 thousands as 1.514, 2.5 billions as 2,500 — all agree with
`scale_to_millions`, so it is inert in normal operation. It is not measured on
the era that produced the error, because doing so needs the 2000s.

**What to conclude about the judge.** Its 4-of-4 catch rate at 2018-19 was
reported here as a strength. It is four datapoints, in an era where the
deterministic table reader produces almost no errors to catch. Given ten real
errors it caught one. The judge is not the thing keeping wrong values out of
the 2018-19 numbers; the table reader not making mistakes is.

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

## 7. Six ways this session was wrong, all the same way

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
4. **Believing a score from a run that could not reach its inputs.** A corpus
   run reported 46 fewer rows read and it was EDGAR throttling — 30 connector
   errors against a baseline's zero. A derivation cannot make a filing
   unreadable, which is the tell. Section 5b.
5. **Diagnosing an outage from one status code.** Announced the LLM budget was
   spent, on a 403 and a plausible story. `GET /api/v1/key` said just under 10
   remaining of 90, and the same key answered 200 minutes later. The endpoint
   that would have said so was never called. Section 5d.
6. **Letting a sound argument stand in for a measurement.** Recommended a
   cheaper extract model because the extract path is verbatim-gated and its
   unique contribution measured zero. Both true, and the switch saved nothing —
   a control run put the whole saving on `--no-metadata`. The argument
   established that it would be *safe*, never that it would *help*. Section 5e.

The first three are `003`'s rule about assignment sites. The last three are the
same rule one level up: a number, a status code and an argument, each believed
without asking what produced it. Every one was caught by a check that cost
seconds or cents, and every one had been reported before that check was run.

## 8. Next, in order

1. **Re-run the held-out set with the retrieval fix in.** 15/24 was measured
   against a connector that dropped three of its six filings; the ceiling on
   that number was retrieval, not reading, and it should now move.
2. **Decide the prose reader**, on section 4's numbers.
3. **Decide what the pipeline does before about 2010.** Section 5f: it
   publishes 28% of that era wrong and the judge catches a tenth of it.
   Refusing the era outright would be consistent with this project's own rule
   and is probably the honest short-term answer. The old text follows.

   ~~**Remodulin 2002-2009, 28 of the 90 remaining misses.**~~ `003` says "the LLM
   extractor may already handle these" and that has never been tested, because
   the end-to-end eval is the only thing that runs the extractor over the
   corpus. It was launched and died on the key limit above, so the question is
   still open and is now one command:

       python scripts/eval_pipeline_end_to_end.py \
           --product "United Therapeutics:Remodulin" --years 2002-2009

   If it reads them, the deterministic floor understates the pipeline in the
   era everyone has written off, and 92.8% is not the ceiling it looks like.
   The rest of item 4 is unchanged: Invega Sustenna's franchise line (21),
   Actelion's 2017 acquisition year, the Tyvaso split-quarter cases.
4. **A corpus end-to-end run**, once someone decides the LLM budget is worth
   the number. Everything before that is a sample.
