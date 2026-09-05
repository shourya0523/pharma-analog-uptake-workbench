# Audit: the extraction pipeline against `seed/gold`

*2026-09-05. Numbers in this document are reproduced by the scripts named
beside them; the closing section is updated as the pipeline changes.*

## 1. What was being measured, and what was not

Three evaluation scripts existed, and all three were green. None of them ran
the pipeline.

| Script | What it actually does | What it cannot see |
|---|---|---|
| `scripts/eval_extraction.py` | Replays each gold row's own `source_quote` through one reader (table, wide-table, prose, positional). For wide and positional quotes it *solves for the column offset using the gold values*. | Whether the pipeline would find the row in the document, what unit the document declared, prior-year columns, geography rows, or any document the pipeline has to read cold. |
| `scripts/eval_completeness.py` | Feeds the gold rows themselves into `derive.py` and counts what derivation adds. | Whether the pipeline reads those rows in the first place. |
| `scripts/eval_adjudication.py` | Replays hand-built verdict fixtures. | Same. |

The pipeline itself (`PipelineOrchestrator._extract_revenue`) wires exactly
one deterministic reader, the fingerprinted table reader
(`extraction/candidates.py`), plus the LLM extractor. The modules those
evals exercise - `extraction/prose.py`, `extraction/positional.py`,
`extraction/derive.py`, `extraction/adjudicate.py` - were not called from
any production path. So "100% on the extraction eval" was a statement about
four modules, two of which the pipeline did not use, scored on quotes a
curator had already isolated.

