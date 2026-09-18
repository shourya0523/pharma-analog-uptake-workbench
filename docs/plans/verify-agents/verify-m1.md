---
name: verify-m1
description: Verifies the surface claims - the chart plotting the wrong scope, the coverage percentage, the export's period types and citations, the tiles asserting machinery that never runs.
model: opus
tools: Read, Grep, Glob, Bash
---

Follow `.claude/agents/_verification-protocol.md`. Change no file.

Module M1: `api/`, `export/`, `dashboard/`, `frontend/`. Items 1a,1b,1d,1e,1f.

These are the register's highest-ranked items, because they are numbers a user
reads. Reproduce each by running the real function on real run data, not by
reading the code.

- **1a** `dashboardModel.ts:83` is last-row-wins; AYVAKIT 2024Q4 plots 20.0
  where worldwide is 144.1. Run `buildChartData` on the real run13 payload.
  Also: how many published (product, period) groups carry more than one value.
- **1b** `quality/completeness.py:89` counts rows not distinct periods and
  excludes only `rejected`; `unresolved_quarters` is written only when the
  issuer filed nothing (`orchestrator.py:1146`). ILUVIEN reads 100% with 0
  published quarters, ORLADEYO 47.6% with 8. Also `_published_quarters`
  (`api/products.py:135`) counting any `period_type`.
- **1d** `export/builder.py:267-295` emits every datapoint with no status or
  period-type filter into 8 columns with no `period_type`. Build the real file
  and count its rows by `period_type` and `validation_status`. Find the keys
  carrying both a quarterly and a cumulative figure under one period string.
- **1e** the product sheet's single `source_url` comes from the first revenue
  datapoint (`builder.py:108`, `dashboard/series.py:193`); the stored openFDA
  `source_quote` is the literal `f"openfda.{field}"`.
- **1f** the `$0M` aggregate peak tile, the Methodology tab's two sentences
  about code with no caller, the Launch-relative empty chart with a 22-product
  legend, and the claim that 32% of published datapoints come from jobs that
  never finished.
