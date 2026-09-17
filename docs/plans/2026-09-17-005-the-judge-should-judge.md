---
title: "note: the judge should judge, not match patterns"
date: 2026-09-17
type: note
status: direction-only
---

# The judge should judge

A note, not a plan. No code was written for it.

## What it is today

Every figure the readers produce is supposed to be checked before it is
published. The check is a bank of regexes.

Measured on `run13`, 549 datapoints from 24 jobs:

    settled by a hard veto      365
    settled deterministically   179
    reached the LLM judge         5

`try_deterministic_judgment` calls `apply_judge_hard_vetoes` first and returns
on a veto, so a vetoed row never reaches the model. Of those five, the model
changed the outcome on two - an R&D expense row read as revenue, which got to
it only because `label_not_understood` is the single branch that returns None.
All 62 tagged-XBRL rows and all 16 derived rows were never questioned by
anything.

So the layer named "evidence judge" adjudicates one row in a hundred, and the
publishing decision is made by pattern matching on the quote's characters.

## Why that is the wrong shape

A regex can ask whether two strings co-occur. It cannot ask the question the
job needs answered, which is whether *this document, read honestly, states
this number for this product and this period*.

The three vetoes that do the most work are all wrong in the same way, and each
is wrong because the thing it approximates is a reading task:

- `value_and_product_in_different_sentences` (326 rows, 212 of them with no
  other objection) splits on newlines, so a table row arrives as three
  "sentences" and the product is never in the same one as its figure.
- `quote_states_a_different_period` (142 / 27) reads the first period in the
  quote, so every prior-year comparative column is rejected.
- `ytd_language_as_quarterly` (52 / 2) reads the whole quote rather than the
  sentence carrying the value, though that sentence is already computed.

139 (drug, period) pairs, 63 of them calendar quarters, have no published
figure with nothing but these three in the way.

And the rule that *admits* figures is the same shape from the other side.
`deterministic:product_quote_value_ok` auto-passes when the product name and
the number appear in one sentence - 179 rows. Run against real quotes it
passes an impairment charge, an accounts-receivable balance, forward guidance,
a combined two-product line and a company total. It published AGAMREE 2023Q3
at $81.5m from "the $81.5 million IPR&D purchase consideration for the
acquisition of the license" - a quarter in which the product was not yet sold.

Adding a fourth pattern, and a fifth, is what produced twelve gates of which
five never fire and four are the same rule written three or four times.

## What it should do instead

Judge the claim against its evidence, the way a person checking the figure
would. That means being given what the pipeline already knows and is not
passing:

- the unit and currency, so 952 thousands is not read as $952 million - the
  model's own explanation in the one case it did catch says "$952 million"
- the geography and scope, so a U.S.-only figure claimed as Worldwide is
  visible
- the extraction method, source type, filing form and filing date, so a
  tagged fact can be weighed against a sentence
- the sibling rows of the same table, which is how a combined line is
  recognised without a catalogue of brands (`peer_names` exists for this and
  is never passed anywhere in `app/`)
- the filing's own tagged value for the same period, which today is only
  compared later, in reconciliation, where the judge cannot see it
- the footnote attached to the row, which reaches the quote on 2 of 549 rows
- the product's approval or launch date, so a figure before first sale is
  questioned. `profile.fda_approval_date` and the `early_launch` validation
  reason both already exist.

And it should return what a reviewer needs: which part of the evidence
supports the number, which does not, and what would settle it. The
`search_results` array the search-validator prompt already asks for - the
corroborating URLs - is produced and then dropped.

## What the patterns are still for

Not "delete the vetoes". A cheap deterministic pass in front of a model is
right where the answer is not in doubt: an empty quote, a value absent from
the document, a period the filing cannot cover. The error is that the fast
path currently decides 544 of 549 rows, including every genuinely doubtful
one, and that its rules are approximations of reading rather than facts about
the data.

The split to aim for: the deterministic pass settles what is certain and hands
everything else to a judge that is actually given the evidence. Today it hands
over one row in a hundred.

## Before any of this is built

Rule 4 applies. The current numbers - 58.7% correct on the shapes holdout,
69.4% of stored rows held for review - come from a pipeline where the judge is
five calls. A judging change needs a held-out set drawn from issuers the
existing answer keys do not use, and it must contain figures that should be
refused as well as figures that should pass; a judge that refuses everything
scores well on a set of things to refuse.

The three broken vetoes above should be fixed first and separately, so that
the judging change is measured against a pipeline whose deterministic pass is
correct, not against one whose baseline is depressed by three bugs.
