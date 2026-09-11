# Working rules for this repository

Five rules. They exist because each was broken, and the cost was not a wrong
number - it was a measurement that had stopped meaning anything, or a
conclusion asserted from something that looked like evidence.

Rules 3 and 4 are enforced by tests. The rest are not enforceable, and are the
ones that go wrong most. Rule 1 is how the others get broken.

---

## 1. Derive the list; do not write it down

Auditing this branch end to end: every failure of rules 3 and 4 - the two about
the answer key - was a hand-maintained list. Rule 2's failures split, about
evenly, between a list used as a filter and a generalisation from one or two
observations. Nothing else accounted for any of them.

(The first draft of this paragraph said "every single failure, all of them",
which was itself a generalisation nobody had counted. Rule 2 applies to this
file.)

    what was written down              what it silently excluded
    ---------------------------------  --------------------------------------
    form in ("8-K", "10-Q")            8-K/A, which held the Actelion financials
    name.endswith("_htm.xml")          every instance filed before inline XBRL
    row.get("decimals")                the column, which NUM calls `dcml`
    PRODUCT_AXIS = "srt:..."           the same axis before the 2018 taxonomy
    KNOWN_PEER_BRANDS                  every brand that is not one of gold's 21
    three answer-key filenames         seed/holdout_labels, and gold's own
                                       annual_revenue.jsonl
    "gold" meaning quarterly_revenue   gold is a directory

Each of those reads at a glance as a reasonable list. Each produced a confident
absence that belonged to the list rather than to the data.

The same audit found the derived versions all held:

- `test_capabilities_are_wired` rglobs for capabilities instead of naming four
- `DOCUMENT_FITNESS` ranks the `SourceType` enum exhaustively
- `_instance_document` anchors on the filing's own `.xsd` stem, so it needs no
  ticker, no period and no adoption date
- `names_a_competing_product` reads the sibling rows the document prints,
  instead of a catalogue of brands we happen to hold
- `_declared_slack` takes the tolerance from what the sources declared, instead
  of a fraction of the value

So: when about to write a literal list of names, columns, forms or files, ask
what produces that list. If the producer can be reached at run time - a glob, an
enum, an index, the document itself - use it. Where a literal really is
necessary, say in a comment what it is a snapshot of and what would make it
stale.

Rule 2's corollary about filters is this rule in its most common disguise.

---

## 2. A claim about data needs the command that showed it

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
  format do not share a property. See rule 3.

The habit that works: when about to write "X contains Y", run the thing that
prints Y first. When reporting, show the number rather than the impression -
and say plainly which parts were measured and which were inferred.

A corollary for scans: a filter is part of the claim. `form in ("8-K", "10-Q")`
excludes `8-K/A`; `endswith("_htm.xml")` excludes every pre-2019 instance;
`row.get("decimals")` excludes a column named `dcml`. State what the filter
was, or the absence you found is the filter's, not the data's.

---

## 3. Nothing the pipeline reads may come from the answer key

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

- `seed/xbrl_members.csv` passes. Delete it and the members that the string
  rules place still resolve; the rest go to the model that decided them
  originally, at one call each. It is a **cache** in front of a procedure that
  works without it. Do not quote a count here - the register grows and the
  rules change under it, and the split is a `match` over the register away:

        from app.extraction.members import load_register, load_products, match
        sum(1 for _, m in load_register() if match(m, load_products()).resolved)

  A cache only holds if a stored decision cannot answer a question it was not
  asked. A negative recorded against one candidate list is not an answer for a
  different list, and consulted as though it were, it stops the rules running
  at all - which is the same file being the mechanism after all.
- A table of document URLs fails. Delete it and nothing produces a URL from a
  product name, because no such procedure exists. It would be the **mechanism**,
  and its rows would be gold's `source_url` column.

Cost in time is a cache. Cost in capability is the answer key wearing a
different hat.

---

## 4. Every scored change is measured on a set it was not built from

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

## 5. A comment explains the code and nothing else

A docstring says what the thing does, what it returns, and why a choice that
looks arbitrary is not. That is all.

Not in a comment or docstring: a measured result, a score, a count, a
before-and-after, which eval produced it, what a past run showed, how a defect
was found, what was tried first. Those belong in the commit message, where
people look for history, and in the eval's own output, where the number can be
produced again.

The test: could this sentence be made false by a change to the code alone? Then
it belongs here. Could it only be made false by re-running something? Then it
belongs in the commit message.

A number written in a comment cannot be checked from the file it sits in. It
goes stale silently, and it reads with the same authority as the code beside
it - so the next reader inherits a claim they have no way to test, which is
rule 2's failure with a longer fuse.

An example of *shape* is different, and is often the clearest thing available:

    HIVProductsBiktarvy resolves to Biktarvy; TyvasoDPI does not resolve
    to Tyvaso.

That is the rule made legible, not a result. Keep those.

---

## Checks worth running

    cd backend && ./.venv/bin/pytest -q                     # everything
    ./.venv/bin/pytest tests/test_gold_is_not_an_input.py    # rules 3 and 4

Evals under `scripts/eval_*.py` score against held-out sets and need
`OPENROUTER_API_KEY`. They are not part of the test run, and a change to a
prompt is not finished until the relevant one has been re-run and its number
reported.
