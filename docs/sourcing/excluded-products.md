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

That rule, not the absence of data, is what discards Opsumit. The exclusion
note concedes the series "can be reconstructed only through 2024Q4" — roughly
45 citable quarters thrown away because the 46th changed reporting basis.
Ventavis and Veletri have the same shape: clean until an acquirer stopped
breaking them out.

**A bounded series — an explicit, cited `series_end_quarter` with a reason —
is the single highest-leverage change here**, and it is independent of any
sourcing work. It converts three exclusions into three real series.

---

## Actelion family: two exclusions do not hold

### Opsumit — exclusion is too aggressive

A standalone series is publicly reconstructible from launch through 2024Q4.

| Issuer | Document | Coverage | Currency |
|---|---|---|---|
| Actelion | Quarterly / FY press releases (GlobeNewswire; actelion.com is dead, mirrors survive) | 2013 launch – FY2016 | CHF |
| Actelion | Annual Reports at `annualreportYYYY.actelion.com` + Financial Report PDFs | 2013–2016 | CHF |
| J&J | **8-K Exhibit 99.2 "Supplementary Sales Data"**, filed with every quarterly earnings release, explicit OPSUMIT line with US / Intl / WW columns | 2017Q2 – 2024Q4 | USD |
| J&J | 10-K MD&A major-product sales table (three fiscal years per filing) | annual | USD |

The Exhibit 99.2 accession pattern under CIK 200406 is stable, so the full
quarterly set is enumerable rather than hunted one at a time. Confirmed
examples: `a8k2017q4exhibit992o.htm`, `a2019q2exhibit992o.htm`,
`a2020q1exhibit992.htm`, and `a2025q1exhibit992.htm` — the last being the one
that shows the combined `OPSUMIT/OPSYNVI` line, confirming the series ends
rather than the data being absent.

Real caveats, none of which justify total exclusion:
- **Currency seam mid-2017**: CHF (Actelion) → USD (J&J), plus a change of
  reporting regime. Needs FX normalization at the join.
- **Likely 1H-2017 hole**: J&J closed the tender 16 June 2017, so its FY2017
  figure is a ~6.5-month stub. Whether Actelion published standalone Q1 2017
  product sales before delisting is unconfirmed. If not, 2017Q1–Q2 is the
  genuine gap — two quarters, not eleven years.
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
| **Opsumit** | combined with Opsynvi from 2025 | **too aggressive** | ~2013/14–2024Q4; Actelion CHF then J&J USD 8-K Ex-99.2 |
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
2. **Opsumit** — the largest clean win. Enumerate J&J 8-K Exhibit 99.2 across
   CIK 200406 for 2017Q2–2024Q4, add Actelion CHF for 2013–2016, resolve the
   1H-2017 stub. The existing positional-PDF reader already handles the
   Exhibit 99.2 layout, since Uptravi comes from that same schedule.
3. **Ventavis** — CoTherix filings give a clean 2005Q1–2006Q3 US series; decide
   whether one interpolated quarter is acceptable or whether Actelion's FY2007
   comparative closes it.
4. **Veletri** — CHF annual 2011–2016, US-scoped.
5. **Flolan** — one document (GSK 20-F FY2010) decides it.
6. **Adempas** — only if territory-scoped series are admitted, with an
   amortization flag.
7. **Alyq / Tadliq / Liqrev** — leave excluded; payer spend belongs in a
   separate table if it is wanted at all.
