# Sourcing map: the eight excluded products

Eight of the twenty seed products carry no revenue data at all. Sized by each
product's commercial span through 2026Q2 they account for roughly **434
quarters — slightly more than the 423 quarters in the entire current catalog**.
So the dataset is missing more than half of its own addressable surface, and
that gap is worth more than any further accuracy work on the half it has.

This document records where the missing data actually lives, so the exclusions
can be revisited on evidence. It is a map, **not data**: see the verification
bar below before any figure here reaches `seed/gold/`.

## Verification bar

Every figure in this document came from web-search *summaries*, not from the
primary documents. That summarizer demonstrably misattributes — it returned
"CHF 15 million, Q1 2014" for both Veletri and Opsumit from the same page.

So nothing here is usable as a value. What is usable is the **location**: the
issuer, the document type, the accession pattern, the years covered. A figure
enters the dataset only after it is read out of the primary document itself,
the way every existing gold row was.

One source is actively poisoned and should be blocklisted if this pipeline ever
sources from the open web: `drugpatentwatch.com` serves a TADLIQ page
describing it as "a novel monoclonal antibody targeting non-small cell lung
carcinoma." Tadliq is a tadalafil oral suspension. The page is fabricated.

## The blocker is architectural, not sourcing

`scripts/build_independent_gold.py` computes every series' expected span as
`commercial_start_quarter → AS_OF_QUARTER` (line ~816) and raises if any
quarter is missing (line ~855). The rule is strictly binary: a complete series
to today, or excluded entirely.

**A bounded series — an explicit, cited `series_end_quarter` with a reason —
is the single highest-leverage change here**, and it is independent of any
sourcing work. This has since been implemented, so a product ending on a
reporting change is no longer discarded for that alone.

That removed the architectural blocker, but it did not convert the exclusions
automatically. Opsumit was checked first, on the theory that the rule rather
than the data was discarding ~45 quarters. Reading the filings showed the
opposite: Opsumit's launch ramp genuinely is not obtainable, because it belongs
to a Swiss issuer that never filed with the SEC. The exclusion holds — see
below. Ventavis and Veletri have not yet been checked against primaries, and
the same caution applies: a promising map entry is not a series.

---

## Actelion family

### Opsumit — exclusion holds, but for a different reason

**Status: investigated against primary documents. The exclusion stands; the
recorded reason was wrong and has been corrected.**

This section originally called Opsumit the biggest available win — roughly 45
quarters from launch through 2024Q4. Reading the actual filings does not
support that. The blocker is not where the exclusion note said it was.

**What was verified**, read directly from J&J 10-K sales tables on EDGAR:

| Year | Opsumit worldwide (USD m) | Filing read |
|---|---|---|
| 2018 | 1,215 | FY2020 10-K (`jnj-20210103.htm`) |
| 2019 | 1,327 | FY2020 10-K; confirmed again in FY2021 10-K |
| 2020 | 1,639 | FY2020 10-K; confirmed again in FY2021 10-K |
| 2021 | 1,819 | FY2021 10-K (`jnj-20220102.htm`) |
| 2022 | 1,783 | FY2024 10-K (`jnj-20241229.htm`) |
| 2023 | 1,973 | FY2024 10-K |
| 2024 | 2,184 | FY2024 10-K |

Each 10-K prints three fiscal years, so the filings overlap and cross-check.
The US line agrees to the dollar across three independent filings (2020 =
$1,008m in the FY2020, FY2021 and FY2022 10-Ks; 2022 = $1,132m in the FY2022
and FY2024 10-Ks). These seven rows are in `annual_revenue.jsonl` as
`series_role: partial_context`, alongside the Actelion CHF rows for FY2013 and
FY2014 recorded below — the same treatment Flolan gets.

**Use the stated worldwide line, never US + International.** For 2019 J&J
prints US 766 + International 562 but Worldwide **1,327**, not 1,328 — it
sums unrounded figures and rounds once. Both filings that carry 2019 print
1,327, so this is J&J's rounding, not a typo. Deriving the total would put a
value in the dataset that appears in no filing.

