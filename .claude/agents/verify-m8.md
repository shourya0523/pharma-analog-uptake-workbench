---
name: verify-m8
description: Verifies the analytics method claims - percentile intensity banding, the observed-peak failure shapes, inverted similarity handling and the scope-blind uptake denominator.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M8: `analytics/`, `imports/peak_sales.py`. Items 4a-4f.
Depends on M5 and M7 reporting.

Every item here is a claim about a pure function, so every one can be settled
by calling it. Call it. Do not read the code and reason about it.

- **4a** `categorize_snapshots` bands by percentile for cohorts >= 6: construct
  six snapshots with identical scores and rosters and show the three bands;
  show empty-market and eight-rival cohorts landing in the wrong bands;
  reproduce the 9/20 agreement with gold's rule and gold's 15/3/2 distribution.
  Check which branch sets `low_coverage`.
- **4b** the four peak shapes - a series starting after the peak, a five-year
  hole, a year counted twice, and multiple scopes returning None - plus the
  distinct-period gate at `:113` summing all rows at `:118`.
- **4c** unknowns dropped from the denominator promote the candidate: reproduce
  Revatio -> Cialis and Winrevair -> Mounjaro over the 62 gold profiles, and
  the two-attribute candidate scoring 1.0. Confirm `ProductProfile` has no
  `indication_area`, and that `_similarity` compares case-sensitively so
  `ORAL` vs `Oral` returns 0.0 rather than unknown.
- **4d** the uptake denominator is a bare float; the `missing_reason`
  precedence reports `nonconsecutive_quarters` for consecutive quarters;
  `time_to_ninety_percent_peak` applies no scope filter, admits undated rows
  and returns an observation rather than a time.
- **4e** the three intensity methods and their 9/20 agreement; `compare_to_rule`
  having no caller in `app/`.
- **4f** that no eval scores any of this.
