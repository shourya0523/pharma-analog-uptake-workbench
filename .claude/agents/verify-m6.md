---
name: verify-m6
description: Verifies the retrieval and identity claims - form and item filters, CIK resolution, and the section 12 cascade design's premises. Gates the characterisation module.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M6: `connectors/sources.py`, `connectors/llm_search.py`, `identity/`.
Items 8, 9c and 12 of the register.

This module carries the register's worst history. Section 2f was wrong three
times, each time about what a filing contains. **A name in a document is not a
figure in a document** - when you check whether a filing carries product
revenue, print the number beside the product label or leave the item `[I]`.

- **9c** `sources.py:541` is `form != "8-K"`; `:289,832` are the PRIMARY and
  SECONDARY sets. The register claims 108 item-2.02 filings on `8-K/A` and
  1,308 filings dropped by the sets, measured over cached EDGAR indices. Re-run
  both counts and state the corpus and predicate you used.
  Then the open question the register explicitly leaves: **open some of them.**
  Does any filing these filters drop carry a product-level revenue figure for a
  period, for any issuer in `seed/example_drugs.csv` or in a run database? One
  worked example either way settles it. Print the figure or report none found.
- **2f** the corrected position: the J&J and Gilead IR documents are on EDGAR
  as EX-99 under item 2.02 (verify for a quarter other than the two already
  checked), and the Actelion 8-K/A `0000200406-17-000046` holds company-level
  CHF statements with no per-product figures. Confirm or correct.
- **8** identity: `_identity` skips `resolve_cik` without a ticker or
  manufacturer; `resolve_cik_from_search` discards `company_name` and
  `confidence`; `SECConnector.retrieve` takes no product; the aspirin repro
  stored 28 ACADIA filings; `resolve_cik` fails on `Vertex Pharmaceuticals` and
  `Eli Lilly and Company` against the real registrant index. Re-run the index
  probes. There is claimed to be no test of `resolve_cik` at all - check.
- **12** is a design, not a finding, and is marked UNMEASURED. Verify only its
  *premises*: that the three literals are as quoted, that `read_label`,
  `periods.py` and `fingerprint.py` can supply the predicate's branches, and
  that tier 6 (`_search_revenue_fallback`) produced 0 datapoints. Do not
  evaluate the tier ordering; it has no measurement yet by construction.