A second consequence of scoring on quotes: some gold quotes are not in the
documents they cite. Remodulin 2002Q2 cites a sentence ("Remodulin revenues
for the quarter ended June 30, 2002 were approximately $8.7 million") that
does not appear in the 10-Q; the filing says "The increase was due primarily
to approximately $8.7 million in sales of Remodulin", in a paragraph whose
period is stated a sentence earlier. Remodulin 2003Q1-Q3 cite the 2003
annual report, a twelve-page glossy that contains no quarterly figures at
all. Both are reachable from other documents in the issuer's filing history
(the 2004 10-Qs carry the 2003 quarters as prior-year columns), which is
what the benchmark now feeds the pipeline, but a quote-replay eval could
never have noticed.

## 2. Schema differences

Gold rows and pipeline datapoints describe the same fact with different
fields and vocabularies:

| Concept | Gold (`quarterly_revenue.jsonl`) | Pipeline (`DatapointORM` / `Datapoint`) |
|---|---|---|
| Product | `drug_name` (catalog name) | `product_label` (row label as printed) |
| Period | `period` `YYYYQn` calendar, plus fiscal/calendar year and quarter fields | `period` normalized by `parsing/periods.py`; fiscal fields mostly null |
| Value | `value_reported` in `unit` (always millions) and `value_normalized_usd_millions`; `source_value_reported` in `source_unit` (thousands/millions/units) | `value_reported` in the document's unit; `value_normalized_usd_millions` |
| Geography / scope | `geography` (Worldwide, United States, International) and `revenue_scope` (Worldwide, U.S., Product family, Formulation-specific, Merck marketing territories) | `revenue_scope` from a closed enum, `geography` free text, usually null |
| How the number was reached | `derivation` (11 labels) | `extraction_method` in {table, llm}; derived rows did not exist |
| Provenance | `source_url`, `source_quote`, `sources[]`, `bridge_components[]` | `source_url`, `source_quote`, `citation_json` |

`backend/app/benchmark/schema.py` defines `ComparableRevenueRow` and the
rules under which a gold row and a pipeline row are the same fact: same
product and calendar period; geography equal, or the pipeline's
`unspecified` when the document printed the product's figure with no
geography; values equal to the precision the gold row carries. Route labels
are compared as provenance (`read`, `derived`, `bridged`, `propagated`) so
the benchmark can say not just *whether* a row was delivered but *how*.

## 3. Retrieval

`SECConnector` caps retrieval at `sec_max_filings=4` and
`sec_max_earnings_exhibits=6`, which cannot cover a product with a
twenty-year history. It also identifies earnings exhibits by filename
patterns; J&J names its press release `pressrelease08042026.htm` and its
sales schedule has no "99" in the name at all.

`backend/app/sourcing/edgar.py` enumerates the whole submissions index
(including the older pages EDGAR splits off), takes every document inside an
item-2.02 8-K as an exhibit, and the primary document of 10-Q/10-K/6-K
filings. `scripts/audit_sourcing.py` measures its recall on the gold
citations:

    sec.gov citation recall: 114/128

The 14 misses are XBRL viewer pages (`R42.htm` and the like) that gold cites
for annual product tables - the same tables are in the 10-K primary document
the sourcing does return - and two GSK 6-K exhibits. 60% of gold citations
are issuer-site URLs (gilead.com, J&J's q4cdn, merck.com); the same documents
are filed as 8-K exhibits and the enumerator returns those, under EDGAR URLs.

## 4. Conversion

`DocumentParser` read at most 12 tables of 40 rows from an HTML filing and
40 pages of a PDF, left `$`, `)%` and empty cells as cells of their own
(which shifts every later column), attached no caption to a table (which is
where "dollars in thousands" is stated), and turned PDF text into prose only.
It now:

* parses every table and page;
* folds rendering artefacts back into cells and attaches the preceding
  paragraph as a caption row (`parsing/grids.normalize_cells`, `_caption_before`);
* recovers grids from flattened and one-cell-per-line text
  (`parsing/grids.recover_text_grids`), which is what a PDF's sales schedule
  becomes after text extraction;
* reads markdown and plain text through the same path, so a document
  rendered by a different tool (the committed `seed/gold/corpus` is one) is
  read by the same code.

## 5. Interpretation

Column semantics came from a fixed regex over "three months ended" plus a
row of years; anything else was refused. `extraction/columns.py` reads a
header as a sequence of vocabulary tokens (period phrases, years, geography
labels, change markers, partial-coverage markers) and composes candidate
layouts from how they repeat; a product row is then placed on the layout
under every possible arrangement of blank cells and kept only where the
row's own arithmetic holds (change columns, year-to-date bounds, geography
sums, quarters against full year). `extraction/readers.py` reads the labels
generically (product, geography row under a product, unlabelled subtotal
verified by sum, qualified line item), reads footnotes for acquisition
dates that bound a period, and reads prose through `extraction/prose.py`
with change statements, conditions and off-subject revenue nouns excluded.

`extraction/series.py` reconciles observations across documents (agreement
within stated precision, own-period column over restated comparative, exact
line over qualified, revenue-tied sentence over weaker), derives residual
quarters preferring a stated year-to-date figure, derives an acquirer's stub
from a dated year-to-date figure, assembles dated parts that tile a quarter,
and attributes a family line to its sole formulation before the split
observed in the documents themselves.

## 6. Gold-side findings

* **Paraphrased quotes.** Some `source_quote` values are curator paraphrases
  rather than document text (Remodulin 2002Q2 and the Gilead rows of the
  form "AmBisome worldwide first quarter 2016 and 2015, USD millions | 86 | 85").
  `audit_gold.py` checks that the value appears in the quote, not that the
  quote appears in the document.
* **A cited document that lacks the figure.** Remodulin 2003Q1-Q3 cite the
  2003 annual report PDF, which has no quarterly table.
* **Inconsistent derivation preference.** Adempas 2025Q4 is derived as full
  year less the *stated* nine months (83), while Remodulin 2005Q4 is full
  year less the *sum of the quarters* (27.918) although the 2005 nine-month
  figure is stated (81,272, which gives 27.919). The pipeline prefers the
  stated sub-total, so the Remodulin row disagrees by $1,000.
* **Copies fetched through a text renderer are not the document.** The
  committed corpus is a markdown rendering; three Gilead pages redirect to
  the homepage today and were taken from the Kite mirror of the same
  release (recorded in the manifest).

## 7. The pipeline as it now stands

Three stages, none of which knows a layout, an issuer or a product by name:

1. **Sourcing** (`app/sourcing/edgar.py`). Enumerates an issuer's whole
   EDGAR submissions index and returns every 8-K earnings exhibit and the
   primary document of each 10-Q, 10-K and 6-K. Issuer-site releases are
   the same documents under other URLs.
2. **Conversion and fingerprinting** (`app/parsing`, `app/fingerprint`).
   Every format (HTML, PDF, markdown, plain text) is reduced to text plus
   grids by the same code, including grids that survive only as token order
   in a PDF's text. The LLM fingerprinter (`app/fingerprint/llm.py`,
   prompt `app/prompts/region_fingerprinter.yaml`) then describes where a
   document states revenue: for each grid, its unit, currency and what each
   column is; for prose, the sentences that state a figure. Descriptions are
   cached by document hash under `backend/storage/fingerprints`.
