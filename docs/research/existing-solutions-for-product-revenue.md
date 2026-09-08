# Existing solutions for product-level revenue

Two attempts in this repository build product-level quarterly revenue from
scratch. This document asks what already exists, under the constraint that the
answer must not depend on a commercial or academic dataset.

The short version: **the SEC already publishes the thing both attempts are
reading documents to reconstruct, as a free bulk download, with the product,
the geography, the period and the unit all declared** — and the assumption that
sent both attempts to the documents instead is wrong for two of this project's
six issuers, across half the gold set.

---

## 1. What the two attempts do

Both target the same thing: one product, one quarter, one number, and a
citation that stands on its own. Gold is 1,415 quarterly rows, 39 products,
six issuers, 2002Q1-2026Q2.

**`main` — model-first.** Deterministic EDGAR sourcing, then an LLM
*fingerprinter* that describes a table (grid index, unit, currency, per-column
period and geography, per-row product and line kind) without ever emitting a
number; extraction then places described rows on candidate layouts and keeps
only placements whose own arithmetic holds. Two-tier model routing, ~$3 to
re-describe the corpus from a cold cache. Scores 993/993 on gold and 111/111
and 126/126 on two held-out sets — but §8 of its own audit records that the
LLM fingerprinter is *not yet a net gain* over the header grammar it replaced,
costing 45 rows their direct reading.

**`claude/gold-dataset-gaps-rd67js` — structured-first.** Four producers in
rank order: tagged XBRL facts, LLM extractor, deterministic table reader
(colspan geometry), derivations. Then an evidence judge and reconciliation
ranked by `SOURCE_PRIORITY` (document) and `CLAIM_STRENGTH` (producer).
Deterministic floor 1,313/1,415 (92.8%) with 12 wrong. Split by era: 2002-2009
**7.1%**, 2010-2019 75.8%, 2020-2026 74.5%.

Both are unusually honest instruments — the branch's `docs/evaluation.md` and
plans 003/004 are a better account of what was measured than most production
systems have. Neither is the problem. **The input is.**

## 2. The load-bearing assumption, and why it is wrong

`docs/plans/2026-09-07-002-structured-first-extraction.md` states:

> Product-level revenue is XBRL-tagged for every issuer in gold, **starting in
> 2019** … (product-axis contexts in that year's 10-Q; zero before 2019 for all
> three)

and plan 003 generalises it into a rule of the data:

> **iXBRL detail tagging phases in by filer class** — large accelerated for
> periods ending on/after 15 June 2019 …

That is true of *inline* detail tagging in the primary document, and it is true
of J&J. It is **false for Gilead and United Therapeutics**, and the difference
is worth about half the gold set. Checked directly against the SEC's own bulk
files (method in §7):

| issuer | earliest per-brand product-axis revenue facts found | evidence |
|---|---|---|
| **Gilead** | period **2009Q2**, in the 2010 Q2 10-Q | Atripla, Truvada, AmBisome, Letairis, Ranexa, Viread, Emtriva, Hepsera — each its own member, `qtrs=1` |
| **United Therapeutics** | period **2011Q2**, in the 2012 Q2 10-Q | Remodulin, Tyvaso by name, `qtrs=1`; Orenitram by 2016 |
| Merck | 2009, but members are **obfuscated** (`ProductOne`, `ProductThirtyThree`) until ~2013; real names (Vytorin) by 2016 | unusable early without a mapping |
| Johnson & Johnson | **2019** — none in 2012q3 or 2016q3, 484 facts in 2019q3 | the 2019 rule holds, for J&J only |

Gold rows by XBRL era:

| era | rows | share |
|---|---|---|
| pre-2009 (no XBRL exists at all) | 70 | 4.9% |
| **2009-2018** | **708** | **50.0%** |
| 2019+ | 637 | 45.0% |

Of the 708 rows in the band the project wrote off, **319 are Gilead and 173 are
United Therapeutics** — the two issuers demonstrably tagging per-brand in that
band. J&J's 192 rows there genuinely are not tagged.

The products this cost are exactly the ones the docs list as unreachable.
`docs/evaluation.md` names "Atripla (37), Truvada (36), AmBisome (34)" as
products "an issuer folds into a franchise line", and
`docs/sourcing/excluded-products.md` excludes **Letairis** outright because
"Gilead reports it only inside 'Other product sales'". In Gilead's Q2 2010
10-Q all four are tagged members carrying single-quarter values:

```
AmBisome                  2009Q2  73.3   2010Q2  78.2
AntiviralProductsAtripla  2009Q2 569.1   2010Q2 715.8
AntiviralProductsTruvada  2009Q2 608.1   2010Q2 641.7
Letairis                  2009Q2  44.1   2010Q2  60.3
```

Against gold, every overlapping value agrees exactly:

| product | period | gold | XBRL | |
|---|---|---|---|---|
| AmBisome | 2010Q2 | 78.2 | 78.2 | ✅ |
| Atripla | 2009Q2 | 569.1 | 569.1 | ✅ |
| Atripla | 2010Q2 | 715.8 | 715.8 | ✅ |
| Truvada | 2009Q2 | 608.1 | 608.1 | ✅ |
| Truvada | 2010Q2 | 641.7 | 641.7 | ✅ |

