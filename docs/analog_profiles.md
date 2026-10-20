# Analog profiles: criteria, options and how each is decided

`seed/analog_profiles.csv` holds one row per product, in four sections:
market conditions, treatment dynamics, product positioning and launch
environment. `seed/analog_profile_evidence.csv` holds, for every value, the
document it was read from. The workbook's Product Profiles sheet is built from
the two.

A product is profiled **on its first approved indication in its launch
market** - the indication its launch, and so its uptake curve, belongs to.
A later indication can change who is treated and how; it does not change the
launch the curve measures. Where the first approval covered several
indications at once, the one the label lists first is used.

Every value is one of the options below, or `Unknown` where no citable
document settles it. `Unknown` is an answer; a guess is not. Each value's
evidence row names the document (a label on DailyMed or Drugs@FDA, an FDA
database, an approval letter, the issuer's filing, a published epidemiology
source) and quotes the part that decides it. A value that follows from the
rubric applied to a quoted fact (a prevalence figure placed in a band) says
so in its basis. A quote is copied from the document at `source_url` as
fetched, never from a search-result excerpt or a summary page.

## Market conditions

| Criterion | Options | Decided by |
|---|---|---|
| Disease Category | Oncology / Infectious Disease / Immunology & Inflammatory / Cardiovascular & Metabolic / Neurology / Other Specialty | The launch indication. Psychiatry is Neurology; vaccines are Infectious Disease; pulmonary arterial hypertension is Cardiovascular & Metabolic; non-malignant hematology, respiratory, ophthalmology, GI, dermatology, urology and women's health are Other Specialty unless the indication is immune-mediated (psoriasis, Crohn's) - then Immunology & Inflammatory. |
| Rare Indication | Rare / Non-Rare | Rare if the launch indication affects fewer than 200,000 people in the US (the Orphan Drug Act threshold), by a cited prevalence figure or an FDA orphan designation for that indication. |
| Disease Course | Acute / Chronic | Acute if the condition resolves within weeks to months with or without treatment (an infection, an acute event, a procedure); otherwise Chronic. Cancer is Chronic, including relapsed or refractory disease. |
| Curability | Curable / Manageable but Non-Curable | Curable if a course of the standard therapy at launch can eradicate the disease (most bacterial infections, HCV, early-stage cancer treated with curative intent); otherwise Manageable but Non-Curable. Metastatic or advanced cancer is Manageable. |
| Disease Pattern | Continuous / Relapsing-Remitting / Episodic | Relapsing-Remitting where flares and remissions define the disease (RMS, ulcerative colitis); Episodic where discrete events recur with health between them (migraine attacks, hemophilia bleeds, infections); otherwise Continuous. |
| Disease-Management Duration | Short-Term / Medium-Term / Long-Term or Indefinite | How long a patient is managed for the indication: Short-Term under 3 months, Medium-Term 3-12 months, Long-Term or Indefinite beyond a year or for life. |
| Incidence Level | Low / Medium / High | New US cases per year: Low under 10,000; Medium 10,000-100,000; High over 100,000. |
| Prevalence Level | Low / Medium / High | US patients living with the condition: Low under 200,000; Medium 200,000-2,000,000; High over 2,000,000. |
| Market Composition | Branded-Dominant / Generic-Dominant / Mixed | Treatments for the indication at the product's launch: Generic-Dominant if the standard of care was mainly off-patent; Branded-Dominant if mainly on-patent brands; Mixed otherwise or where none existed. |

## Treatment dynamics

| Criterion | Options | Decided by |
|---|---|---|
| Treatment Pattern | Continuous / Fixed-Duration / Episodic or As-Needed | The label's dosing: until progression or indefinitely is Continuous; a stated course length (8-12 weeks, 6 cycles, one dose) is Fixed-Duration; dosing on symptoms or events is Episodic or As-Needed. |
| Therapy Structure | Monotherapy / Combination Therapy / Both | The launch label's use: alone, only with named other agents, or either. |
| Route of Administration | Oral / Self-Injection / Office-Administered Injection / Infusion / Other | Self-Injection where the label provides for patient or caregiver administration (pen, autoinjector, prefilled syringe with instructions for use); Office-Administered Injection for an injection given by a health-care professional (intramuscular, intravitreal, subcutaneous in clinic); Infusion for intravenous infusion; Other for inhaled, topical, ophthalmic, implant, intrathecal and the rest. |
| Administration Setting | Home / Physician Office / Infusion Center / Hospital / Mixed | Follows the route unless the label says otherwise: oral, inhaled, topical and self-injected at Home; office injections (intramuscular, intravitreal, subcutaneous in clinic) in the Physician Office; IV infusions in an Infusion Center; a procedure (intrathecal injection, surgical implant) or a label requiring inpatient administration or monitoring in Hospital. Mixed only where the label itself provides for two settings. |
| Benefit Type | Pharmacy Benefit / Medical Benefit / Mixed / Not Applicable | Pharmacy for self-administered products dispensed by a pharmacy; Medical for products administered by a health-care professional; Mixed only where a cited document shows both channels are standard for the product; Not Applicable for inpatient-only products. |
| Testing Requirement | None / General Diagnostic Test / Biomarker Test / Genetic Test | What the label requires before treatment: a companion or required biomarker test; a germline or tumour genetic test; a general diagnostic (a standard lab or imaging confirmation); otherwise None. |
| Monitoring Requirement | Routine / Enhanced / Facility-Based | Facility-Based where a REMS or the label requires a certified facility or observation period; Enhanced where the label requires scheduled lab or clinical monitoring beyond routine care, or a REMS with monitoring; otherwise Routine. |
| Treatment Initiation | Self-Administered After Prescription / HCP-Administered / Inpatient Initiation / Mixed | How the first dose is given under the label. |

