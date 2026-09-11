# Evaluation

There is one eval, and it speaks to the pipeline the way a person does.

    cd backend && ./.venv/bin/uvicorn app.main:app --port 8000    # in one shell
    python scripts/eval.py --cases seed/cases/gold_sample.json    # in another

It POSTs a run, waits for the jobs, reads the datapoints back, and scores what
the pipeline **published** - `auto_pass`, the only status it will stand behind
without a reviewer. It imports nothing from `app`, so there is no second
implementation of the pipeline for it to drift from.

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
| `gold_sample.json` | 8 runs, 32 quarters | `seed/gold/quarterly_revenue.jsonl` |
| `gold_all.json` | every product-year in gold, one run per window | `seed/gold/quarterly_revenue.jsonl` |
| `foreign_xbrl.json` | 7 runs, 7 quarters | the figure printed in the filing each case cites |
| `unseen.json` | issuers no answer key uses, one quarter each | the figure printed in the 10-Q each case cites |

`value_normalized_usd_millions: null` means the run must come back with
nothing for that quarter. A set that only refuses is passed by a system that
always refuses, so both answers are represented.

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