Letairis, Ranexa and Viread have **no gold rows at all** for that quarter —
Letairis because it was excluded on the stated ground that the figure is not
published. It is published, and has been since 2010.

## 3. The route: SEC Financial Statement **and Notes** Data Sets

This is the finding. There are two DERA products and the project used neither;
the distinction between them is the whole point.

| | Financial Statement Data Sets | **Financial Statement _and Notes_ Data Sets** |
|---|---|---|
| scope | "numeric information from the **face financials**" | "the text and detailed numeric information from **all financial statements and their notes**" |
| dimensions | `segments` field added Dec 2024 | full **`DIM`** data set: "all of the combinations of XBRL axis and member used to tag any submission" |
| coverage | Jan 2009 – present | **Jan 2009 – present**, monthly since Nov 2020 |
| cost | free | free |

Product-level revenue is a *note* disclosure (ASC 606 disaggregation), not a
face-financial line. It lives in the second product, which the project never
touched — `main` uses no XBRL bulk data at all, and the branch parses instance
documents one filing at a time.

Segments are encoded as `{axis}={member};` pairs, e.g.
`BusinessSegments=InnovativeMedicine;Geographical=US;ProductOrService=Xarelto;`.
`NUM.dimh` joins to `DIM.dimhash`; `NUM.qtrs` gives the duration in quarters
(so `qtrs=1` *is* the quarter, stated, not derived); `NUM.adsh` is the
accession number, which resolves to the filing the fact was published in.

What one free 108 MB file yields for J&J's Q2 2026 10-Q — **424** product-axis
revenue facts, and the geography split is a second axis:

| product | period | qtrs | US | Non-US | Worldwide |
|---|---|---|---|---|---|
| Stelara | 2026Q2 | 1 | 336 | 404 | 740 |
| Stelara | 2025Q2 | 1 | 1,078 | 575 | 1,653 |
| Uptravi | 2026Q2 | 1 | 386 | 108 | 494 |
| Uptravi | 2025Q2 | 1 | 382 | 94 | 476 |

Every hard problem the readers solve by inference is *declared* here: the
period (context dates), the unit (`uom`, unscaled `value`), which row is the
product (the axis member is an identity), and regional line vs worldwide total
(geography is a separate axis; its absence **is** worldwide). Prior-year
comparatives come along for free, which is a second, independent reading of
older quarters.

### What it does not solve

Three limits, each checked rather than assumed:

- **8-K earnings exhibits are not tagged.** United Therapeutics' 8-K of
  31 July 2026 appears in the dataset with **zero** numeric XBRL facts. Any
  quarter whose only source is an earnings exhibit still needs the document
  readers both branches built. This is not a replacement for them.
- **Pre-2009 is empty.** Scope begins with submissions from 15 April 2009.
  The 70 gold rows before 2009 (4.9%) stay a document-reading problem, and
  EDGAR's `.txt` submissions of that era stay hard. The branch's 7.1% on
  2002-2009 is the floor for a genuinely unreachable band, not a failure.
- **The franchise line is the issuer's own tagging, not a reader failure.**
  J&J tags `ProductOrService=INVEGASUSTENNAXEPLIONTRINZATREVICTA` as a single
  member. No extraction technique recovers the four brands from a figure the
  filer never split. That exclusion is correct and should stop being treated
  as a gap.

### The SEC's JSON APIs cannot substitute

Worth stating because it is the obvious thing to reach for. `companyfacts`,
`companyconcept` and `frames` aggregate only facts that "Apply to the entire
filing entity". Gilead's `RevenueFromContractWithCustomerExcludingAssessedTax`
returns 90 facts, **every one ≥ $5.088bn** — consolidated totals, no product
values at any scale. Dimensional facts are excluded by design. The branch's
instance-parsing was the right call; it was just done one filing at a time
instead of in bulk.

## 4. Other routes that meet the constraint

**XBRL US Public Filings Database + XBRL API.** A Postgres mirror of every SEC
XBRL filing since 2009, updated ~every 15 minutes, queryable *by dimension*:
`dimension.local-name=ProductOrServiceAxis` with `member.local-name` is a
documented query shape. A free API key is available at `xbrl.us/apirequest`
and "anyone can explore … without cost or obligation"; unlimited/no-quota
access and direct SQL require membership. Non-profit consortium, not a
commercial data vendor. Useful as the incremental/live path where the monthly
bulk file is too coarse. *Quota specifics for the free tier were not verified.*

**Issuer IR files, for what XBRL cannot reach.** J&J publishes "Sales of Key
Products/Franchises" schedules per quarter back to at least 2013 and links a
historical-sales archive; Merck posts "Other Financial Disclosures (Excel)"
each quarter. These are the documents gold already cites and the branch already
reads — worth keeping for the untagged 8-K quarters and pre-2009, not worth
re-plumbing. *Depth of each archive was not verified beyond J&J 2013 and
Merck's current-quarter Excel.*