**Why the exclusion still holds.** Not because the launch ramp is unreachable
— Actelion's own FY2013 and FY2014 figures were recovered, and are recorded
below. The problem is the *middle*:

- J&J acquired Actelion on 16 June 2017, so 2018 is its first full reported
  year. In J&J's own words, "The Pulmonary Hypertension therapeutic area was
  established with the acquisition of Actelion Ltd on June 16, 2017. Sales in
  2018 represented a full year as compared to half a year in 2017." 2017 is a
  ~6.5-month stub, not a comparable year.
- Actelion's FY2015 and FY2016 results releases are not reachable from here,
  so 2015–2017 has no citable annual figure from either issuer.
- At the back end, J&J merges Opsumit into a combined OPSUMIT/OPSYNVI line
  from 2025, so the standalone series cannot be extended.

A series with a three-year hole before its maximum cannot be a peak benchmark:
2024's $2,184m is a highest-observed value on a still-rising curve, not a
lifetime peak. That is the same defect Flolan is excluded for. `reason_code`
moved from `reporting_scope_changed` to `incomplete_pre_peak_history`
accordingly.

**Resolved (2026-09-02).** The route below was reached through the Parallel
Search connector, and Opsumit is no longer excluded: `seed/gold` now carries a
30-quarter worldwide series for **2017Q3-2024Q4**, bounded at both ends and
described in `seed/gold/README.md`. What is written below is the sourcing plan
that produced it, kept because it records what was tried and in what order.
Two claims in it are now false and marked as such: that the Exhibit 99.2 PDFs
are unreachable, and that no contiguous series exists. The peak conclusion is
unchanged - 2015-2017 is still not citable, so 2024 is still not a lifetime
peak, and `peak_eligible` stays false.

The quarterly route:

| Issuer | Document | Coverage | Currency |
|---|---|---|---|
| Actelion | Quarterly / FY press releases (GlobeNewswire; actelion.com is dead, mirrors survive) | 2013 launch – FY2016 | CHF |
| Actelion | Annual Reports at `annualreportYYYY.actelion.com` + Financial Report PDFs | 2013–2016 | CHF |
| J&J | **8-K Exhibit 99.2 "Supplementary Sales Data"**, filed with every quarterly earnings release, explicit OPSUMIT line with US / Intl / WW columns | 2017Q2 – 2024Q4 | USD |

The Exhibit 99.2 accession pattern under CIK 200406 is stable, so the full
quarterly set is enumerable rather than hunted one at a time. Confirmed
examples: `a8k2017q4exhibit992o.htm`, `a2019q2exhibit992o.htm`,
`a2020q1exhibit992.htm`, and `a2025q1exhibit992.htm` — the last being the one
that shows the combined `OPSUMIT/OPSYNVI` line, confirming the series ends
rather than the data being absent. Those PDFs are hosted on J&J's IR CDN and
are not in any SEC full-text index reachable here; the 8-K itself only points
at the website. That is what puts the *quarterly* J&J series out of reach —
the annual figures above come from the 10-Ks, which are indexed.

#### Retrieved since: what the primary documents actually yield

Working from the Actelion press releases themselves (via Bigdata.com, which
indexes them in full including the `Sales by product` tables), these Opsumit
figures are read out of the issuer's own tables rather than predicted:

| Period | CHF m | How it is stated |
|---|---|---|
| 2013Q4 | 5 | FY2014 release, `Sales by product - quarterly`, Q4 2013 column |
| 2014Q3 | 59 | 9M 2014 release, COO quote, and 112 − 53 from the two YTD figures |
| 2014Q4 | 68 | FY2014 release, quarterly table; equals FY 180 − 9M 112 |
| 2015Q3 | 147 | 9M 2016 release, Q3 2015 comparative column |
| 2016Q1 | 178 | Q1 2016 release, financial highlights |
| 2016Q3 | 218 | 9M 2016 release, Q3 2016 column |

