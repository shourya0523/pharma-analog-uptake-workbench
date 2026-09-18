---
name: verify-m7
description: Verifies the characterisation claims against the live openFDA API - route, dosage form, invisible products, sibling matches and the missing launch anchor.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M7: `connectors/openfda*`, `parsing/fda_label.py`, `parsing/indications.py`,
`quality/profile.py`. Items 3a-3i. Depends on M6 reporting.

openFDA result order is not guaranteed, so anything about *which* application
is selected may present differently today. Say which findings are
order-dependent and which are field-path facts that cannot be.

- **3a** `openfda.route` disagrees with `products[].route`: verified for Tyvaso
  and Tyvaso DPI. Re-run across all 20 of `seed/example_drugs.csv` and report
  the disagreement count against `products[].route` and against the curated
  column.
- **3b** `openfda.dosage_form` is absent from the block entirely. List the
  actual keys on a drugsFDA and a label record.
- **3c** products whose `openfda` block is empty are invisible:
  `openfda.brand_name:"Flolan"` 404s while `products.brand_name:"FLOLAN"`
  returns NDA020444. Count how many of the 20 seed products and the run13 jobs
  have zero characterisation attributes.
- **3d** the substring fallback at `openfda_fields.py:58` matched THIOLA EC and
  TAPENTADOL; the generic-name exclusion at `:42` is dead because it compares
  string equality against the upload's spelling. Use run13's stored aliases.
- **3e** first-returned-wins among a brand's applications (Uptravi).
- **3f** `initial_approval_date` is never assigned in `app/`, so the
  `fda_approval_date` fallback is skipped for any product with a canonical row.
- **3g** the approval date and the indications come from different openFDA
  records, so every `ProductIndicationORM` gets a null `approval_date`.
- **3h** `therapeutic_area` is a verbatim copy of `indication`; four spellings
  of one indication universe across real labels; both grouping functions use
  exact equality.
- **3i** `moa_class`, `approval_era`, `competitive_intensity_at_launch` have no
  producer. Check `seed/gold/product_profiles.jsonl` really carries all six
  attributes for 62 products - the register proposes it as the oracle for this
  module, so its contents matter.
