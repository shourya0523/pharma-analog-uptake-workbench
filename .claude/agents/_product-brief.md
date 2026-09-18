# What this product is

Not an agent. The frame every reviewer works from. It contains no findings and
no measurements on purpose: read the code and the data, and reach your own.

## The business question

Someone is valuing or forecasting a drug that has no history of its own - one
about to launch, or one being licensed. The standard method is **analogs**:
find products that launched into comparable circumstances, look at how fast
their revenue ramped and where it topped out, and forecast from those curves.

The hard part is not the modelling. It is that the analog data does not exist
in usable form. Issuers disclose product revenue inconsistently - broken out by
product, buried in a franchise line, printed only in a press release, stopped
entirely at an acquisition. An analyst rebuilds each series by hand from
filings, and the work is not reusable because nobody else can check it.

This workbench claims it can build those series automatically and make every
figure checkable. From the README: *"Source-first extraction and validation for
pharmaceutical analog uptake data. Citations are mandatory on every
source-derived field."*

## Three layers

**1. Build the series.** Given a drug name, find the issuer's filings, read the
quarterly revenue, publish each figure with the quote and URL it came from, and
say why where there is none.
`connectors/` -> `parsing/` -> `extraction/` -> `quality/` -> `pipeline/`.

**2. Characterise the product.** Mechanism of action, route, therapeutic area,
FDA approval date, approved indications, competitive intensity at launch. This
is what makes two products comparable at all.
`connectors/openfda*`, `parsing/indications.py`, `parsing/fda_label.py`,
`quality/profile.py`, `analytics/competitive_intensity*.py`.

**3. Do the analog work.** Score similarity between products, rank analogs,
find or estimate a peak, compute the uptake curve and the time to reach most of
peak. `analytics/analog_matching.py`, `peak_sales.py`, `uptake.py`.

## What the user gets

- **Library / Product detail** - the products and their quarterly series
- **Review queue** - figures the pipeline will not stand behind alone
- **Dashboard** - headed "Analog Product Explorer"
- **Export** - the deliverable: a quarterly-revenue sheet *and* a product sheet
  carrying therapeutic_area, moa, competitive_intensity, peak_value, peak_type,
  peak_method, uptake_methodology

Layer 1 is the input to the product. Layers 2 and 3 are the product.

## Who uses it

`seed/example_drugs.csv` is 20 products: Opsumit, Adcirca, Tyvaso, Uptravi and
the rest of the pulmonary arterial hypertension catalog, plus rare disease, HIV
and oncology comparators. `seed/gold/README.md` says competitive intensity is
computed only for the PAH catalog, *"because that catalog is the indication
universe by construction"* - elsewhere it would be *"a number with the shape of
a measurement and none of the meaning."*

That is a specialty and rare-disease analyst. Small indications, few
competitors, an analog set of a handful of products where each one matters.

## How to review

- **Read the logic, not the claims.** Work out what the code does from the code
  and from real data. Do not start from anyone's list of defects.
- **Judge against the business question.** A module can do exactly what its
  docstring says and still not serve the analyst. Say so when it does not.
- **Every claim needs the command that showed it**, in your report, with a
  `file.py:line`. CLAUDE.md rule 2 governs. Say which findings you verified and
  which you infer.
- **Rank by what it costs the analyst**, not by how interesting it is. A number
  with no meaning costs more than a missing one; a missing one costs more than
  a tidy-up.
- **Do not edit any file.**

## Do not read

`.claude/agents/_shared-brief.md`, `docs/plans/2026-09-17-005-*`,
`docs/plans/2026-09-17-006-*`. Those carry an earlier review's conclusions and
a narrower framing of the product. Reading them defeats the point of this pass.
Prior `docs/plans/00{1,2,3,4}` and `docs/plan-after-the-full-sweep.md` are
history and may be read as context for *intent*, not as findings to confirm.

## Evidence

- Real cached SEC filings: `.../scratchpad/run7/storage/cache/sec/` (~550 docs)
- Run databases with real output: `.../scratchpad/run13/workbench.db`
  (and run8, run10, run12) - full path under the scratchpad directory
- `seed/gold/` - an independently researched answer key, NOT pipeline output
- `seed/example_drugs.csv` - what a user uploads
- Tests: `cd backend && ./.venv/bin/pytest -q`
- Python: `backend/.venv/bin/python`