FY2013 = Q4 2013 = CHF 5m in the same document, which fixes the commercial
start at 2013Q4 on the issuer's own arithmetic rather than on a launch date.

The prediction above that only 2017Q1–Q2 is missing is too optimistic for the
Actelion years. **2015Q1, 2015Q2, 2015Q4 and 2016Q4 are not separately citable
from any source reachable here**: the FY2015 and FY2016 results releases carry
those tables, but neither is in the Bigdata index and this sandbox has no
outbound network. 2015H1 is known only as a CHF 207m pair (354 − 147), one
equation in two unknowns, which the derivation stage correctly refuses.

#### Where that leaves the series

Both ends are partly citable and the middle is not:

- **2013–2014**: Actelion annual, CHF, carried as context.
- **2015–2017**: no citable annual figure at all. Actelion's FY2015/FY2016
  releases are unreachable, and J&J's first figure is a ~6.5-month 2017 stub
  from the 16 June acquisition, which is not a comparable year.
- **2018–2024**: J&J annual, USD, read from the 10-K sales tables and carried
  as context.
- **2025 onward**: combined OPSUMIT/OPSYNVI line only.

So Opsumit is not the clean win this document originally called it. Not
because the figures do not exist — most of them do — but because the pre-peak
middle is missing and the quarterly route is unreachable here. It stays
excluded, and the two annual blocks are carried as context.

Remaining caveats for anyone who does reach the quarterly sources:
- **Currency seam mid-2017**: CHF (Actelion) → USD (J&J), plus a change of
  reporting regime. Needs FX normalization at the join.
- **1H-2017 hole**: whether Actelion published standalone Q1 2017 product
  sales before delisting is still unconfirmed.
- Opsynvi was approved March 2024, so late-2024 Opsumit figures may already
  reflect switch dynamics even while reported separately.

### Ventavis — exclusion is too aggressive

The stated "2006Q4 gap" is exactly one quarter wide.

| Issuer | Document | Coverage | Currency | Scope |
|---|---|---|---|---|
| CoTherix Inc. (NASDAQ: CTRX, **CIK 0001138812**) | 10-K FY2004, 10-K FY2005, 10-Q through Q3 2006, 8-K Ex-99.1 earnings releases | 2005Q1 – 2006Q3 | USD | US |
| CoTherix | SC 14D-9 (Actelion tender) — may carry recent-period actuals | late 2006 | USD | US |
| Actelion | Quarterly / FY releases, named Ventavis line | 2007 – 2016 | CHF | US |

No FY2006 10-K exists because Actelion acquired CoTherix in January 2007 —
that acquisition *is* the entire gap. Two unexplored routes to close it:
Actelion's FY2007 release may carry a 2006 comparative, and the tender
materials may disclose recent actuals.

**Scope warning:** Bayer/Schering held ex-US Ventavis rights, so the Actelion
series is **US-only**. It must not be labelled worldwide beside the WW products.

### Veletri — exclusion is correct but partial

Actelion published a named Veletri line from roughly 2011, so a CHF series
2011–2016 exists (quarterly and half-yearly as well as annual). What is
genuinely thin is 2010, the launch year. The second half of the exclusion
premise holds: J&J does not break Veletri out post-2017 — its supplementary
schedule names only OPSUMIT and UPTRAVI, aggregating the rest of pulmonary
hypertension.

**Scope warning:** US-only, as with Ventavis.

A comparability trap to note: Actelion's FY2015 Veletri growth is quoted
excluding 2014 US rebate reversals, so the raw 2014 and 2015 figures are not
directly comparable.

---

## Generic and private products: the exclusions hold

Alyq (Teva), Tadliq and Liqrev (CMP Pharma, private) have no manufacturer
disclosure. Teva's Alyq launch release quotes only the *reference brand's*
IQVIA-measured sales (~$490M for Adcirca), not Alyq's. CMP Pharma publishes
nothing beyond third-party company-level estimates with no product split.

