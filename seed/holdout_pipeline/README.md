# Held-out set for the retrieval pipeline

`cases.jsonl` scores a change to the product-quarter retrieval pipeline
(`backend/app/retrieval/`). It is drawn from issuers that no answer key in this
tree uses, so under `CLAUDE.md` rule 4 a number measured on it means something.
`backend/tests/test_holdout_pipeline_is_held_out.py` checks the property that
makes that true, not the score.

It is an answer key. Nothing the pipeline reads may come from it (rule 3), and
once a change has been tuned against it, it is spent: retire it and draw another.

Counts are not written here; print them:

    python3 -I -c "import json,collections; c=[json.loads(l) for l in open('seed/holdout_pipeline/cases.jsonl')]; print(collections.Counter(x['expect'] for x in c)); print(collections.Counter(x['kind'] or x['reason'] for x in c))"

## What a case asks

One product, one calendar quarter, one issuer: "what did this issuer report as
this product's revenue for this quarter?" The answer is either a figure
(`expect: "value"`) or `expect: "not_reported"`.

A `not_reported` case is not a missing label. It is the correct answer, and a
pipeline that returns a number for it is wrong. Each one has a number nearby
that it would be wrong to return:

- `pre_launch` - the product is approved (or about to be), but the quarter has
  no revenue for it. Some of these quarters have other product revenue from the
  same filer (a sibling product, or a total line).
- `royalty_only` - the filer earns a royalty on another company's sales of the
  product. The royalty, and the filer's own sales of the same molecule under
  another brand or territory, are not the answer.
- `aggregate_only` - the filer sells the product but prints it only inside a
  line that holds other products. The aggregate is not the answer.

`value` cases are tagged with `kind`, the shape of the disclosure:
an 8-K release table or sentence, an 8-K release attached under a file name
that does not say exhibit 99, a 10-Q disaggregation table where the release
only rounds, a single-product filer whose revenue line never names the product,
a Q4 printed directly in the year-end release, a non-USD foreign filer's 6-K,
and a foreign filer whose fiscal year does not end in December.

## Fields

    product, period ("Q2 2023", calendar quarter), issuer, cik
    expect        "value" | "not_reported"
    kind          shape of the disclosure (value cases), else null
    reason        pre_launch | royalty_only | aggregate_only (not_reported), else null
    value         the most precise figure printed for the product, in `unit`
    unit          "millions" (a figure printed in thousands is divided by 1000)
    currency      as reported (USD, EUR, SEK); never converted
    scope         what the figure covers (US only, worldwide, US plus partners)
    form, filed, accession   the filing the label was read from (from EDGAR's
                  submissions index, not typed)
    source_url    the primary document
    source_quote  the text that decides the label
    note          the trap, where there is one

`source_quote` is copied from the document as rendered to text: whitespace is
collapsed, and table cells are joined with ` | `, so a table row reads
`Product revenue, net | $ | 22,504 | $ | 4,284`. Where a label needs two
passages (the product named in prose, the figure in a table), they are joined
with ` ... `. Each passage was checked to occur in the fetched document after
that rendering, by substring match.

Where prose prints the product rounded (`$15.0 million`) and the table prints
the filer's product-revenue line exactly, `value` is the exact figure and the
note says what ties the line to the product. A scorer should accept a figure
within the precision the prose prints.

## How it was built

1. **Exclusions were derived, not listed.** Every issuer name in a field naming
   a company and every CIK in an EDGAR URL, across every file under
   `seed/gold/` (including `source_manifests/`, `companion_series.jsonl` and
   `excluded_products.jsonl`), `seed/cases/`, the analog profiles and the
   consensus estimates. The guard test re-derives the same sets at run time.
2. **The previous codebase's held-out sets are not in this tree.** They are
   on `main` (`seed/holdout/`, `seed/holdout2/`, `seed/holdout_labels/`,
   `seed/holdout_members/`, `seed/holdout_foreign_xbrl.json`,
   `seed/cases/holdout_2026_09*.json`, `seed/cases/shapes_holdout.json`). Their
   issuers and CIKs were read with `git show main:<path>` and excluded by hand
   at build time; none of this set's issuers is among them. The guard test
   cannot repeat that check, because `main` need not be present where it runs.
   It does pick up any `seed/holdout*` set that is later added to this tree.
3. **Candidates** were small and mid-size commercial biotechs, screened by name
   against those exclusions, then by CIK from EDGAR's `company_tickers.json`
   and company search. Earnings 8-Ks (item 2.02), 10-Qs and 6-Ks were listed
   from `data.sec.gov/submissions`, with the filing's own `index.json` for the
   exhibits.
4. **Labels were read from the primary document**, fetched with
   `SEC_USER_AGENT`, by a person (a model acting as one) reading the passage -
   not by the pipeline, and not by Gemini. No product in the set is a
   `drug_name` gold holds.

Products were chosen so that each issuer contributes a shape, not a volume:
several issuers appear twice, once each way (a product's launch quarter as
`not_reported` and a later quarter as `value`; two sibling products inside one
total), so that a pipeline cannot pass by learning the issuer.

## Known limits

- The set is US-listed filers only. The non-USD cases are two foreign
  private issuers filing 6-Ks (EUR, SEK); there is no JPY or CHF case.
- No case requires subtracting nine months from a full year: both Q4 cases
  print the quarter. A derived-Q4 case would need an issuer whose year-end
  release prints only the year.
- Values for Korlym are attributed from the 10-Q's statement that Korlym is the
  product the filer sells, not from a line naming it.
- The Ryoncil case is a results presentation (6-K exhibit), and its quarter is
  the filer's fiscal Q4 (April-June); the period field gives the calendar
  quarter.
