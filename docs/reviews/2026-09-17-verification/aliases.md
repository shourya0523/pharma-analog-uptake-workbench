# Verification: "Wrong identity (6) is an alias set short by one name"

**Verdict: the finding is false in every load-bearing premise, and the proposed mechanism would have made the run worse.** The direction of the failure is the exact inverse of what the register records.

All commands below ran against the branch head `a736ac38` (`git log -1`), the orchestrator read via `git show HEAD:backend/app/pipeline/orchestrator.py` (copied to `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/verify-aliases/orchestrator_HEAD.py`), and a copy of the smoke database plus its `-wal`/`-shm` at `.../scratchpad/verify-aliases/smoke2.db`.

---

## Meaning changes first

**[V] M-1. `Kebilidi` WAS in the job's alias set.** `status: claimed-absent -> present`.

    sqlite: select value from drug_profile_fields
            where job_id='2c5e142d-...' and field='llm_aliases'

    merged = ["Upstaza","eladocagene exuparvovec","Kebilidi",
              "eladocagene exuparvovec-tneq","PTC-AADC","AGIL-AADC",
              "solution for infusion","suspension for infusion",
              "intraputaminal infusion","AAV2 viral vector",
              "PTC Therapeutics","PTC Therapeutics Inc","Agilis Biotherapeutics"]

The model returned it and `merge_aliases` kept it. The register's premise "`Kebilidi` was not in the job's alias set" is false.

**[V] M-2. `reported_as` was never stamped — on this job or anywhere in the run.** `status: stamped 'Upstaza/Kebilidi' -> NULL on all 596 rows`.

    select distinct reported_as from datapoints        -> [None]
    select count(*) from datapoints                    -> 596
    select count(*) from datapoints
      where issue_flags like '%combined%'              -> 0

The six figures were published **under Upstaza's own name**. The register has the polarity backwards.

**[V] M-3. The six "wrong identity" rows are the answer key asking for a pair label the pipeline did not produce.** `scripts/eval.py:216-218` (`covers()`) compares the datapoint's `reported_as` to the case's. Reproduced exactly:

    ev.score(case, datapoints) ->
      2024Q3 correctly silent | 2024Q4..2026Q1 published, wrong identity (6)
      2026Q2 no answer
    whole holdout: {'no answer':15,'published, correct':31,'correctly silent':33,
                    'published, WRONG':1,'published, wrong identity':6,'answered anyway':2}

`/home/user/pharma-analog-uptake-workbench/seed/cases/holdout_2026_09.json`, Upstaza case, expects `reported_as="Upstaza/Kebilidi"` on all seven valued periods; its `shapes` say so in words: `"row label naming two brands joined by '/'"`, `"must come back under the pair's name"`.

**[V] M-4. The rule the register blames does the opposite of what it says.** `backend/app/parsing/labels.py:339-344`:

    # A slash joins the names of one product: a brand and its generic,
    # or the name it carries in another market. Unknown to this reader,
    # such a name is tolerated beside a name it does know.
    if joiner.strip() == "/" and matched is not None:
        continue

Run over the real label, with and without `Kebilidi` in the aliases, with and without the table's sibling row labels:

    read_label("Upstaza/Kebilidi", <job aliases>)            -> matched='Upstaza' combined=() flags=()
    read_label("Upstaza/Kebilidi", <aliases minus Kebilidi>) -> matched='Upstaza' combined=() flags=()
    read_label("Calderon/NuVessa", ["Calderon","calderinol"])-> matched='Calderon' combined=() flags=()
    read_label("Calderon®/NuVessa®", ...)                    -> combined=('NuVessa',) flags=('combined_line',)

Combined detection needs the *second* name to be knowable — a tracked product, a sibling row label, or a name the filer marks with ®/™ (`labels.py:221 _TRADEMARKED_NAME_RE`). PTC prints `Upstaza/Kebilidi` unmarked and gives it no row of its own, so the reader tolerates it. `backend/tests/test_a_slash_joins_one_product_or_two.py` (1c) is about `product_aliases` splitting an *alias string* on `/`, not about reading a *row label*; the register conflated the two.