## Product positioning

| Criterion | Options | Decided by |
|---|---|---|
| Market Entry Position | First Approved Treatment / First Branded Treatment / Early Entrant / Later Entrant | Counted within the launch indication and its line of therapy as the label states it (a second-line indication is compared with second-line approvals). First Approved Treatment if the product was the first FDA-approved therapy for that indication and line; First Branded Treatment if only generics or off-label use preceded it; Early Entrant if it was the second or third approved product of its kind for the indication, or arrived within three years of the first; otherwise Later Entrant. |
| Mechanism Position | First-in-Class / New Mechanism in an Established Market / Same-Class Entrant | Mechanism means the molecular mechanism (the target and action named in the label's Mechanism of Action section), not the modality. First-in-Class if it is the first product with that mechanism approved in its launch market; New Mechanism in an Established Market if its mechanism was new to an indication that already had treatments; Same-Class Entrant if a product with the same mechanism was already approved for the indication. |
| Treatment Role | Replacement / Add-On / Combination Component | Replacement if the label places it instead of existing therapy; Add-On if it is used on top of continued background therapy; Combination Component if it is approved only as part of a named regimen. |
| Target Population | Broad Population / Defined Subgroup / Biomarker-Selected Population | Biomarker-Selected if the label restricts use to a test-defined group; Defined Subgroup if restricted by severity, line, age or disease subtype; otherwise Broad Population. |
| Treatment Eligibility | Broadly Eligible / Prior Treatment Required / Inadequate Response Required / Intolerant or Refractory / Biomarker-Defined | The launch label's indication statement: after a named prior therapy; after an inadequate response to one; for patients intolerant of or refractory to one; restricted by a biomarker; otherwise Broadly Eligible. |
| Approved Use | Monotherapy / Combination Use / Both | The launch label's indication statement. |
| Dosing Frequency | Daily / Weekly / Monthly / Less Frequent / One-Time / Other | The maintenance dose interval in the launch label: Daily (one or more times a day), Weekly (every 1-3 weeks), Monthly (every 4-8 weeks), Less Frequent (longer than 8 weeks), One-Time; Other for as-needed or irregular dosing. |
| Observable Differentiation | Efficacy / Safety or Tolerability / Route or Administration / Dosing Frequency / New Patient Population / Multiple | What the launch label and pivotal evidence set apart from existing options; New Patient Population where no option existed; Multiple where two or more apply. |
| Clinical Evidence Position | Head-to-Head Superiority / Non-Inferiority / Placebo-Controlled Evidence / No Direct Comparison | The design of the pivotal trial(s) in the launch label's Clinical Studies section: superiority against an active comparator; non-inferiority against one; against placebo or best supportive care; or single-arm. |
| Orphan Designation | Yes / No / Not Applicable | Read from the FDA Orphan Drug Designations and Approvals database: Yes if it lists a designation for the product covering the launch indication; No if a search of the database by product and generic name finds none; Unknown if the database could not be searched. Never inferred from prevalence. Not Applicable for a product not approved by FDA. |

## Launch environment

| Criterion | Options | Decided by |
|---|---|---|
| Launch Geography | US / EU5 / Japan / Other | Where the product was first launched. |
| Launch Year | Calendar year | The year of first commercial launch, from the issuer's documents; the first approval year where no launch date is published. |
| Launch Era | Pre-2010 / 2010-2019 / 2020+ | From Launch Year. |
| Established Competitors at Launch | None / 1-2 / 3-5 / 6+ | Approved products for the same indication and line of therapy marketed at launch, named in the evidence basis. |
| Competing Launches Around Entry | None / One / Multiple Within ±2 Years | Other products approved for the same indication within two years either side of the product's own approval. |
| Distribution Model at Launch | Retail / Specialty Pharmacy / Buy-and-Bill / Restricted Distribution / Mixed | Restricted Distribution where a REMS or the issuer limits dispensing to enrolled pharmacies or sites; Specialty Pharmacy where the issuer distributes through specialty pharmacies; Buy-and-Bill for provider-purchased products; Retail otherwise. |
| Payer Management at Launch | Limited Restrictions / Prior Authorization / Step Edit / Multiple Restrictions / Unknown | Only from a cited document describing coverage at launch (issuer filings, published payer policy); otherwise Unknown. |
| Diagnostic Infrastructure at Launch | Established / Expansion Required / New Infrastructure Required / Unknown | Where Testing Requirement is not None: whether the required test was routinely available at launch (Established), existed but had to be scaled (Expansion Required), or was approved with the product (New Infrastructure Required). Established where no test is required. |
| Prescriber Base at Launch | Established Broad Base / Established Specialist Base / New Prescriber Base Required / Mixed / Unknown | Whether the prescribers of the launch indication already treated it (primary care: Broad; a specialty: Specialist) or the product needed new prescribers (a new treatment setting or specialty). |
