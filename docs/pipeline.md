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

## Four ways a number is obtained, in this order

Everything above describes the table reader, which is the third of four and the
only one that infers anything.

**What the filer tagged.** From 2019 the filers tag product-level revenue in
XBRL, and a tagged fact states its period, its unit, its currency and which
product it belongs to. There is nothing to recover from a layout, so this is
tried first and the citation names the element and the context rather than
quoting a line. `app/parsing/xbrl.py` reads the instance;
`app/extraction/tagged.py` decides which member is which product, through the
rules in `app/extraction/members.py` and the register those rules cannot
settle. When detail tagging began is a property of the issuer rather than a
date in the code: a filing from before its own cutoff simply tags no product
facts and says so.

The register lives in `xbrl_member_resolutions`, seeded from
`seed/xbrl_members.csv` and added to as runs resolve members the file does not
cover — the drugs a run is about arrive at upload time, and a file in the
source tree is not somewhere a worker can write. `scripts/build_member_register.py`
warms an issuer in bulk ahead of time; `scripts/export_member_register.py`
writes the table back out as the reviewable copy. A decision that a member
*names a product* is permanent. A decision that *nothing in the candidate list
matched* is stored with a fingerprint of the list it was judged against and
binds only for that list, because it is a fact about the list and not about the
member — without that, a member recorded as unplaceable while a drug went
untracked would still be unplaceable for the run that uploaded it. Only a
reviewer records the third verdict, `not_a_product`, which is a member that
names no single product at all: a category line or a total.

**What a sentence says.** Issuers disclosed product sales narratively long
before the product-sales exhibit existed, and smaller ones never adopted one.
`app/extraction/prose.py` reads a figure out of a sentence that names exactly
one period and one amount; anything ambiguous about which number belongs to
which period is refused rather than resolved by proximity. This is where United
Therapeutics' first eight years of Remodulin live.

**What a table prints.** The reader described above.

**What the issuer's own arithmetic implies.** `app/extraction/derive.py`
completes a series from figures already extracted: the quarter left implicit
against a stated total, and the family total attributed to the one formulation
on sale before a sibling appeared. Both are exact arithmetic over published
figures, applied only when uniquely determined, and marked as derived with the
inputs that produced them. A quarter that derives to zero or below is refused,
because a total that does not cover the year cannot be subtracted from.

Nothing here is enabled by a flag or read only by an eval: `app/` calls all
four, and `backend/tests/test_capabilities_are_wired.py` fails the suite if any
of them becomes reachable only from a script. That test exists because three of
them had been written, tested, measured in an eval and never called by the
pipeline, so the coverage figure described something the product could not do.

## Which of two answers wins

Two candidates for one quarter are ranked, never pooled, and there are two
rankings because there are two questions.

`SOURCE_PRIORITY` ranks the **document**: a 10-K or 10-Q above an 8-K earnings
release, and both above anything found by search. `CLAIM_STRENGTH` ranks the
**producer**, by how much it had to infer — a tagged fact states its own period,
unit and product; a schedule declares its unit and its columns; a derivation is
exact arithmetic over figures the issuer published; a sentence and a model's
reading are recovered from running text.

The second exists because the first cannot answer the question. A product-sales
schedule and the narrative around it sit in the same 8-K exhibit, so they tie on
source, and the tie used to be settled by whichever was extracted first — which
once meant a sentence reading 13.4 could beat a schedule reading 54.0.

They apply in two places. Reconciliation sorts by document, then claim, then
confidence, and marks the losers `needs_review`. And a derivation is computed
only from figures at least as strong as itself: `complete_series` treats any
candidate for a period as that period being answered, so a sentence misreading
a quarter used to stop that quarter's derivation being computed at all, which is
upstream of any ranking and looked exactly like a ranking that did nothing.

## What gets published

A datapoint leaves the pipeline as `auto_pass` — published — or as
`needs_review`, which is a gap awaiting a person rather than an answer. The
evidence judge decides on the quote, quality checks can override, and a
confidence below 0.7 fails the gate.

Be careful what is treated as evidence of doubt here. A deterministic fill that
restates something the row already says is not an estimate and must not be
flagged as one: writing `aggregate` into `formulation` because the scope is
already `Product family` once forced review and capped confidence at 0.55, and
that single rule withheld two thirds of everything the readers found.
`scripts/eval_pipeline_end_to_end.py` is the eval that can see this, because it
scores what was published rather than what was extracted.

## Reading further

- [`docs/evaluation.md`](evaluation.md) — what each eval measures, and which
  number is the pipeline's score
- [`docs/research/sec-table-period-context.md`](research/sec-table-period-context.md)
  — why a value's period comes from column geometry, and what that missed
