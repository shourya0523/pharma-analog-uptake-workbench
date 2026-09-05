# Contributing to the readers

The pipeline reads product revenue from documents it has never seen. That
only stays true if the code never learns the shape of the documents it has
seen. This page says where a fix may go and where it may not.

## Two kinds of content, two kinds of code

A document has *meaning* (this column is Q3 2025 United States; this row is
the product's own revenue; this sentence is guidance) and *arithmetic*
(US + International = Total; the change column equals the two values;
nine months bound the quarters; the quote contains the figure). Meaning
varies without bound across issuers and years. Arithmetic does not.

- The **model** states meaning, as a structured description under the
  fingerprint contract (`backend/app/prompts/region_fingerprinter.yaml`).
- **Code** sources, converts, grounds, verifies arithmetic, reconciles,
  assembles and scores. It accepts a description only where the document
  prints what the description says and the numbers agree.

A wrong description therefore costs a value and is reported (and gets one
repair round). It never invents one.

## The model path is the scored path

`--mode model` is the only mode the gates measure. Its modules are:

| Module | Job |
| --- | --- |
| `app/fingerprint/llm.py` | sketch the document, call the model, ground its answer |
| `app/fingerprint/triage.py` | decide whether a document is worth a model call (fails open) |
| `app/extraction/described.py` | place the described rows and verify them |
| `app/extraction/columns.py` | column arithmetic: placement, change and sum checks |
| `app/extraction/series.py` | reconcile across documents, derive, identify by agreement |
| `app/sourcing/edgar.py` | enumerate every document of every filing |
| `app/benchmark/schema.py` | compare to a reference at the precision it states |

`tests/test_no_shape_rules_on_model_path.py` locks this down:

1. none of these modules imports `app/extraction/degraded` or a legacy extractor;
2. every regular expression in them is listed in
   `tests/fixtures/model_path_regexes.json` with a one-line reason, and the
   only reasons accepted are number formatting, EDGAR artefacts, contract
   format validation, money or revenue detection for triage, and label
   normalisation for grounding or scoring;
3. `app/extraction/degraded/` is frozen by hash.

## Degraded mode is unscored

`--mode degraded` runs the header grammar and the regex prose reader for a
run with no model available. Its numbers are printed and never gate
anything. A change under `app/extraction/degraded/` counts toward nothing;
if you make one deliberately, regenerate the freeze
(`uv run python -m tests.freeze_degraded`) and say so in the commit.

## When a gate row fails

Find the cause in this order, and fix it only in the file named:

| What went wrong | Where the fix goes |
| --- | --- |
| The model was not shown the row, caption, footnote or heading | the sketch in `fingerprint/llm.py` |
| The model described it wrongly and the contract could have asked better | `prompts/region_fingerprinter.yaml`, `region_fingerprint_repair.yaml` |
| The description was right and the parser dropped it | the grounding parser in `fingerprint/llm.py` |
| The description was right and the reader could not verify it | `extraction/described.py` or `extraction/columns.py` |
| The value was read and the series chose another | `extraction/series.py` |
| The value is right and the comparison says otherwise | `benchmark/schema.py` |

If none of these applies, the fix is a new verification, not a new rule
about how issuers print things. A verification is a check that would hold
for any issuer: a subtotal equals its members, a document states each
figure once, a coverage span lies inside its period. Ask what arithmetic
the document offers before asking what words it uses.

## What is not allowed on the model path

- A vocabulary of header words, geography spellings, section names or
  product-line phrasings.
- A rule that decides what a row *means* from its label.
- Tie-breaking the model with a deterministic reading of the same header.
- A regex that is not in the manifest.

## Gates

- gold: `uv run python ../scripts/eval_pipeline.py --mode model --rendering markdown` and `--rendering raw`, 993/993, twice (the second from an empty description cache);
- held-out 1: `uv run python ../scripts/eval_holdout.py --mode model`, 111/111;
- held-out 2: a 6-K filer in a non-USD currency and a PDF-only issuer, 100%;
- `uv run pytest tests -q` green, `uv run ruff check app tests` clean.

Fixes between gate runs are confined to the files in the table above.
