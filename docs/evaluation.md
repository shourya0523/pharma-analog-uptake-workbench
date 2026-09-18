# Evaluation

There is one eval, and it speaks to the pipeline the way a person does.

    cd backend && ./.venv/bin/uvicorn app.main:app --port 8000    # in one shell
    python scripts/eval.py --cases seed/cases/gold_sample.json    # in another

It POSTs a run, waits for the jobs, reads the datapoints back, and scores what
the pipeline **published** - `auto_pass`, which it stands behind without a
reviewer, and `confirmed`, which a reviewer has stood behind. A figure held for
review is neither: it counts as no answer rather than a wrong one. The eval
imports nothing from `app`, so there is no second implementation of the
pipeline for it to drift from.

## Why there is only one

There were nineteen. Two called `run_job` and the other seventeen called the
readers directly, each carrying its own idea of how the stages compose. Every
one of them was right about the function it called and silent about the
product:

- a coverage figure that excluded the LLM extractor, the evidence judge,
  reconciliation and the search fallback was quoted as the pipeline's accuracy;
- the tagged reader scored perfectly for a whole branch in which the pipeline
  published nothing it produced, because the instances were being dropped
  before it ran and its citations could not clear the judge.

Neither was visible to a script that called the reader itself.
`backend/tests/test_the_eval_runs_the_pipeline.py` now fails the suite if any
eval imports `app`, which is the property those scripts were missing.

## Case files

A case is what someone would type - the drug, who makes it, the ticker, the
window - plus the figures the run should come back with and where they came
from. They live in `seed/cases/`:

| file | cases | oracle |
|---|---|---|
| `gold_sample.json` | the smoke test: one product-year per issuer, plus one case of each way a case can expect nothing | `seed/gold/` |
| `gold_all.json` | every product-year gold holds, one run per window | `seed/gold/` |
| `foreign_xbrl.json` | one foreign filer's 6-K, whose figures are tagged on axes of its own: a product line, an aggregate of the rest, a line covering two products, a six-month-only figure, and a product it does not sell | the figure printed in the filing each case cites |
| `unseen.json` | issuers no answer key uses, one quarter each | the figure printed in the 10-Q each case cites |
| `shapes_holdout.json` | issuers no answer key uses, drawn by the shape of what the filing prints (item 0 of `docs/plan-after-the-full-sweep.md`); both answers represented | the filing each figure cites by accession; an empty quarter says why |
| `holdout_2026_09.json` | issuers no answer key uses, drawn for the fixes in `docs/plans/2026-09-17-006-what-twelve-reviews-found.md`; the file's own `note` says what spends it, and no case states a CIK | the filing each figure cites by accession; an empty quarter says why |

`value_normalized_usd_millions: null` means the run must come back with
nothing for that quarter, and says `why`. A set that only refuses is passed by
a system that always refuses and a set that only publishes is passed by one
that publishes anything, so both answers are represented in every file here -
a claim `backend/tests/test_the_eval_runs_the_pipeline.py` holds each file to by
globbing the directory, so a file added later is covered without this table
being edited. The same test holds every case to the options the API ships.

How many cases and expectations a file holds is not written here: the header
`scripts/eval.py` prints above every score counts them from the file it read,
and a count kept by hand is a number that goes stale in silence.

The two gold files are not maintained by hand - they are gold restated in the
shape a caller types, and a hand-maintained copy of an answer key goes stale
without saying so:

    python scripts/build_gold_cases.py          # rebuild both from seed/gold/
    python scripts/build_gold_cases.py --check  # say whether they still match

The figures come from `quarterly_revenue.jsonl`. The empty expectations come
from gold's own records of absence: `series_coverage.jsonl`, where a series
ends because the issuer stopped printing the line, and `excluded_products.jsonl`,
the products gold refused to build a series for at all.
`backend/tests/test_gold_cases_are_derived.py` fails when the committed files
and gold disagree.

Adding a held-out set is a new file, not a new script.

## A full run

`gold_all.json` is hours of work for the server and a client that dies
half-way orphans every run it started, so the client can rejoin:

    python scripts/eval.py --cases seed/cases/gold_all.json --attach

`--attach` scores the runs already on the server for each case's window
instead of starting them again. A job that failed - the model endpoint was
unreachable, the server restarted - is scored as it stands; run those cases
again as fresh runs (`--case DRUG`, or a case file holding only them, without
`--attach`) and read the two outputs together.

## Checking by hand

The score says a published figure matched the answer key. It does not say
the figure is in the document the pipeline cited, which is what a reader will
check first:

    python scripts/check_by_hand.py --run <run_id>

For every published quarterly figure it fetches the cited document and looks
for the figure beside the product's name; for a tagged fact it finds the
context that names the product and the fact filed against it, and then the
same figure in the printed filing beside the instance. A figure it cannot
find is either a document it cannot read or a wrong publication; look at
each one.

## Reading the output

    published, correct     the score
    published, WRONG       what a caller is given and cannot check
    held for review        the judge's cost, or its catch
    no answer              a gap
    correctly silent       a case that expected nothing and got nothing

`published-correct by method` is the line to watch after a change to a reader:
it says which reader actually answered, and a reader that answers nothing is
how a dead path hides.

## Gold is an oracle, not an input

Gold scores the pipeline and never reaches it;
`backend/tests/test_gold_is_not_an_input.py` enforces that. A change is
measured on a set it was not built from - see rule 4 in `CLAUDE.md`.
