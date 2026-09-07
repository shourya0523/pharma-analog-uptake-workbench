# What the pipeline does

One product, one quarter, one number, and a citation that stands on its own.
Everything below exists to make that number either right or absent — a wrong
number that looks plausible is the failure this design is organised against.

## The run

`PipelineOrchestrator.run_job` (`backend/app/pipeline/orchestrator.py`) takes a
drug name and walks these stages, recording the current one on the job so a
failure names the step it happened in:

| stage | what it does |
|---|---|
| `_identity` | resolve the product: brand, generic, aliases |
| `_retrieve` | **find the filings** — the step every later score depends on |
| `_parse` | each document becomes text blocks and tables, twice over (below) |
| `_extract_metadata` | label and profile facts, mechanism, indications |
| `_judge_profile` | a model checks the profile against its own sources |
| `_extract_revenue` | the deterministic table reader, then a model pass |
| `_judge` | every candidate is checked against the document it cites |
| `_reconcile_with_llm` | conflicting candidates for one quarter are resolved |
| `_quality_and_validation` | checks that can fail a datapoint, and review tasks |
| `_completeness` | which quarters are still missing, and where to look |

## Retrieval is part of the job

`app/connectors/sources.py` resolves an issuer to a CIK, lists the 8-K filings
carrying item 2.02 in a window around the quarter, and reads the EX-99 exhibits
attached to them. Two rules there were each bought with a wrong answer:

- An ambiguous issuer name resolves to **nothing**. Prefix matching once
  resolved "United" to a company that was not United Therapeutics.
- **Every** EX-99 exhibit is read, not the first. Johnson & Johnson puts its
  press release in EX-99.1 and its product sales schedules in EX-99.2.

## A document, read as a rectangle

`app/parsing/documents.py` turns any filing into the same shape: a rectangle
where a cell's text sits at the column it starts in and the columns it
continues over hold `None`. What differs is where that structure comes from.

**HTML states it.** A cell declares its `colspan`, so the document says which
period covers which figures. Flattening to ragged rows throws that away and the
period then has to be guessed from prose.

**A PDF states nothing.** It is glyphs at coordinates. Columns are recovered
from the vertical whitespace no figure crosses — the classical method, after
Nurminen (2013), which Camelot's `stream` reader and pdfplumber's `text`
strategy both implement — and then a heading whose text crosses those
boundaries is treated as spanning them, which is the PDF's equivalent of
`colspan`. The threshold between a word space and a column gap is 1.5 median
character widths, measured: gaps within a cell cluster at 0.5–1.1, gaps between
columns at 2 and above, and 21 of 19,500 gaps fall in between.

`flatten_grid` reads a rectangle back as ragged rows, and `html_tables` is
defined in terms of it, so the two views of a document cannot drift apart.

Which tables are kept is `table_relevance`: a table is worth reading if it has
a line-item row — a text label followed by a figure. Position decides nothing;
a Gilead 8-K holds 39 tables and prints its product sales summary in the
thirty-seventh.

## Reading a number out of a table

`app/extraction/fingerprint.py` establishes what a table declares about itself
before any number is read: its unit, its currency, and which column holds which
period. An undeclared table yields nothing rather than a guess, because a
missing unit is exactly the condition that produced values wrong by 1000×.

A column's period comes from the headings covering it — but only where the
headings and the body use the same columns. A filer can span its headings and
not span its body, and then the rectangle describes a layout the numbers are
not in. Two checks catch that:

- a period may not cover a column the body puts a row label in;
- a row putting two of its figures under one period condemns the reading for
  the whole table, not just that row, because the rows that did not trip it
  were read against the same headings and are right only by luck.

Either sends the table back to the ragged reading, which infers the columns and
can refuse.

`app/extraction/extract.py` then reads the product's rows. Where several rows
name one product — an issuer reporting by region prints a line per region — the
row that names the product and nothing else is the product; failing that, the
row that is the sum of the others in every period is the total they add up to,
labelled or not. The arithmetic identifies it, so no list of region names
exists anywhere. Several lines and no total is refused: which one is the
product's revenue is exactly what the table has not said.

## Every number carries its receipt

A published datapoint quotes the passage it came from, and that quote must be
verbatim in the document cited, with the value present in it. A regional total
is quoted together with the lines it sums, so a reader can check the arithmetic
that identified it. `scripts/eval_provenance.py` audits this from outside.

## Where the numbers are checked

`app/extraction/check.py` and `app/quality/` run after extraction: a datapoint
failing a check is held back rather than published, and the finding names the
period and the reason.

## What this design is not

The filers tag product-level revenue in XBRL from 2019 onward, and the pipeline
reads none of it — it fetches 8-K earnings exhibits, which are untagged, and not
the 10-Q and 10-K, which are. Everything above about recovering a period from
geometry is inference standing in for something the filer has already declared.
See [`docs/plans/2026-09-07-002-structured-first-extraction.md`](plans/2026-09-07-002-structured-first-extraction.md).

## Reading further

- [`docs/evaluation.md`](evaluation.md) — what each eval measures, and which
  number is the pipeline's score
- [`docs/research/sec-table-period-context.md`](research/sec-table-period-context.md)
  — why a value's period comes from column geometry, and what that missed
