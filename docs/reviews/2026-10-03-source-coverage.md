# Where verified figures live, and whether the pipeline can reach them

2026-10-03. Gold is used here as CLAUDE.md rule 4 allows: an oracle for
finding defects, never a scorer for a fix. Every gap below is stated as a
document shape or a mechanism. None is closed by naming a product, an issuer
or a URL, and none needs anything from `seed/gold` at run time. A fix to any
of them is scored on a new held-out set drawn from issuers no answer key
uses, not on gold.

How this was produced: the breakdown in part 1 is a census of
`seed/gold/quarterly_revenue.jsonl` (8,189 rows, 223 products, 58 issuers).
Part 2 comes from three reviews of `backend/app` (retrieval, parsing,
readers), each run against invented names (Calderon, NuVessa), live EDGAR,
and cached issuer documents. Each finding says whether it was **measured**
(code was run) or **read**. The four marked *re-measured* were reproduced
independently before this was written.

## 1. Where the figures are, and how they were read

| Issuer class | Rows | Share |
|---|---:|---:|
| A. US domestic SEC filer | 6,161 | 75.2% |
| B. Foreign private issuer on EDGAR (6-K, 20-F) | 1,127 | 13.8% |
| C. Non-SEC issuer (own investor-relations site only) | 901 | 11.0% |

| Document kind | Rows | Share |
|---|---:|---:|
| EDGAR earnings release / 6-K exhibit (HTML table) | 5,643 | 68.9% |
| Issuer IR PDF | 1,532 | 18.7% |
| EDGAR periodic report (10-Q/10-K) | 693 | 8.5% |
| Issuer IR HTML page | 254 | 3.1% |
| EDGAR annual-report exhibit (Ex-13) | 34 | 0.4% |
| EDGAR plain-text filing | 33 | 0.4% |

| How the figure was obtained | Rows | Share |
|---|---:|---:|
| Product row of a table | 6,837 | 83.5% |
| Unlabelled total under regional sub-lines | 578 | 7.1% |
| Stated in prose | 247 | 3.0% |
| Prior-year column of a later document | 174 | 2.1% |
| Derived: full year less nine months | 118 | 1.4% |
| Retrospective / recast table | 91 | 1.1% |
| Derived: full year less other quarters | 52 | 0.6% |
| Derived: year-to-date less earlier quarters | 39 | 0.5% |
| Other (identity normalisation, acquisition bridge) | 53 | 0.6% |

Other shapes:

- **Currency:** USD 76.6%, EUR 6.3%, GBP 5.4%, CHF 4.5%, JPY 4.4%, DKK 2.8%.
- **Printed unit:** millions 85.6%, thousands 10.0%, billions 4.4%. The billions are mostly JPY to one decimal.
- **Fiscal years:** 364 rows follow April–March fiscal years.
- **Older filings:** 398 rows predate 2009 (pre-XBRL), and 838 US earnings-release rows predate 2015.

## 2. What the pipeline does natively