**[V] M-5. There was no openFDA record to take a brand name from.** The job's only openFDA source is a no-match:

    source_documents: source_type=openfda, retrieval_status=partial,
      source_title='OpenFDA no match',
      source_url='...drugsfda.json?search=openfda.brand_name:"Upstaza"&limit=10',
      notes='No OpenFDA match | parse: No text available'

Live probe today (all three 404):

    curl .../drugsfda.json?search=openfda.brand_name:"UPSTAZA"+OR+products.brand_name:"UPSTAZA"  -> 404
    curl ...:"KEBILIDI"...                                                                       -> 404
    curl ...?search=openfda.generic_name:"eladocagene+exuparvovec"                                -> 404

Kebilidi is a CBER gene therapy; drugsFDA holds neither name. The proposed mechanism — "the openFDA record the same job fetched carries the US brand name" — has no record to read on the one case it was proposed for.

(Drift note, [V]: the stored URL is one path at `limit=10`; HEAD's `search_queries` builds `openfda.brand_name:"X"+OR+products.brand_name:"X"&limit=20`. The smoke run's openFDA pass predates the current connector. The live 404s make the conclusion independent of that.)

**[V] M-6. Even perfect pair detection scores the same six failures, because of the string format.** `orchestrator.py:346 reported_as_for` joins with `" + "`:

    reported_as_for("Upstaza", {"combined_with":["Kebilidi"], "source_quote": <row>})
      -> 'Upstaza + Kebilidi'
    re-score with every row stamped 'Upstaza + Kebilidi' -> 6 published, wrong identity
    re-score with every row stamped 'Upstaza/Kebilidi'   -> 6 published, correct

The answer key demands the filer's exact `/` spelling; the code's vocabulary is `A + B`. This is a second, independent reason the case cannot pass, and nobody has named it.

---

## The four questions

### 1. What the alias set was, and what openFDA carried

Answered above: alias set in M-1 (13 names, `Kebilidi` third); openFDA carried nothing (M-5). The job did write a `brand_name` profile field for the 13 jobs that matched (`select field,count(*) from drug_profile_fields group by field` -> `brand_name: 13`), but not for Upstaza.

### 2. Where aliases are assembled — the call graph

    run_job (orchestrator.py:636)
      └─ _expand_aliases(job)                     :778   <- ONLY writer of self._job_aliases
           ├─ merge_aliases(job.drug_name, job.generic_name)          (llm off)
           ├─ _aliases_already_expanded(job)      :742   cache keyed on ALIAS_QUESTION
           │     = ("drug_name","generic_name","manufacturer","ticker")
           └─ llm.expand_aliases(product, generic, manufacturer, ticker)
                 -> merge_aliases(..., llm_aliases=, formulations=, parent_companies=)
                      -> app/parsing/evidence.py:116 product_aliases
                           -> _franchise_parts :94 -> _spells :73   (held = product+generic only)
      └─ _retrieve  (openFDA)                     :663
      └─ _label_metadata(job, sources, parsed)    :1006  READS self._job_aliases, writes none

`grep -n "self\._job_aliases\s*=" ` on the committed orchestrator returns exactly four lines: 649 (reset), 782, 786, 803 — all inside `run_job`/`_expand_aliases`. **There is no path today by which a record's brand names reach the alias set**, and there structurally cannot be one without a reorder: `_expand_aliases` runs *before* the first `_retrieve` (orchestrator.py:661-663), by design, because the label documents are matched to the product by name. `openfda_brand_names()` is consumed only by `brand_matched_results` and by the no-match log line (`orchestrator.py:1060`). That half of the register's claim — "nothing feeds a record's brand names into the aliases" — is **true**, and useless: it is true and it is not this defect.

