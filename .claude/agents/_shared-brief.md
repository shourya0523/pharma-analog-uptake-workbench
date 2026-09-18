> **Superseded.** This brief framed the product as layer 1 only - "name a drug,
> get its quarterly revenue with a citation" - and seeded each reviewer with
> known defects to bound. Both biased the pass: reviewers confirmed claims
> rather than reading the logic, and nobody examined the analog work the
> product exists for. Kept for the record. New reviewers use
> `_product-brief.md`.

# Shared brief for the pipeline review agents

Not an agent. The common instructions each reviewer inlines.

## What this product is for

A user names a drug and a window and asks: what were this product's quarterly
revenues, and where is each figure stated? The pipeline answers by finding the
issuer's filings, reading figures out of them, and publishing only what it can
show a citation for. Everything is judged against that job:

1. Find the filings that report this product's sales.
2. Read a figure whose product, period, scope and unit are what the document says.
3. Publish it with a quote a person can check, or say why there is none.
4. Never publish a figure that is not this product's own.

A module earns its lines by serving one of those. Say plainly where it does not.

## What to report

- **Dead code** - unreachable, never called, a flag nobody reads, a parameter
  every caller defaults, a branch no input can reach.
- **Broken code** - wrong on inputs it will actually meet. Show the input.
- **Edge cases** - real filing shapes it mishandles. Prefer ones you can point
  at in the cached corpus over ones you imagine.
- **Redundancy** - two things doing one job, or a second implementation of
  something the codebase already has.
- **Jobs-to-be-done gaps** - work the product needs that nothing does.

## How to report it

Rule 2 of CLAUDE.md governs: a claim about code or data needs the command that
showed it, in the same message. Do not say "X handles Y" - run something that
proves it. Cite `file.py:line`. Quote the command and its output.

Rank findings by what they cost the user, not by how interesting they are. A
tidy-up that changes no output is worth less than one wrong published figure.
Say which findings you verified and which you are inferring.

Do not edit any file. This is a review.

## Evidence available

- Real cached SEC filings: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/run7/storage/cache/sec/` (~550 documents)
- Run databases with real datapoints: `.../scratchpad/run13/workbench.db` (and run8, run10, run12)
- Tests: `backend/tests/`, run with `cd backend && ./.venv/bin/pytest -q`
- Python: `backend/.venv/bin/python`

## Measured context (do not re-derive; challenge if you find it wrong)

Leave-one-out over 75 scored figures - the change in correct answers when each
reader's rows are removed: xbrl_fact -18, llm -6, derived -4, prose -2,
table 0, web-search 0. Of 477 stored datapoints, 69.4% end `needs_review`;
70% come from the web-search fallback and 92% of those are unpublishable.
