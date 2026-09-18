---
name: verify-m0
description: Verifies the baseline and eval claims - the gold case files, the eval's blind spots, the option defaults. Nothing downstream can be measured until these are settled.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M0: `scripts/`, `seed/cases/`, `seed/gold/`. Items 0a-0e of the register.

- **0a** `gold_all.json` is 1,415 expectations over 39 products against gold's
  2,203 over 55, missing every Eli Lilly series; no test catches divergence.
  Re-derive both counts and the missing set. Also check whether the values that
  are present still match gold - the register claims 0 mismatches over 1,415.
- **0b** `gold_all.json` and `gold_sample.json` carry zero null expectations,
  so "answered anyway" is unreachable. Count nulls per case file, all five.
- **0c** `eval.py`'s conflict `spread` has no scope filter, so the AYVAKIT
  2024Q4 three-way scope split scores as a conflict though the expected value
  is published. Reproduce by running the scoring path, not by reading it. Also
  the three sub-claims: 35 of 73 published pairs never examined; `expect` rows
  carry only three keys; `score()` runs regardless of job status.
- **0d** all five case files disable `openfda` and `product_metadata` while
  both default True; `drug_profile_fields` holds only `llm_aliases` in all 19
  run databases; `test_shapes_holdout_is_held_out.py:130` asserts the off
  configuration is "what a person would type".
- **0e** `sec_include_8k` and `enable_profile_judge` are overridden against
  their defaults. Establish where each override lives and what it currently is.

The register's headline depends on 0a. If 0a's numbers have moved, say so
before anything else.