| Shape | Gold share | Status | Mechanism and evidence |
|---|---:|---|---|
| HTML earnings-release product row | ~55% | **native** | `extract.py:625-870`, `fingerprint.py:463` (measured) |
| … the same, filed before about 2015 | 10.2% | **absent** | Exhibits are found only through `{accession}-index-headers.html` (`sources.py:722`), which returns 404 for older filings; the filing is skipped as `sec_no_earnings_exhibit` (`sources.py:960`). Releases before August 2004 also fail the item test (`states_item(..., "2.02")`, `sources.py:939`), and filings with an empty `primaryDocument` are skipped (`sources.py:1341`) (measured) |
| 10-Q/10-K, inline XBRL product members | 8.5% | **native** (USD only) | `sources.py:1027-1179`, `xbrl.py:609`. Non-USD facts are dropped (`tagged.py:47-51`) (read) |
| Foreign issuers' 6-K exhibits | 13.7% | **absent** | `EARNINGS_FORM = "8-K"` and the 2.02 item test (`sources.py:612, 936-940`); 6-K is a secondary form and off by default (`config.py:39`) (read; re-measured on an issuer's 6-K list) |
| Issuer IR PDF / HTML, non-SEC issuers | 21.7% | **partial**, effectively absent | The web-search fallback needs `not sec_found` (`orchestrator.py:1009`), but the SEC connector always returns a row, a FAILED one when no CIK resolves (`sources.py:1211`), so the fallback never runs. Only the per-quarter search (`orchestrator.py:714`) can reach these documents (read; re-measured) |
| … blocked hosts | — | **absent** | 403 is not retried (`sources.py:192`), and the SEC User-Agent is sent to every host (`sources.py:1460`) (measured: 2 of 6 IR hosts refuse it) |
| PDF tables | 18.7% | **partial** | Only the first 40 pages are read; no caption or unit per table; side-by-side tables merge (`documents.py:83, 509, 768`) (measured) |
| Non-USD conversion | 23.4% | **absent** for most | `FX_USD_PER_UNIT` is a literal table: CHF 2001–2016, GBP 2010–2013. Only 66 of 1,915 non-USD gold rows fall inside it; every other row gets no USD value and is dropped (`process.py:23`, `candidates.py:206`) (re-measured) |
| Currency not in the pattern list (DKK and others) | 2.8% | **wrong figure** | `detect_currency` knows five codes and defaults to undeclared USD; `currency_declared` is read nowhere (`fingerprint.py:238, 351`) (re-measured: a "DKK million" header gives USD) |
| Unit abbreviations ("€m", "USD m", "JPY bn", "Billion JPY", "$000s") | most non-US rows | **refused** | `fingerprint.py:187, 218`; 490 of 1,600 real revenue tables refused as `unit_not_declared` (measured) |
| Space-grouped thousands ("1 083") | European issuers | **absent, silent** | Three separate number readers disagree (`extract.py:50`, `tables.py:41`, `documents.py:87`) (measured) |
| Quarter column beside an H1 / 9M / YTD column | unknown, common abroad | **wrong figure** | A column heading the reader cannot parse inherits the first span in the header (`fingerprint.py:490/502`) (re-measured: `Q2 2024 \| H1 2024` gives two 3-month blocks, so the half-year total is published as Q2) |
| Day-first and fiscal headings ("three months ended 31 December", "April–June", "FY2024 Q1", "H1 2024") | GBP/EUR/JPY | **refused**, or wrong | `fingerprint.py:76, 93`; `periods.py:57, 162`. "FY2024 Q1" becomes calendar 2024Q1 (measured) |
| April–March fiscal years | 4.4% | **partial**, then wrong in derivation | Six- and nine-month spans lose their end month (`fingerprint.py:266`), and `derive.py:39` assumes calendar spans (measured) |
| Regional sub-lines with an unlabelled total | 7.1% | **partial** | Works only when every sub-line is in `SCOPE_PATTERNS`, and only within 2 rows (`extract.py:526, 741`; `labels.py:333`) (measured) |
| Derived quarters | 3.0% | **native, but unsound** | Inputs are keyed by period only (`derive.py:181`), so a U.S. Q1 can be subtracted from a worldwide year, a combined line's flags are lost, and the last reading wins (measured) |
| Spans compared by year label, not date interval | — | **wrong figure** | "Year ended March 31, 2024" less "nine months ended December 31, 2024" was published as 2024Q4 (measured) |
| Prose figures | 3.0% | **partial**, with false accepts | `prose.py:397`: whether a sentence's figure is an aggregate is decided by word order, so "Calderon contributed to total revenues of $242.0 million" is accepted as Calderon's (measured) |
| Prior-year and recast columns | 3.2% | **partial** | The original filing always wins and a recast only raises a flag (`orchestrator.py:518-562`) (read) |
| EDGAR plain-text filings / Ex-13 | 0.8% | **absent** | No grid is built from `<PRE>` text (`documents.py:731`); only exhibit 99 is taken (`sources.py:619, 958`) (measured / read) |
| Ownership change, partner-booked products | 16 products / up to 6.2% | **absent** | The issuer is chosen once, from openFDA's manufacturer or the model's CIK (`orchestrator.py:1222`). The split-quarter assembly and adjudication are written but not wired (`derive.py:343`, `adjudicate.py`; `test_capabilities_are_wired.py:88-95`) (read) |

