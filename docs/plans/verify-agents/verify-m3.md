---
name: verify-m3
description: Verifies the judging claims - the period veto, the YTD veto reading the whole quote, the tier-blind contradiction, and what the judge is not shown.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M3: `llm/`, `prompts/`, `quality/`. Items 6b, 6c, 6d, 7d.
Depends on M2 having reported.

- **6b** `quote_states_a_different_period`: `periods_named_in` returns one
  period, so a prior-year comparative is vetoed. 142 rows, 27 alone. Run
  `periods_named_in` on the quoted sentence and show what it returns.
- **6c** `client.py:828` calls `re_ytd_language(q)` on the whole quote though
  `read` is computed at `:820`. 52 rows, 2 alone. Same for `TOTAL_REVENUE_RE`.
- **6d** `filing_contradicts_itself` is tier-blind; a tier-4 prose row vetoes a
  tier-0 tagged fact. The register quotes a tier-aware variant at +3/0 and a
  period-type-aware one at +3/+1. Those are measurements from an earlier
  session - mark them `[U]` unless you can re-run them, and say so.
  `test_one_figure_one_publication.py:172` is claimed to lock the behaviour.
- **7d** the judge is not shown unit, currency, geography, extraction method,
  filing form, sibling rows, the filing's own tagged value, or the footnote;
  `peer_names` is accepted by two functions and supplied by none, so
  `hard_veto:other_brand` cannot fire; footnotes reach the quote on 2 of 549
  rows. Verify `peer_names` has no supplier by finding every call site.