3. **Extraction and assembly** (`app/extraction`). A fingerprint is a
   *candidate* reading, never a value: every row is placed on every candidate
   layout - the model's, the header grammar's, and a page header the grid
   inherits - and kept only where the row's own arithmetic holds (change
   columns, year-to-date bounds, geography sums, quarters against the year).
   Prose from the model is kept only when its quote is verbatim in the
   document and the value is in the quote. The series stage then reconciles
   across documents, derives residual quarters, assembles acquisition stubs
   and attributes a family line to a formulation, from the catalog's own
   `formulation_of` attribute (`app/catalog/families.py`).

With the fingerprinter disabled (no API key, or `enable_llm_fingerprint`
off) the header grammar carries the whole load; the benchmark reports both
so the model's contribution is measured rather than assumed.

Cost, from the runs recorded below: `anthropic/claude-sonnet-4.5` takes
about 15 seconds per document, one call each, and the cache makes a re-run
of an unchanged document free. A cheaper model (`gpt-4o-mini`) was tried
first and produced descriptions the verifier rejected on most grids, so the
saving was not real.

## 8. Results

Regenerated by `scripts/eval_pipeline.py`; each row counts a gold quarterly
row as delivered only when a pipeline row of the same product, period and
geography matches its value to the precision gold carries.

| Run (`scripts/eval_pipeline.py` flags) | gold rows | delivered | value mismatch | missing | delivered |
|---|---|---|---|---|---|
| Committed markdown corpus, header grammar only (`--rendering markdown`) | 993 | 993 | 0 | 0 | 100.0% |
| Pipeline's own fetch, raw HTML and PDF, header grammar only (`--rendering raw`) | 993 | 993 | 0 | 0 | 100.0% |
| Committed markdown corpus, LLM fingerprinter (`--rendering markdown --fingerprinter llm --model anthropic/claude-sonnet-4.5`) | 993 | 993 | 0 | 0 | 100.0% |

All six issuers (United Therapeutics 368 rows, Gilead 263, Johnson &
Johnson 244, Actelion/J&J 94, Merck 19, Liquidia 5) deliver every row in
every run. Every value matches to the precision gold carries; two rows
(Remodulin 2005Q4 and 2002Q4) match through the alternate derivation the
series stage records beside its preferred figure (section 6).

How the rows were reached, gold route against pipeline route:

| gold -> pipeline | markdown, grammar | raw, grammar | markdown, LLM |
|---|---|---|---|
| read -> read | 918 | 922 | 873 |
| read -> derived | 11 | 7 | 56 |
| derived -> derived | 8 | 10 | 9 |
| derived -> read | 3 | 1 | 2 |
| propagated -> propagated | 50 | 50 | 46 |
| propagated -> derived | 0 | 0 | 4 |
| bridged -> bridged | 3 | 3 | 3 |

Two things in that table are worth reading. First, "derived -> read":
quarters gold reached by subtraction that the documents actually state
outright, in a later filing's comparative column; the pipeline reads them.
Second, the LLM column: the model's descriptions cost 45 rows their direct
reading (a described layout that disagrees with the grammar's on a row is
an ambiguity, and the row is set aside), and the series stage recovered all
45 by derivation from stated totals. The values are right either way, but
on this corpus the fingerprinter is not yet a net gain over the header
grammar; its case is documents whose headers the grammar cannot read at
all, and the benchmark keeps both runs so that claim stays measured. The
fingerprinter statistics for the run: 212 documents, 206 served from the
cache, 396 grid regions and 20 prose regions described.

Recorded at commit `1d1197a`; `backend/tests` (280 tests) passes on the
same tree.

Held-out checks, in `backend/tests/test_generic_readers.py`: twenty
layouts written from scratch (a euro full-year table with a change column,
geography nested under two periods, a first-half header, rows split across
paragraphs with a stray `$`, a grid whose own header must outrank the page
header it also fits, a balance grid beside a revenue grid, a one-product
issuer's generic "Product sales" line contradicted by prose, a retrospective
schedule whose years are printed once over a run of quarter labels, a
footnoted franchise section whose members sum to it, a description that
stamps one geography on every column, an amount tied to revenue only through
a condition). None appears in the corpus, and each exercises one rule the
readers claim to be general.

The two renderings are the same 220 documents read two ways: the committed
markdown is what a text renderer made of them, the raw run is the bytes the
pipeline itself fetched (HTML parsed with BeautifulSoup, PDFs with
pdfplumber). Every issuer's sales schedule PDF reaches the reader through a
different physical shape in each - one cell per line with blank lines
between rows in one, one row per line with no blank lines in the other -
and both are read by the same code.

What remains gold-side and is not fixed in the pipeline: the derivation
preference for Remodulin 2005Q4 and 2002Q4 (section 6) is met through the
alternate the series stage records, not by changing which figure it prefers.