## 3. Gaps ranked by cost

Wrong figures come first, because nothing downstream catches them. Then the
largest silent losses.

1. **Figures published wrong, unflagged.**
   - A half-year or year-to-date column is read as the quarter.
   - A DKK (or any unlisted currency) table is labelled USD.
   - Derived quarters subtract inputs of different scope or span.
   - "FY2024 Q1" is mapped to calendar 2024Q1.
2. **Non-USD figures dropped: 23.4% of gold.** The hand-written rate table is the
   mechanism, and it is rule 1's failure. The producer exists, a dated FX source
   (e.g. the Federal Reserve H.10 series), and should be read at run time or
   through a cache outside the answer key.
3. **Foreign issuers never fetched: 13.7%.** 6-K exhibits are excluded by
   form and item.
4. **Non-SEC issuers unreachable: 11%.** The fallback condition is decided on
   presence, not success. The per-quarter `coverage` verdict is computed but
   read by nothing.
5. **Older US releases never fetched: 10.2%.** The exhibit list depends on a
   page EDGAR does not keep for older filings.
6. **Most non-US tables refused before they are read.** The causes are unit
   abbreviations, space-grouped thousands, and day-first or fiscal headings.
7. **Regional totals, PDFs past page 40, prose subject attachment, recasts,
   ownership changes.**

## 4. General mechanisms (none names a product or issuer)

- **Spans.** Carry each column's start and end date through to derivation.
  Subtract only when the inner span is a prefix of the outer one and both
  have the same series identity (scope, combined line, currency). A heading
  the reader cannot parse gives the column no period, never an inherited one.
- **One period grammar** (`periods.py`), used by `fingerprint.py`. It should cover
  "quarter ended", "N weeks ended", day-first dates, month ranges, and
  H1/1H/6M/9M/FY/YTD. Take the fiscal year-end from what the filer declares
  (`fiscalYearEnd` in EDGAR submissions, the dei fiscal year-end, or the document's
  own phrase).
- **One number reader** for all three call sites. It should accept space and
  thin-space thousand groups when every group is three digits, U+2212 minus
  and trailing footnote marks.
- **Currency and unit.** Any ISO-4217 code beside a magnitude word, in
  either order ("JPY billion", "Billion JPY", "€m", "USD m"). An undeclared
  currency is unknown, not USD. Rates come from a dated source, not a literal.
- **Retrieval.**
  - Read declared exhibit types from the `.txt` full submission or `-index.htm` when the header page is missing.
  - Map the earnings item by filing date, or take every EX-99 in the window and let `coverage` prune.
  - Run the exhibit pass over 6-Ks without an item test.
  - Decide the fallback per quarter from the `coverage` verdict.
  - Send a neutral User-Agent to non-SEC hosts and look for an alternate copy on a 403.
- **Issuer per quarter.** When the bound issuer's documents for a quarter
  return `absent` or `names_only`, search for who reported that quarter.
  Accept a new issuer only after its own document carries the product. Wire
  the existing split-quarter assembly once readers return dated part-period
  spans.
- **Regional totals.** Identify sub-lines by arithmetic (they sum to the row
  beneath the heading), not by a list of region words.
- **Prose.** Attach an amount to the noun phrase it governs, not to the
  nearest earlier word.

## 5. What is not known yet

- How often each shape costs a figure in live runs. A run of the pipeline on
  a sample stratified by these categories would measure it. It must be
  scored on a held-out set of unseen issuers, and only after a fix (rule 4).
- Whether product tables in issuer PDFs actually sit beyond page 40.
- How duplicate same-quarter candidates are resolved downstream.