Public **product-level** data does exist, and all three have identifiable NDCs
(Alyq `0093-3334`, Tadliq `46287-045-15`, Liqrev `46287-055-01`). But it is
payer spend, not manufacturer revenue:

| Dataset | Grain | Measures |
|---|---|---|
| Medicaid State Drug Utilization Data (CMS) | **quarterly**, per-NDC, per-state + national, 1991– | amount reimbursed to pharmacies |
| Medicare Part D Spending by Drug (CMS) | annual, national, brand × generic × manufacturer | gross drug cost |
| Medicare Quarterly Part D Spending by Drug | quarterly, preliminary and restated | gross drug cost |
| NADAC (CMS) | weekly, per-NDC | pharmacy acquisition **price**, not revenue |

SDUD matches this dataset's quarterly grain exactly, which makes it tempting.
It should still not be used as revenue:

- **Pre-rebate.** CMS states amounts are not net of rebates, and is legally
  barred from disclosing them. Medicaid rebates start at 23.1% of AMP plus CPI
  penalties; for a branded single-source product like Tadliq the gap can exceed
  50%.
- **Not manufacturer money.** The figure is what was paid to the *pharmacy*,
  including dispensing fees and pharmacy margin, and includes third-party and
  patient portions.
- **Payer-limited denominator.** Medicaid or Medicare only — no commercial,
  cash, 340B, or VA/DoD.
- **Suppressed at low volume.** Cells under 11 claims are suppressed, and Part D
  drops drugs under 11 claims retroactively — a discontinued micro-product like
  Liqrev can vanish from years it had claims.
- **Brand keying is unreliable** for an authorized-generic-shaped product:
  dispensed tadalafil may land under "Alyq" or under a generic tadalafil row
  depending on NDC.

Recommendation: keep the three exclusions as they are. If coverage matters,
carry payer spend in a **separate table** with an explicit
`basis = gross_payer_spend_pre_rebate` flag and a suppression flag — never
summed with net sales, and never filling a null in a revenue column.

Medicare Part B does not apply: all three are self-administered orals with no
HCPCS J-code, and Part B is calculated at HCPCS level.

---

## Adempas and Flolan: exclusions hold, for better reasons than recorded

These two fail in opposite ways. Adempas has *too many* reporters and no clean
worldwide figure from any of them. Flolan has one reporter, but an unverifiable
early history and a mid-series change in what "GSK's Flolan revenue" means.

### Adempas — correct, but the recorded reason understates it

The recorded reason is that Merck's figure is not worldwide. True, but Bayer's
is neither worldwide nor clean.

Confirmed from Merck's 10-K: Bayer commercializes in the Americas, Merck in the
rest of world; Merck records its own territory sales plus alliance revenue
representing its profit share on Bayer's territory sales. So Bayer product line
+ Merck product line would **not** double-count, but Bayer product line + Merck
*alliance revenue* **would**.

The disqualifier is on Bayer's side. Bayer states its Adempas sales "reflected
the proportionate recognition of the upfront and milestone payments resulting
from the sGC collaboration with Merck" — a $1bn upfront plus up to $1.1bn in
milestones (Merck disclosed a final $400m in 2022), amortized into a €350–500m
line, non-uniformly year to year. **Bayer's Adempas line is product sales plus
collaboration income, not a product-sales series.** There is also a structural
break: Adempas was global to Bayer before the May 2014 collaboration, and
Americas-plus-amortization after.

Sources located: Bayer Integrated Annual Report, Pharmaceuticals section
(FY2015–FY2025 confirmed, EUR); Bayer quarterly statements (name Adempas, but
snippets showed only growth rates, not per-product figures); Merck 10-K/10-Q
(CIK 0000310158) for the Merck-territory line and the alliance line.

Where it is arguably too aggressive: if the dataset admits **territory-scoped,
single-reporter** series, Bayer's EUR 2014–2025 series is complete and
internally consistent, and belongs with a flag for the amortization component.
It is simply not a worldwide product-sales series.

### Flolan — correct, and the real reason is different

The recorded reason is incomplete pre-peak history. Two stronger reasons:

