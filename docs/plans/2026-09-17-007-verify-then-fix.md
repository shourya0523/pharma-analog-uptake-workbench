---
title: "plan: verify, then fix - the module order and the evidence rule"
date: 2026-09-17
type: plan
status: complete
---

# Verify, then fix

`006` is the findings register: what twelve reviews found, ranked by cost. This
is how it gets executed - module by module, with a verification gate in front
of every change, because the register itself was wrong three times in one
sitting and each time in the same way.

## Why this document exists

Section 2f of `006` was written, corrected, and corrected again within an hour.
The three claims were:

1. "Retrieval is EDGAR-only, so 41% of gold's evidence is out of reach."
   Wrong: the J&J and Gilead IR documents are on EDGAR as EX-99 with item 2.02.
2. "The Actelion 8-K/A is the one filing that unlocks quarters nothing else
   reaches." Wrong: it holds company-level CHF statements.
3. "`positional.py` must be kept because 29% of gold's rows are PDFs." Wrong:
   those PDFs are the readable copy of HTML that is on EDGAR.

Every one was a claim about what a document contains, asserted from something
other than the document. (1) was inferred from a count of citations. (2) was
inferred from `"OPSUMIT" in text.upper()` returning `True`. (3) followed from
(1). None of the three was caught by a reviewer, a test or a reader - they were
caught by eventually opening the file.

CLAUDE.md rule 2 already says this. The rule did not fail; applying it only to
*new* claims did, while claims already written down were treated as settled. So
this document adds one thing to the rule: **a claim's status travels with it,
and a change may not be built on a claim that has not been verified in the
session that builds it.**

## The evidence rule

Every item in `006` carries one of three states:

    [V]  verified - a command was run in the session making the claim, and its
         output is quoted. Re-verification is still required before acting, but
         the command exists and is known to answer the question.
    [I]  inferred - reasoned from something adjacent. The reasoning is stated.
         No command answers this claim directly.
    [U]  unchecked - carried from a review, a doc or an earlier plan, and not
         re-run since.

**No change is implemented from an `[I]` or `[U]` claim.** Promote it to `[V]`
first, in the same session, or drop the item. A promotion that fails is a
finding: record what the claim actually turned out to be, and re-rank.

### Three tests a verification must pass

1. **The command answers the claim, not a neighbour of it.** "Does this
   document carry Opsumit's quarterly revenue" is not answered by
   `"OPSUMIT" in text`. **A name in a document is not a figure in a document.**
   Print the span - the number beside the label - or the claim stays `[I]`.
2. **State the filter, because the filter is part of the claim.** A count
   produced by a scan carries the scan's predicate. `form in ("8-K","10-Q")`
   excludes `8-K/A`; `seed/*/*.json` excludes `seed/holdout_foreign_xbrl.json`;
   `"OPSUMIT" in text` excludes nothing and therefore proves nothing.
3. **A count is not a capability.** "41% of gold's rows cite a non-SEC host" is
   a fact about gold's citations. "41% of gold's evidence is unreachable" is a
   fact about our retrieval, and needs a separate command against our
   retrieval. Do not let the first sentence become the second.

### What a module reports

For each item it owns:

    item   status_before -> status_after   what was run   what it turned out to be

An item whose meaning changed on verification is the most valuable output a
module produces, and is reported first, ahead of any fix.

## Module order

Each module is a gate: verify its items, report, then change, then measure. A
module does not begin until the modules it depends on have reported.

| # | module | code | `006` items | depends on |
|---|---|---|---|---|
| M0 | Baseline and eval | `scripts/`, `seed/cases/`, `seed/gold/` | 0a-0e | - |
| M1 | Surface truth | `api/`, `export/`, `dashboard/`, `frontend/` | 1a,1b,1d,1e,1f | M0 |
| M2 | Parsing | `parsing/` | 5b,5c,6a,7a,7c | M0 |
| M3 | Judging | `llm/`, `prompts/`, `quality/` | 6b,6c,6d,7d | M2 |
| M4 | Orchestration | `pipeline/`, `jobs/` | 1c,5a,5d,5f,6e,6f,6g,6h,7b | M2,M3 |
| M5 | Series identity | `pipeline/`, `export/`, `dashboard/` | 2a,2b,2c,2d,2e | M4 |
| M6 | Retrieval and identity | `connectors/sources.py`, `identity/` | 8, 9c, 12 | M0 |
| M7 | Characterisation | `connectors/openfda*`, `parsing/fda_label.py`, `quality/profile.py` | 3a-3i | M6 |
| M8 | Analytics method | `analytics/` | 4a-4f | M5,M7 |
| M9 | The project's account | `tests/`, `docs/`, `CLAUDE.md` | 9a-9i | - |
| M10 | Delete and infra | various | 10, 11 | all |

**M0 and M9 can start immediately and in parallel with each other.** M0 gates
everything that carries a number; M9 gates nothing but fixes the traps.
**M6 is the other early one** - it gates M7, which gates M8, and that is the
longest chain in the table.

### Why this order and not `006`'s

`006`'s order of work ranks by cost to the analyst, which is right for deciding
what matters and wrong for deciding what to do next, because it interleaves
modules and so interleaves context. A module-shaped order means one body of
code is in hand at a time, its assumptions are checked together, and a claim
that turns out false is found before anything downstream is built on it.

The cost ranking still governs *within* a module, and M0 still comes first for
the reason `006` gives: until the baseline is honest, no measurement means
anything.

## The parallel pass

The verification pass is run by one agent per module, concurrently where the
dependency table allows, each holding only its own module's code and its own
items. This is a context-management decision as much as a throughput one: the
register is 1,600 lines and no single reader holds it and the code at once,
which is how a stale claim survives a re-read.

Agent definitions are in `.claude/agents/verify-*.md`, one per module, sharing
`.claude/agents/_verification-protocol.md`. Each agent:

- re-runs the command behind every `[V]` item it owns, and reports whether the
  output still matches;
- promotes or fails every `[I]` and `[U]` item it owns;
- reports items whose meaning changed **first**, before any proposal;
- changes nothing. Verification and implementation are separate passes, because
  an agent that can fix what it finds has a reason to prefer finding it.

Wave 1: M0, M6, M9 (no dependencies among them).
Wave 2: M1, M2 (M0 reported), M7 (M6 reported).
Wave 3: M3 (M2), then M4 (M2,M3), then M5 (M4), then M8 (M5,M7).
Wave 4: M10.

Each wave's reports are folded into `006` - status markers updated, items
re-ranked, items that failed verification rewritten or removed - before the
next wave starts. That fold is the step that would have caught 2f.

## What this does not change

- Rule 3 still holds: nothing a module builds may read `seed/gold/` or
  `seed/product_attributes.csv`.
- Rule 4 still holds: a fix found with gold is scored on a set gold does not
  touch, and both answers must be represented in it.
- `006` remains the register. This document is the procedure over it, and
  carries no findings of its own.

## Where the agents went

The pass is complete. The eleven one-shot `verify-m*` definitions it ran are in
`docs/plans/verify-agents/` beside this document, as the record of it; the
reusable method (`.claude/agents/_verification-protocol.md`) and the reviewer
definitions stay where an agent can be invoked from them.
