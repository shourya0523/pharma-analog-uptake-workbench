---
name: verify-m5
description: Verifies the series-fitness claims - duplicate readings per quarter, the geography vocabulary, identical curves for two products, and where the holes fall.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M5: the series as an artifact. Items 2a-2e. Depends on M4 reporting.

- **2a** 377 quarterly rows over 157 distinct (product, period) cells; 115
  `duplicate_period_scope_formulation` checks, all `open`, over 17 of 20
  products. And the layer-3 table: how many products yield >=1 and >=4 uptake
  points as-is, and after dedup plus a normalized vocabulary. Re-run
  `calculate_revenue_uptake` yourself; the register's claim that it slices four
  *rows* rather than four *quarters* is the load-bearing one, and the Nutrition
  case (10 clean quarters, 0 uptake points) is the test of it.
- **2b** 18 distinct `geography` strings, 63% null; `_has_compatible_scope`
  compares by exact equality; ELEVIDYS 2024Q2 carrying four scope labels for
  one value; ORLADEYO's 11 tuples.
- **2c** YUTIQ and ILUVIEN ship identical curves because every YUTIQ figure is
  read from an `ILUVIEN and YUTIQ` row. `ProductProfile` has no revenue-basis
  field. Compare against gold's `benchmark_identity`, which the register says
  is unique per series across all 55.
- **2d** the Q1/Q2/Q3/Q4 cell counts and the by-method split; 16 of 35
  `unresolved_quarters` are Q4s; DAYBUE 2023Q2 present in run10/12/13 and
  demoted in run13.
- **2e** `reported_as` on 12 of 549 rows; absent from `QUARTERLY_HEADERS` and
  the series payload. The AGAMREE 81.5 IPR&D row, the 19-day stub quarter, the
  Nutrition restatement, and `Nutrition [PRGO]` being a reporting segment.
  Also gold's `launch_quarter` vs `commercial_start_quarter` differing for 24
  of the 30 records carrying both.