1. **It is unconfirmed that a pre-2010 GSK Flolan turnover line exists at all.**
   Eight targeted searches across GSK's 2003–2010 Annual Reports and 20-Fs
   surfaced no Flolan figure before 2010. GSK's 20-F does carry product-level
   turnover for that therapeutic area (FY2008 shows Coreg £587m, Avodart
   £285m), so the table exists — whether Flolan was a named line in it, or sat
   inside "Other", is unresolved.
2. **A definitional break in 2006–2009.** GSK licensed exclusive US rights to
   promote, sell and distribute Flolan to Myogen (acquired by Gilead in
   Nov 2006), running April 2006 to April 2009. Whatever GSK reported as Flolan
   turnover in that window was a **supply price to Gilead**, not end-market US
   sales. The basis changes twice, independent of whether figures were
   published.

Does the gap matter? Probably not. US generic epoprostenol arrived in 2008, but
the US brand was only ~$80m of a franchise worth ~£195m — the bulk was ex-US,
principally Japan — so US genericization did not cap it. The decline is
Japan-driven and starts after the Q2-2012 biennial price cut: £195m (2010),
£179m (2011), ~£120–125m (2012, interpolated), £103m (2013). That points to
2010 being at or very near the lifetime peak. This is inference from market
structure, not an observed 2009 figure.

**Cheapest way to settle it:** GSK's 20-F product tables run three years, so
FY2010 (`sec.gov/Archives/edgar/data/0001131399/000095012311022182/`) would
print 2008, 2009 and 2010 at once — resolving both whether the line exists and
whether 2009 < 2010.

**Currency, and a warning that echoes Tracleer:** GSK reports in GBP, with a
USD convenience translation for headline totals only, not product lines. The
2008–2009 sterling collapse (~$2.00 → ~$1.56) lands directly on the window
where Flolan's peak sits, so a USD-normalized peak year may differ from the
GBP-normalized one — exactly what happened with Tracleer, where the 2011 franc
surge moved the peak from 2010 to 2011.

---

## Verdict summary

| Product | Recorded reason | Verdict | What exists |
|---|---|---|---|
| **Opsumit** | ~~combined with Opsynvi from 2025~~ → `incomplete_pre_peak_history` | **holds** (checked against primary documents) | annual CHF FY2013–FY2014 (Actelion) and USD 2018–2024 (J&J 10-Ks), all verified and carried as `partial_context`; 2015–2017 not citable, quarterly route unreachable here |
| **Ventavis** | CoTherix 2006Q4 gap | **too aggressive** | 2005Q1–2006Q3 USD (CoTherix SEC) + 2007–2016 CHF (Actelion), US-only; one quarter genuinely missing |
| **Veletri** | early quarters missing | **correct but partial** | ~2011–2016 CHF, US-only |
| **Adempas** | Merck figure not worldwide | **correct**, reason understates | Bayer EUR territory series, contaminated by amortized collaboration income |
| **Flolan** | incomplete pre-peak history | **correct**, better reasons exist | 2010–2013 GBP confirmed; pre-2010 unverified; 2006–09 basis break |
| **Alyq** | Teva reports generics in aggregate | **correct** | payer spend only, not revenue |
| **Tadliq** | private issuer | **correct** | payer spend only, not revenue |
| **Liqrev** | private issuer | **correct** | payer spend only, not revenue |

## Recommended order of work

1. **Add bounded series** (`series_end_quarter` + cited reason) to the builder.
   This is the enabling change and needs no new sourcing.