**Open-source XBRL tooling.** `edgartools`, Arelle and `sec-api`'s XBRL-to-JSON
all read XBRL first, as the branch's own research note says. Arelle is the
reference implementation and is the right thing to reach for if instance-level
parsing is kept for the 8-K path. None of these supply data; they supply
parsing the project largely already has.

## 5. Ruled out by the constraint

Recorded so the question is not reopened. The completed research verified these
before the constraint was set; none are recommended.

- **GlobalData Pharma Drug Sales & Forecasts** — annual *and quarterly*
  history from 2000, global, by region and indication. But it blends
  "proprietary company and prescription drug sales data … with analyst
  consensus forecasts" across 60,000+ sources, and does not claim per-figure
  citation to the issuer's filing. Commercial licence.
- **IQVIA MIDAS / National Sales Perspectives** — *estimated*, audit-projected
  channel sales (MIDAS: 87 countries, 709 therapeutic classes; NSP: US-only,
  projected from a ~90% wholesaler panel). This is not issuer-reported net
  revenue and is not citable to a disclosure. Categorically the wrong measure
  for this benchmark, licence aside.
- **Clarivate Cortellis** — ~3,000 drug sales *forecasts*; no stated count of
  historical quarterly actuals.
- **Evaluate Pharma / Biomedtracker** — compiled from 10-K/10-Q and company
  presentations, so traceable in principle; the academic uptake-curve
  literature licenses these rather than building from filings. Commercial.
- **Daloopa** — the one vendor whose model matches the requirement: every data
  point hyperlinked to its location in the source filing. Coverage of
  pharma product-by-geography for these 39 products could not be confirmed,
  and depth appears to be roughly a decade. Commercial.
- **Curated web compilations** (Drugs.com, Wikipedia, Statista) — already
  blocklisted in `scripts/audit_gold.py`, correctly.

## 6. Recommendation

1. **Ingest the Notes Data Sets as a fifth, top-ranked producer**, above tagged
   facts read from instances. Same evidence class as the existing tagged path
   — element, context, member, accession — so it slots under `CLAIM_STRENGTH`
   without a new provenance story. `seed/xbrl_members.csv` already solves
   member→product resolution keyed by `(issuer, member)`; these members feed
   the same register.
2. **Re-measure the 2009-2018 band before building anything else.** The
   opportunity is 708 gold rows, of which ~492 are Gilead and UTHR. Prove the
   quarter-by-quarter hit rate against gold — the five values checked here all
   matched exactly, but five is not a measurement.
3. **Revisit the exclusions on evidence.** Letairis is excluded on a factual
   claim that is wrong. Atripla, Truvada and AmBisome are counted as
   franchise-folded and are not.
4. **Keep the document readers for what only they can do**: 8-K-only quarters,
   pre-2009, and non-SEC filers. Their scope shrinks a lot, which makes the
   prose reader's 5-correct/13-wrong trade easier to settle by deletion.
5. **Do not treat this as a way to stop reading documents.** It is a way to
   stop *inferring* what the filer already declared.

## 7. Method

Every claim in §2 and §3 was checked by downloading SEC bulk files in this
session and joining them locally — no vendor, no intermediary:

```
https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/2026_07_notes.zip
                                    …/2019q3_notes.zip  …/2016q3_notes.zip
                                    …/2012q3_notes.zip  …/2011q1_notes.zip
                                    …/2010q3_notes.zip
```

Join `NUM.dimh → DIM.dimhash`, filter `segments` containing `ProductOrService`
and `tag` containing `Revenue`, restrict `adsh` to the issuer's submissions via
`SUB`. Gold comparison is against `seed/gold/quarterly_revenue.jsonl` at
`geography = Worldwide`. Files were deleted after checking.

**Flagged as unverified:** XBRL US free-tier quotas; the depth of each issuer's
IR archive beyond J&J (2013) and Merck (current quarter); whether every quarter
in 2009-2018 is tagged for every gold product — only the sampled filings above
were opened.

### Sources

- [SEC — Financial Statement and Notes Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets)
- [SEC — Notes Data Sets documentation (PDF)](https://www.sec.gov/files/aqfsn_1.pdf)
- [SEC — Financial Statement Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets)
- [SEC — EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [XBRL US — The XBRL API](https://xbrl.us/home/priorities/use/xbrl-api/) and [Database of Public Filings](https://xbrl.us/home/use/filings-database)
- [XBRL US — disaggregated revenue tagging guidance](https://xbrl.us/data-rule/guid-revenuepr/)
- [J&J — quarterly results](https://www.investor.jnj.com/financials/quarterly-results/default.aspx) · [Merck — financial information](https://www.merck.com/investor-relations/financial-information/)
- [GlobalData Pharma Drug Sales & Forecasts](https://www.globaldata.com/marketplace/dataset/pharma-drug-sales-forecasts/) · [IQVIA data](https://www.iqvia.com/insights/the-iqvia-institute/available-iqvia-data) · [Cortellis](https://clarivate.com/life-sciences-healthcare/portfolio-strategy/competitive-intelligence/cortellis-competitive-intelligence-analytics/) · [Daloopa](https://daloopa.com/benefits/deepest-dataset)
