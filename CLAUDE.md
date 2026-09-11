# Working rules for this repository

Three rules. They exist because each was broken, and the cost was not a wrong
number - it was a measurement that had stopped meaning anything, or a
conclusion asserted from something that looked like evidence.

Two of them are enforced by tests. The first is not enforceable, and is the one
that goes wrong most.

---

## 1. A claim about data needs the command that showed it

Do not state what a file, a column, an API or a document contains without
running something that shows it, in the same turn. This applies hardest when
the claim feels too obvious to check - that is the condition under which it has
always been wrong here.

Some real ones:

- "NUM carries the filer's own `decimals`" - the column is `dcml`, and INF is
  written `32767`. Read under the XBRL name it is always empty, which is
  indistinguishable from a filer that declared nothing, so every bulk-read fact
  would have been silently unbounded.
- "The pre-acquisition quarters are in no SEC filing" - stated after listing
  filings with `form in ("8-K", "10-Q")`. The Actelion financial statements are
  an `8-K/A`. The conclusion survived; the scan that produced it did not.
- "The `seed/xbrl_members.csv` pattern applied to URLs" - two files sharing a
  format do not share a property. See rule 2.

The habit that works: when about to write "X contains Y", run the thing that
prints Y first. When reporting, show the number rather than the impression -
and say plainly which parts were measured and which were inferred.

A corollary for scans: a filter is part of the claim. `form in ("8-K", "10-Q")`
excludes `8-K/A`; `endswith("_htm.xml")` excludes every pre-2019 instance;
`row.get("decimals")` excludes a column named `dcml`. State what the filter
was, or the absence you found is the filter's, not the data's.

---

## 2. Nothing the pipeline reads may come from the answer key

`seed/gold/` scores the pipeline. `seed/product_attributes.csv`,
`seed/xbrl_members.csv` and `seed/example_drugs.csv` are what it runs on. The
dependency runs one way: reference data may flow into gold, never back.

`backend/tests/test_gold_is_not_an_input.py` enforces this in three shapes -
application code naming gold, a script that reads gold and writes an input, and
a pipeline input whose text carries gold's own URLs or quotes. The third exists
because the failure arrived as a hand-written file, which the first two do not
watch.

**Before adding any file the pipeline reads, answer the delete test in the
commit message: remove the file, and does the pipeline still work on a product
it has never seen?**

- `seed/xbrl_members.csv` passes. Delete it and 76 of its 363 members still
  resolve from the string rules, and the other 287 go to the model that decided
  them originally. It costs 287 calls. It is a **cache** in front of a
  procedure that works without it.
- A table of document URLs fails. Delete it and nothing produces a URL from a
  product name, because no such procedure exists. It would be the **mechanism**,
  and its rows would be gold's `source_url` column.

Cost in time is a cache. Cost in capability is the answer key wearing a
different hat.

---

## 3. Every scored change is measured on a set it was not built from

Gold is an oracle for finding defects, never a scorer for the fix. `seed/holdout`,
`seed/holdout2`, `seed/holdout_labels` and `seed/holdout_members` are each spent
on the change they were built for.

A new change gets a new held-out set, drawn from issuers none of the existing
answer keys use. `backend/tests/test_combined_name_holdout_is_held_out.py` is
the shape: it checks the property that makes the number mean anything rather
than the number.

Two things that follow:

- **Stop tuning before the set is exhausted.** Editing a prompt until it scores
  full marks on the set that is supposed to score it is fitting. Leaving one
  failure documented is worth more than a clean number nobody can trust.
- **A set that only refuses is passed by a system that always refuses.** Both
  answers must be represented, and the guard test should say so.

---

## Checks worth running

    cd backend && ./.venv/bin/pytest -q                     # everything
    ./.venv/bin/pytest tests/test_gold_is_not_an_input.py    # rules 2 and 3

Evals under `scripts/eval_*.py` score against held-out sets and need
`OPENROUTER_API_KEY`. They are not part of the test run, and a change to a
prompt is not finished until the relevant one has been re-run and its number
reported.