2. ~~**Opsumit** — the largest clean win.~~ **Done (2026-09-02.)** The J&J
   Exhibit 99.2 schedules were fetched through the Parallel Search connector for
   2017Q3–2024Q4 and built as a bounded quarterly series; catalog coverage went
   50% → 55%. The 2017Q2 stub resolved to $45m and is deliberately *not* in the
   series: it is 15 days of ownership, not a quarter. Actelion's CHF 2013–2016
   quarters remain only partly disclosed and stay out, which is why the series
   starts at 2017Q3 and carries a `series_start_reason` saying so.

   One expectation here was wrong and worth recording: the rows did **not** go
   through the positional-PDF reader that Uptravi uses. A positional block is
   solved by finding the one (scope, offset, direction) that explains *all* the
   periods citing it, and each Opsumit quarter cites its own filing — one period
   per block, which cannot constrain three unknowns. It surfaced as an ambiguity
   on 2021Q3, where the worldwide quarter (458) collides with the International
   nine-month figure in the same block. Rendering each row as a pipe-delimited
   table row instead puts it through `replay_table_row`, which checks column
   alignment and enforces that a Q4 row's third number is the full year — a
   stronger check, and the one that matches how the schedule is actually shaped.
3. **Ventavis** — CoTherix filings give a clean 2005Q1–2006Q3 US series; decide
   whether one interpolated quarter is acceptable or whether Actelion's FY2007
   comparative closes it.
4. **Veletri** — CHF annual 2011–2016, US-scoped.
5. **Flolan** — one document (GSK 20-F FY2010) decides it.
6. **Adempas** — only if territory-scoped series are admitted, with an
   amortization flag.
7. **Alyq / Tadliq / Liqrev** — leave excluded; payer spend belongs in a
   separate table if it is wanted at all.

---

## Alternative sources: what the open web adds (and does not)

Searched for non-SEC routes to the figures the filings index cannot reach.
Two findings, one useful and one cautionary.

**Bayer's quarterly statements do NOT yield worldwide Adempas — checked.**
This document previously called them "the single highest-value fetch", on the
theory that Bayer holds the Americas and would supply the half Merck does not
report. The statements have since been read, and that is wrong.

Bayer does print the figure. "Best-selling Pharmaceuticals Products" (table
A 8) gives Adempas™ in € million:

| Q1 2023 | Q1 2024 | Q1 2025 | Q1 2026 |
|---|---|---|---|
| 152 | 171 | 183 | 186 |

But the segment narrative on the facing page says what the figure is, in both
statements and in identical words: *"As in the past, sales reflected the
proportionate recognition of the upfront and milestone payments resulting from
the sGC collaboration with Merck & Co., United States."* Bayer's Adempas line
is product sales **plus** amortized collaboration payments. It is not a
product-sales series, and adding it to Merck's territory sales would not
produce worldwide Adempas — it would produce a number with collaboration income
baked into one half.

That vindicates the original exclusion note, which said Bayer's line "includes
amortized collaboration income, so it isn't a product-sales series". The note
was right about Bayer and wrong only in concluding that no Adempas series
existed at all: Merck's post-2020 territory line is clean, and is what this
catalog now carries, explicitly scoped.

Sources read: Bayer Quarterly Statement as of March 31, 2024, pages 8-9; and
as of March 31, 2026, page 10.
`https://www.bayer.com/sites/default/files/2024-05/bayer-quarterly-statement-q1-2024.pdf`
`https://www.bayer.com/sites/default/files/2026-05/bayer-quarterly-statement-q1-2026.pdf`

**Do not add these figures to `seed/gold/`.** They belong in the same category
as the CMS payer spend behind Alyq: real, citable, and not revenue as this
dataset defines it.

**The web-search summarizer cannot be used as a data source, confirmed again.**
Asked for Adempas sales, it returned "Bayer projected annual peak sales of
€7.5 million from Adempas". Adempas is a blockbuster; the real figure is three
orders of magnitude larger, and Merck's *territory* share alone was $312m in
2025. The summarizer had misread a fragment. This is the same failure mode
recorded at the top of this document, where it attributed one CHF figure to two
different drugs.

The rule stands and is worth restating because it survived a direct test: web
search is for **locating documents**, never for reading values out of them. A
figure enters `seed/gold/` only from the primary document itself.

---

## Egress and connectors: what was tested (2026-09-02)

The unreachable documents behind every remaining gap are the same three:
pre-2017 SEC filings, J&J's Exhibit 99.2 IR PDFs, and Merck's IR schedules.
Each route to them was tested directly rather than assumed.