### 3. Would it fix the case, and what would it break

**Fix: no.** Verified in M-4 — `read_label` returns the identical reading with and without `Kebilidi`. Adding openFDA brand names changes nothing on this case, and on this case there are none to add.

**Break: yes, verified as a mechanism.** `labels.py:254-259` builds `others`/`sibling_names` by *excluding* anything already in `own_keys`. So an added alias can only ever **suppress** a pair reading, never create one:

    read_label("Pombiliti® + Opfolda®", ["Pombiliti","cipaglucosidase alfa"])
      -> matched='Pombiliti' combined=('Opfolda',) flags=('combined_line',)
    read_label("Pombiliti® + Opfolda®", ["Pombiliti","cipaglucosidase alfa","Opfolda"])
      -> matched='Pombiliti' combined=()          flags=()

`backend/tests/test_a_slash_joins_one_product_or_two.py:79` already encodes this as the defect being guarded against.

**Measured over the 20 products of `seed/example_drugs.csv`** (live drugsFDA, HEAD's `search_queries` + `brand_matched_results` with `product_aliases(brand, generic)`; filter stated: brand query first, molecule query only when the brand query 404s; brand names taken from `openfda_brand_names` over *matched* records only):

| product | matched records | brand names that are not the product |
|---|---|---|
| Uptravi | 2 | `UPTRAVI TITRATION PACK` (NDA207947 `openfda.brand_name`) |
| Remodulin | 2 | `STERILE DILUENT FOR REMODULIN` (NDA021272 `openfda.brand_name`) |
| the other 18 | 1–3 | none |

So **2 non-product names over 20 products**. `STERILE DILUENT FOR REMODULIN` is a different marketed item on the same application; as an alias it would make a row naming the diluent read as Remodulin's own revenue. Neither of the two is a co-packaged partner brand, so on *this* catalogue no genuine combined line flips. The sharper leak is the fallback path: `Nebulized Tyvaso` gets `match_scope='generic'` and 0 matched records, and the returned window carries

    ['DILUENT','ORENITRAM','REMODULIN','STERILE DILUENT FOR REMODULIN',
     'STERILE DILUENT FOR TREPROSTINIL','TREPROSTINIL','TYVASO','TYVASO DPI','YUTREPIA']

— i.e. an implementation that fed brand names from *retrieved* rather than *matched* records would alias a product to its four nearest competitors, and every competitor row in a United Therapeutics schedule would read as this product's own. That is rule 3's shape in reverse, and it is one `brand_matched_results` call away from happening by accident.

### 4. One product or two, and what the pair label costs

**One product.** The filer says so, in the 10-K the case cites (`.../cache/sec/000110465926017575/tmb-20251231x10k.htm`):

> "Upstaza ™ (eladocagene exuparvovec) / Kebilidi ™ (eladocagene exuparvovec-tneq) – Upstaza, a gene therapy for the treatment of Aromatic L-Amino Decarboxylase, or AADC, deficiency ... is approved ... within the EEA and the United Kingdom. **This gene therapy is approved and marketed with the brand name Kebilidi in the United States.**"
> "Upstaza/Kebilidi **is** an adeno-associated virus, or AAV, gene therapy for the treatment of AADC deficiency"

Same molecule (US suffix `-tneq`), same indication, same applicant, two regional brands, one worldwide revenue line, singular verb.

**What the pair label would cost the analyst.** `reported_as` is not a display string; it has consequences:

- `orchestrator.py:1621-1634`: any quarter where every figure carries `reported_as` gets an `UnresolvedQuarterORM` reading "*Upstaza is reported only as ...; no figure for Upstaza alone is disclosed*", reason code `REPORTED_WITH_ANOTHER_PRODUCT`. For Upstaza that is **every quarter of the series**, so the one complete, exact, worldwide launch curve of a rare-disease gene therapy — the archetypal analog for this user — arrives as eight review-queue entries saying its revenue is undisclosed.
- `backend/app/pipeline/series_identity.py:208`: `reported_as` becomes a component of the series key, so the pair line is a different series from the product's own.
- `backend/app/analytics/*.py`: `grep -rn "reported_as" backend/app/analytics/` returns nothing. Layer 3 never reads it, so the analog maths are unaffected either way.

So: `reported_as='Upstaza/Kebilidi'` on this product is **wrong, not merely unhelpful** — under the field's own docstring ("what a figure is a figure for, where that is **not** the product asked about", `orchestrator.py:346`) it asserts that Upstaza did not sell what the row reports. The current output (`reported_as=None`, one worldwide series, exact values) is the **right answer for the analyst and a failing answer for the case file**. The defect that the six rows expose is in `seed/cases/holdout_2026_09.json`, not in `backend/app/`.

One more thing the case gets right that nobody has costed: **the filer disagrees with itself inside one document.** The 2025Q3 figure the pipeline published (15.735) is tagged `srt:ProductOrServiceAxis = ptct:UpstazaMember` while the HTML row above it reads `Upstaza/Kebilidi`; from the 2026 10-Q onward the member is `ptct:UpstazaAndKebilidiMember` (`grep -o "ptct:\w*(Upstaza|Kebilidi)\w*Member"` across the cached filings shows `UpstazaMember` in 2024-2025 filings and `UpstazaAndKebilidiMember` only from `000107008126000012` onward). Any rule that stamps identity from the row label and not from the XBRL dimension will disagree with the filer's own tag on 2 of the 6 rows.

---

## Rule checks (protocol test 4)

- **Rule 2 (the register's own evidence).** The register states a data fact — "`Kebilidi` was not in the job's alias set" and "stamped `reported_as='Upstaza/Kebilidi'`" — with no command behind either. Both are false against the database it names. `select distinct reported_as from datapoints` would have caught it in one line. Report this as its own line item: **the register's Upstaza entry was written from the eval's state name, not from the rows.**
- **Rule 1.** No written-down list is implicated; the current code derives correctly (`BRAND_SEARCH_PATHS` from `brand_name_paths()`, `LABEL_FLAGS` from `QUESTION_FLAGS`). A fix that hardcodes any pair spelling would introduce one.
- **Rule 3.** `seed/cases/holdout_2026_09.json` is a scored set. The string `Upstaza/Kebilidi` appears in it; it must not appear in `backend/app/`. Note that a fix aimed at making `reported_as` match the key's spelling is exactly the shape of fitting code to the answer key.
- **Rule 4.** The 2026-09 set has now been read against this diagnosis twice and is spent. Nothing may be scored on it.
- **Rule 5.** No measurement found in the comments I read in `labels.py`, `evidence.py`, `openfda.py`, `openfda_fields.py`.

---

## What the item actually is, and a proposal (offered, not decided)

The item is **not** an alias feed. It is three separable things:

**(a) An answer-key question, not a code question.** Does a regional-brand pair get `reported_as`? The product says no (question 4). If the answer is no, the fix is one edit to `seed/cases/holdout_2026_09.json` removing `reported_as` from the Upstaza expectations — and the score moves from 6 wrong-identity to 6 correct with no code change at all (verified: `re-score with reported_as stamped 'Upstaza/Kebilidi' -> 6 published, correct`, and symmetrically the unstamped rows already match a case that asks for nothing). That is a decision for a person, and it is rule-4 sensitive: editing the key after seeing the score is fitting unless the reason is stated as a property ("a regional brand pair is one product") rather than as a number.

**(b) If the answer is yes, the vocabulary is wrong.** `reported_as_for` emits `A + B`; the key wants the filer's `A/B`. Files: `backend/app/pipeline/orchestrator.py:346` and `backend/app/parsing/labels.py`. Preserves: the `A + B` form for genuine co-packaged pairs, which `backend/tests/test_a_pair_is_published_as_the_pair.py:32` pins. Risks: any change to the join string breaks that test and every stored `series_identity` slug.

**(c) The real, unclaimed defect on this job is period assignment, not identity.** The 2026Q2 expectation scores `no answer` because the 2026Q2 10-Q row `Upstaza/Kebilidi — 11,163 11,163 — 11,889 11,889` (verified in `.../cache/sec/000107008126000017/tmb-20260630x10q.htm`: `Three Months Ended June 30, 2026 | 2025`) had its **current-quarter** value 11,163 filed as **2025Q2** and then superseded by the correct XBRL 11.889. That is section 6's two-numbers-on-one-row auto-pass gate, arriving again, and it costs a whole quarter.

**If anything is implemented**, the guard test must answer both ways, and neither answer may name a real brand (rule 5):

    read_label("Calderon/Calderon EU", ["Calderon","calderinol"])
        -> one product: combined_with == (), reported_as is None
    read_label("Calderon® + NuVessa®", ["Calderon","calderinol"])
        -> two products: combined_with == ("NuVessa",), reported_as == "Calderon + NuVessa"

and a third that does not exist today and should: **a stamp must not split a quarter into two series.** Simulated on the smoke rows — stamping only the label-derived readings and leaving the XBRL facts unstamped gives 2025Q2 two published figures (11.889 unstamped, 11.163 stamped) under two different `series_identity` keys, which `scripts/eval.py:234` scores `published, conflicting`. Today they share a key and one supersedes the other. Any partial stamping regresses that.

**Scoring set (rule 4):** not `holdout_2026_09`. A new set drawn from issuers none of `seed/holdout`, `seed/holdout2`, `seed/holdout_labels`, `seed/holdout_members` or `seed/cases/holdout_2026_09.json` uses, and it must contain **both** a regional-brand pair row and a genuine co-packaged pair row, or a system that stamps everything (or nothing) passes it.

---

## Verified vs inferred

**Verified by command:** M-1 through M-6; the call graph and the four `self._job_aliases` writes; the 20-product openFDA brand-name census and the `Nebulized Tyvaso` generic-fallback window; the alias-suppresses-pair mechanism; the 10-K prose on one therapy / two brands; the XBRL member drift; the 2026Q2 column order; `derive.py:551` carrying `reported_as` through derivations; `analytics/` never reading `reported_as`; the three label guard test files green (47 passed).

**Inferred, not run:** that in a live run the two `derived_from_period_total` quarters would inherit a pair stamp from their annual/nine-month inputs (read from `backend/app/extraction/derive.py:535-555`, not executed end to end); that the smoke run's openFDA pass predates HEAD's connector (inferred from the stored URL's shape, not from a commit bisect).

**Files that matter:** `/home/user/pharma-analog-uptake-workbench/backend/app/parsing/labels.py` (235-361), `/home/user/pharma-analog-uptake-workbench/backend/app/parsing/evidence.py` (94-139), `/home/user/pharma-analog-uptake-workbench/backend/app/pipeline/orchestrator.py` (346-369, 736-830, 1006-1200, 1610-1645), `/home/user/pharma-analog-uptake-workbench/backend/app/connectors/openfda_fields.py` (30-160), `/home/user/pharma-analog-uptake-workbench/backend/app/pipeline/series_identity.py` (175-215), `/home/user/pharma-analog-uptake-workbench/scripts/eval.py` (193-256), `/home/user/pharma-analog-uptake-workbench/seed/cases/holdout_2026_09.json`, `/home/user/pharma-analog-uptake-workbench/backend/tests/test_a_slash_joins_one_product_or_two.py`, `/home/user/pharma-analog-uptake-workbench/backend/tests/test_a_pair_is_published_as_the_pair.py`.

No file in the repository was changed; all scratch work is under `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/verify-aliases/`.
