---
name: verify-m4
description: Verifies the orchestration claims - corroborator demotion, conflicting-values rejection, source priority versus claim rank, derivation provenance and the combined-line guard.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M4: `pipeline/`, `jobs/`. Items 1c, 5a, 5d, 5f, 6e, 6f, 6g, 6h, 7b.
Depends on M2 and M3 having reported.

- **1c** the alias list disarms the combined-line guard. The decisive test is
  `read_label("Total Pombiliti + Opfolda sales", <run13's merged alias list>)`
  returning no `combined_line`, and returning one with Opfolda removed. Re-run
  it with the aliases actually stored in run13, and count how many Pombiliti
  datapoints come from a combined line and how many are `auto_pass`.
- **6e** corroborators are computed from winners and never re-examined: 37
  quarters across six runs with a `corroborates` row and nothing published, 14
  corroborators themselves clean; and 10 of 157 quarter-cells in run13, 5 of
  them XBRL, table or derived, including series starts (2d).
- **6g** an XBRL instance inside a 10-Q is typed `QUARTERLY_REPORT` (priority
  4) while the human-readable document is `SEC_FILING` (0); 100% of tagged
  facts sit in the lower band and 32 were demoted to citations.
- **6f** `check.py:231` and `candidates.py:183` discard every reading of a
  period with an error finding: 20 gold-correct readings, including Pombiliti
  2025Q1 = 21.005 carrying the `reported_as` the replacement lacked.
- **6h** 65 groups across six runs mix period types under one period label.
- **5a** 13 of 16 derived rows cite a document not containing their input, 12
  of those `auto_pass`; `DerivationLineageORM` never written.
- **5d, 7b** `_candidate_of` drops `rounding_uncertainty_usd_millions`, so 42
  of 42 derived rows are unbounded and `HELD_FOR_BOUND` never fires.
- **5f** `orchestrator.py:2125` demotes a `combined_line` row and `:2248` sets
  AUTO_PASS without consulting `label_flags`; 7 of 7 published YUTIQ quarters
  are ILUVIEN+YUTIQ.