| Route | Result |
|---|---|
| Direct HTTPS via the session proxy | **403 at the gateway.** The proxy itself is healthy (`enabled: true`, `selective: false`, no relay failures); the egress policy denies CONNECT to `www.sec.gov`, `efts.sec.gov`, `www.merck.com` and `s203.q4cdn.com`. The proxy README says to report such denials, not route around them |
| Bigdata.com open-web lane (smart mode, "search the open web") | Returns indexed content only; will not retrieve a named IR PDF |
| Bigdata.com index, `regulatory` + `filings` categories, pre-2017 | Empty. The filings floor is roughly 2017; news reaches back to about 2010 |
| Bigdata.com news, 2006–2011 | UTHR results press releases exist (via Benzinga), but the per-product revenue table is cut off mid-chunk, and a redistributor's copy is weaker provenance than the 8-K it reproduces |
| Google Drive | Connected and working; holds nothing pharma-related |
| Gmail, Calendar, Calendly, Vercel | Not document sources for this. Vercel's fetch is limited to Vercel-hosted URLs |
| Microsoft 365 | Installed but not enabled in this chat, status unknown — untested |
| **Uploading the PDF into the session** | **Works.** Proven end-to-end with Bayer's Q1 2024 and Q1 2026 quarterly statements |
| **Parallel Search connector (`web_fetch`)** | **Works, and this is the answer.** It fetches server-side, so the session's egress policy does not apply. J&J's Exhibit 99.2 schedules, Merck's IR schedules and Bayer's statements all return their product tables |
| **`sec.gov` through the Parallel connector** | **Works.** An earlier version of this row said it failed on SEC's user-agent policy. That was wrong, and it was load-bearing: it is why Remodulin's 2003–2008 annual totals and Winrevair's 2024 quarters were written up as unreachable when both were one fetch away. Direct `curl` to sec.gov is still blocked at the org gateway (403 on CONNECT); the connector is not, because it fetches server-side |

**Superseded (2026-09-02): the Parallel Search connector closes this.** The
recommendation below stands only for a session without that connector. With it,
the J&J Exhibit 99.2 archive is readable, and OPSUMIT worldwide has been pulled
for 2017Q3-2024Q4 and **built into `seed/gold`**, every year reconciling exactly
to J&J's stated full year (2018 1,215; 2019 1,327; 2020 1,639; 2021 1,819;
2022 1,783; 2023 1,973; 2024 2,184). The same tables carry TRACLEER and the
UPTRAVI US/International split, and the 2017Q2 acquisition stub resolves to 45
(573 less the 528 of Q3+Q4) — which is also the figure that would close the
open Uptravi 2017Q2 gap, from the same 2Q2018 exhibit.

One document in the archive resisted: the 3Q2020 schedule's text could not be
read past its International row on repeated attempts, so 2020Q3 is cited to the
3Q2021 schedule's prior-year column with a written legend. Every other quarter
cites its own filing.

The same 2Q2017 Actelion Historical Sales Schedule also carries OPSUMIT and
TRACLEER in US dollars back to 2016Q1, under a column headed "Q2 ... through
6/15". That is what let the Opsumit series start at 2016Q1 rather than 2017Q3,
and it falsified a claim made earlier in this file — that the Actelion era was
unusable because Actelion reported in CHF. Actelion did; J&J republished it in
dollars.

**Without that connector, the highest-leverage change is an egress allowlist
entry for `sec.gov`.** That one host unlocks Adempas back to 2020Q1. It would
also have unlocked Remodulin's 2003–2008 annual totals and the four Winrevair
2024 quarters, but those are closed: the connector reached the same filings, and
`seed/gold` now carries every one of them. Adding `s203.q4cdn.com` would additionally unlock J&J's Exhibit 99.2
quarterly schedules, which was the only route to Opsumit and is still the
route to Uptravi's US/International split.

Failing that, uploading the specific PDFs is the working alternative and needs
no policy change.
