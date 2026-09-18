
# Audit: `backend/app/parsing/` (minus `fda_label.py`, `indications.py`), `backend/app/extraction/`, `quality/sentences.py`, `quality/candidate_filters.py`, `744864d..830aad2`

Snapshot read: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/wt-audit` (detached at `830aad2`). Scratch scripts: `/tmp/claude-0/-home-user-pharma-analog-uptake-workbench/2246df3a-f6aa-5795-b4af-896b904953c0/scratchpad/audit-parsing/`. Nothing edited anywhere.

Shape of the change set in scope: 10 substantive commits, ~700 lines of app code, ~1,900 lines of new/changed tests. `git -C <snap> diff --stat 744864d 830aad2 -- backend/app/parsing backend/app/extraction backend/app/quality/sentences.py backend/app/quality/candidate_filters.py` gives 21 app files, 561 insertions in app code.

Headline: **the commit messages measure what they claim** — I re-ran the load-bearing ones and they reproduce. The complexity added is mostly proportionate. There are **four real simplifications** (one measured at zero behavioural cost), **one verified live fragility** in `evidence.py`, and **one dead property** plus **one now-false log line**.

---

## A. `b257270` (5b) + `26b96ca` (5c) — the footnote reader, `labels.py` + new grammar in `periods.py`

**Value.** Measured, not asserted, and it reproduces. The ANI note is read correctly now:

```
$ ./.venv/bin/python .../audit-parsing/p1.py
read_footnote("(1) There were no sales of YUTIQ during the quarters ended March 31, 2026
   and June 30, 2026, as the issuer moved promotion to Calderon during the second quarter of 2025.")
 -> NoteReading(names=(), flags=('footnote_says_no_sales',), months=3,
                periods=frozenset({'2026Q2','2026Q1'}), no_sales_of=('YUTIQ',))
_includes("(2) Full year 2026 guidance does not include sales of NuVessa.", [...]) -> ((), None)
```

I re-derived the corpus numbers independently (964 footnotes from `table_footnotes` over every `<table>` in the 433 `.htm` of `run7/storage/cache/sec`; the commit's 679 is the narrower "selected tables" predicate, same direction):

```
$ ./.venv/bin/python .../audit-parsing/p2.py
notes: 964
notes naming >=1 period anywhere: 120
  naming >1 distinct period key: 52        <- what the single-period return could not express
notes with a negator within 40 chars before an includes-claim: 6
  of those, negator immediately adjacent (lookbehind catches): 6
```

The clause cut is **right on every corpus instance**. All 10 notes where `_claim_clause` discards a period are correct discards — 7 are `Total manufacturing commitments includes the Catalent ... agreement, for which ... recorded on the ... balance sheet as of <date>` (a balance-sheet date, not the figure's period), 1 is the ANI guidance note (`.../audit-parsing/p3.py`). Zero observed over-cutting.

**Complexity cost.** `labels.py`: `_CLAUSE_BREAK_RE` (:111), `_INCLUDES_CLAIM`/`_NEGATES` (:94-95), `_claim_clause` (:389), `_includes` (:415), `_no_sales_of` gains a position return (:437), `NoteReading.periods` + `states_a_scope` (:362, :365). `periods.py`: `NamedPeriod` (:388), `periods_named` (:448), `period_key` (:525), `_NAMED_PHRASE_RE` (:162), `_quarter_form_hits` (:244), `_dates_after` (:426), `_month_of`/`_month_at` (:172, :192), `_DATE_JOIN_RE` (:404). Against that it **deletes** four literals (`_NOTE_SPAN_RE`, `_NOTE_PERIOD_RE`, `_SPAN_MONTHS`, `_QUARTER_WORDS`) that were copies of `periods.py`'s. Held by 33 tests in `tests/test_row_labels.py` plus `tests/test_the_judge_sees_the_row.py`.

The multi-date machinery earns its keep beyond the one ANI note: **26 footnote headings across 16 distinct notes state two end dates** (`.../audit-parsing/p11.py`), e.g. `"(1) Reported net sales for the three months ended September 30, 2023 and October 1, 2022 ..."`.

**Simpler alternative (measured, zero cost).** `periods.py:156` and `:162` are two compiled spellings of one grammar, differing only by `|quarters?`, with a six-line comment justifying the split. Folding them into one pattern changes **nothing**:

```
$ ./.venv/bin/python .../audit-parsing/p10.py
documents: 433   datable before: 242
documents whose date changes if the note form is folded into the phrase pattern: 0
```
(`detect_period_context` run over `soup.get_text("\n", strip=True)[:400_000]` for all 433 cached `.htm`, before and after `_PERIOD_PHRASE_RE = _NAMED_PHRASE_RE`.)
**Do this:** delete `_PERIOD_PHRASE_RE`, have `detect_period_context` use the one pattern. Removes a constant and the comment that exists only to explain why there are two. Risk: none measured on the corpus the change was built against.

**Second simpler alternative.** `NoteReading` carries scope in two half-representations — a scalar `months` (:358) and a set of keys `periods` (:362) — where `periods_named` already produces `(months, key)` per period. That duality produces `states_a_scope` (:365) as a two-clause `or`, and a latent mis-pairing:

```
$ ./.venv/bin/python -c "... period_span('2024', 3)"
period_span("2024", 3) = (2024-01-01, 2024-03-31)   <- an annual key read as a quarter span
```
`_dates_part_of_the_period` (:456) passes **one** `months` — `next((p.months for p in named), None)`, the first period's — against **all** keys, so a clause naming a three-month span before an annual one would test the annual key over a Q1 window. I could not construct a corpus note that reaches it (the one I built has its periods cut by `since`), so this is **latent, not live**. Collapsing to a single `frozenset[tuple[int, str | None]]` removes the field duality, `states_a_scope`, and the hazard together.

**Dead code.** `NoteReading.states_a_scope` (`labels.py:365`) has **no production caller**:
```
$ grep -rn "states_a_scope" app tests
app/parsing/labels.py:366 ...
tests/test_row_labels.py:150, :186
```
It is a property only tests call. The commit message says it is "askable by a caller that drops a figure on what a note says" — no such caller was added. Delete it or add the caller.

**Fragile.** `_includes`'s negation guard is one-sided: `(?<!not\s)(?<!never\s)` covers only a negator immediately before the claim word, while `_NEGATES` covers everything *after* it. Verified:
```
$ ./.venv/bin/python .../audit-parsing/p1.py
'(2) Guidance does not currently include sales of NuVessa.'            -> (('NuVessa',), 32)
'(2) Net revenue does not, for the periods presented, include sales of NuVessa.' -> (('NuVessa',), 53)
```
Both read as "the line combines two products" — the exact 5c failure, one adverb away. Zero occurrences in the 964-note corpus (all 6 negated claims are adjacent), so **not live**. The fix is smaller than the current code, not larger: replace the two lookbehinds with the same tempered-negation window used after the claim.

**Minor.** `_includes` compiles a fresh f-string pattern per candidate name per note; with a 30-row schedule that is 30 `re.search` compiles per footnote, relying on `re`'s 512-entry cache. Not measured as a cost; noting only because one pattern over an alternation of the names would be both shorter and cheaper.

---

## B. `05498fc` (7d, parsing half) — dated partial periods, footnote citation, peer names

**Value.** Verified. The AGAMREE eighteen-day stub now flags:
```
read_footnote("net product revenue for the three months ended March 31, 2024 is for the period
   between March 13, 2024 (date of commercial launch) and March 31, 2024", ["Calderon"])
 -> NoteReading(names=(), flags=('partial_period',), months=3, periods=frozenset({'2024Q1'}))
```
`cite_footnote`/`footnotes_in` (`labels.py:535`, `:540`) are a genuine producer/reader pair — `extract.py:747` writes, `orchestrator.py:276,277,2438` read. Correct and minimal.

**Duplication / seam — the clearest one in scope.** `parsing/tables.py:78 sibling_row_labels` and `quality/candidate_filters.py:159 peer_product_names` are one idea in two modules, and the composition is written **verbatim twice**:

```
orchestrator.py:2079-2085   peers = peer_product_names(sibling_row_labels(doc.tables, product=..., generic=..., extra_aliases=extra), job.drug_name, job.generic_name, extra)
orchestrator.py:2462-2468   peers = peer_product_names(sibling_row_labels(getattr(doc,"tables",None), product=..., generic=..., extra_aliases=aliases), job.drug_name, job.generic_name, aliases)
```
The same four arguments are threaded to both functions, and `product_aliases(product, generic, extra)` is computed **twice per call** (`tables.py:92`, `candidate_filters.py:173`). Neither caller uses the intermediate label list. Neither function is named by any test (`grep -rln "sibling_row_labels\|peer_product_names" tests` -> nothing); they are held only through `tests/test_the_judge_sees_the_row.py:109,128`.
**Do this:** one function — `peer_product_names(tables, *, product, generic, extra_aliases)` — in one module, aliases computed once, and the two orchestrator sites become one line each. Preserves the rule-3 property exactly (the document is still the supplier; no brand catalogue). Loses nothing.

**Also.** `read_footnote`'s `claims = [sold_at, includes_at, partial.start()]` then `min(claims)` (`labels.py:~510`) scopes *all* claims in a note by the *earliest* claim's clause. Defensible and documented; noting because it is the one place the "one clause, one claim" idea is approximated.

---

## C. `1221ac0` (7a) — `detect_period_context`

**Value.** Measured in the commit (10 of 253 documents change, each onto the period its filename states). The fix reuses the predicate that already existed 49 lines above in `_quarter_notation`, so it adds **three lines and removes a nine-line comment that carried counts and a defect history** (rule 5). Held by one new test, `tests/test_periods.py:149`.

**This is already as simple as the value allows.** No alternative.

---

## D. `36dbe13` (7c) — `MONTHS_TO_PERIOD_TYPE`, `period_label`, `Fact.period`

**Value.** Measured (before `{3:90,12:46}`, after `{3:90,6:29,9:30,12:46}` through `product_facts`). Pure rule-1 consolidation: one map and one label producer replace four hand-written span vocabularies (`xbrl.Fact.period`, `tagged.py:165`, `bulk_tagged.py:160`, `check.py:77` inverted). Eight tests rewritten.

**Complexity cost is negative** — the diff deletes more decision sites than it adds. `check.py:77` inverts rather than restating. Leave exactly as is.

**One seam outside scope worth a line:** `llm/client.py:802-804 _SPAN_OF_PERIOD_TYPE` is the same inversion as `check.py:77 _MONTHS_BY_PERIOD_TYPE`. Both are derived (so neither can go stale), but the inversion is written twice; a `PERIOD_TYPE_TO_MONTHS` beside `MONTHS_TO_PERIOD_TYPE` in `periods.py` would remove one.

---

## E. `ff97066` (6b/6c) — `prose.py` period namespace

**Value.** Reproduces on run13's stored quotes. The **span x year cross-product is heavily load-bearing**, not decoration:

```
$ ./.venv/bin/python .../audit-parsing/p5.py     (549 quotes from run13/workbench.db)
veto fires, as shipped (cross-product):                     3
veto fires, without the span x year cross-product:         79
veto fires, periods.py grammar alone, no cross-product:    80
```
So the cross-product is what turns 79 held rows into 3 — the register's classes A and B.

**Cost it buys.** The cross-product adds **355 keys over 197 of the 549 quotes** that neither grammar literally named (max 4 per quote, `.../audit-parsing/p4.py`). Those are over-generated periods, and every one of them makes `quote_states_a_different_period` silent rather than loud. That is the safe direction (a missed veto, never a wrong publish) and the docstring says a block "states a period when it states a span and a year, wherever in the block it states them" — but the docstring does not say the reader has thereby traded precision for recall. Worth one sentence there.

**Duplication — measured, and smaller than the comment implies.** `_spans_named_in` (`prose.py:175`) unions two grammars, with a docstring saying "neither contains the other". On run13's quotes the second grammar's marginal contribution is **one quote**:
```
$ ./.venv/bin/python .../audit-parsing/p6.py
veto: shipped(both grammars) 3   periods.py only 4   prose.py only 50
quotes where prose.py grammar adds a SPAN periods.py did not name: 1
```
I would **not** delete it: `_periods_with_positions` is needed anyway for the positional read at `prose.py:496`, the union costs ~6 lines, and the narrative forms it reads ("full-year 2002", "FY2002") are a press-release shape this run's table-heavy quotes do not exercise. But the docstring's claim should be qualified — on the corpus the change was measured against, the second grammar moves one row of 549.

---

## F. `cbc7ed6` (6a) — `quality/sentences.py`

**Value.** Measured (326 vetoed before, 136 after; 190 clear). Verified:
```
sentences('EXONDYS 51\n$\n134,688\n$\n126,377\n$\n8,311\n7\n%\n')
 -> ['EXONDYS 51 $ 134,688 $ 126,377 $ 8,311 7 %']
sentences('Revenue rose.\nNet sales were approximately $258.4\nmillion in the quarter.')
 -> ['Revenue rose.', 'Net sales were approximately $258.4', 'million in the quarter.']
```
The second output is the 27 residual cases the commit message names and explicitly declines to fix — the wrapped-prose shape. Documented, not hidden.

**Complexity: 12 lines, one new concept ("a line that states no word continues the row above"), one new regex.** No flag, no parameter, no branch on row provenance — the unit is decided from the document. This is as simple as the value allows.

---

## G. `ca4c79b` (6f) — `check.py` cells, `_states_a_figure`

**Value.** Measured against `seed/cases/shapes_holdout.json` with the predicate stated (40 dropped before, 12 after, 0 newly dropped). Held by `tests/test_a_finding_names_a_cell.py` (6 tests).

**Complexity cost is small and right.** `Cell = tuple[str,str,str]` (`check.py:86`) + `cell_of` (`:89`) + `Finding.cells` replacing `Finding.periods`, with `periods` kept as a derived property (`:103`) for the log line at `:109`. One type alias, one function, one property. No parameter threading.

**This is as simple as the value allows** for the rejection half. **The voting half is broader than the defect required**, and I want to flag the one place I think it over-reaches:

`value_supported_by_quote` (`check.py:231`) now runs over `_states_a_figure(points)`, so a row flagged `label_not_understood` whose number does not appear in its own quote **no longer produces a finding at all**. That check is a self-consistency property of one row — "was this number read from the source" — not a cross-row claim, and excluding the row removes a guard rather than scoping one. The reason it had to be excluded is structural: `Finding.cells` is the only rejection channel, so a per-row finding necessarily rejects the whole cell. The cleaner shape — reject the row, not the cell, for this one check — needs a row identity `Datapoint` does not have, so it is *more* complexity, not less. **Conclusion: the shipped design is the simpler one; the loss is real and is not stated anywhere.** One sentence in `_states_a_figure`'s docstring saying "and so a not-understood row's own quote is no longer checked" would close it. I did not measure how many rows that is.

---

## H. `1aba1c2` (5a/5d/7b) — `derive.py` lineage

**Value.** Measured over run13's 16 derived rows (0 of 16 cited a document printing the input before; 10 of 10 reproducible rows after). Held by `tests/test_a_derivation_says_what_it_subtracted.py` (6 tests).

**Complexity cost — the highest in scope.** New type `DerivedFrom` (`derive.py:89`); a `lineage` **out-parameter** on `complete_quarters_from_totals` (`:117`) and `propagate_sole_formulation` (`:286`) with four `if lineage is not None:` guards; two `id()`-keyed dictionaries, `origin` (`:474`) and `from_point` (`:524`); a nested `observed` (`:476`) and a nested `carried` (`:529`).

**Simpler alternative.** The `lineage` parameter is threaded for **exactly one caller** — `complete_series` is the only production caller (`orchestrator.py:2359` is the only call into this module). Have the two functions **return** `list[DerivedFrom]` and let `complete_series` take `.output`. That deletes an optional parameter from two public signatures, four `is not None` branches, and the mutable-out-parameter idiom. Cost: ~10 direct test call sites in `test_extraction_stack.py` and `test_every_quarter_two_totals_determine.py` gain `[d.output for d in ...]`. Value fully preserved.

**Fragile (by scoping, not by luck).** `origin: dict[int, dict]` is keyed on `id(point)`, which is correct only while every keyed object is alive. It is: `own` and `family_points` stay bound in `complete_series` for the whole function. But `family_points` is built by filtering the temporary list `observed(...)` returns (`:509-513`), so the invariant depends on which locals happen to remain bound. One comment stating the invariant would be enough; I found no way to break it today.

**Rule-1 tension.** `carried()` restates two literal column lists — nine identity keys at `:508-515` and four provenance keys at `:517-527` — that are a snapshot of what `orchestrator._candidate_of` returns. Nothing links them: if `_candidate_of` gains an identity column, it is silently not carried, which is the exact class of defect 5d was. **Do this (cheap version):** a test asserting the two key sets agree. **Do this (structural version):** carry `head` minus the keys the derived dict recomputes — those keys are literally the dict literal two lines below.

**Dead branch.** `carried()`'s `if record is None: return {"_inputs": []}` (`:532`) is unreachable — all three derivation sites append a lineage record, and `complete_series` always passes a list.

---

## I. `11253f9` (6d) — `xbrl.Calculation.roots` / `settles`

**Value.** Measured in the commit over 52 cached linkbases ((True,True) 31, (True,None) 8, (False,False) 6, (None,None) 5; no revenue element moved). I re-ran the direction that matters — does the root rule lose real revenue?

```
$ ./.venv/bin/python .../audit-parsing/p7.py     (52 *_cal.xml in run7 cache)
revenue-named elements settling True: 58
revenue-named elements settling None (unplaced -> goes to model): 27
```
Every one of the 27 is a balance-sheet or cash-flow item (`AccruedProductRevenueRelatedReserveCurrent` under `LiabilitiesAndStockholdersEquity`, `IncreaseDecreaseInDeferredRevenue` under the cash reconciliation). **No revenue element is lost.**

**Latent, verified as not live.** `parse_calculation` only records `roots[element]` for elements that *have* parents (`xbrl.py:498-515`; an element that is its own top returns `frozenset({element})` without memoising). So a filer whose product-revenue element is the top of its own tree would settle `None` instead of `True`. Occurrences across the 52 cached linkbases: **0** (`.../audit-parsing/p8.py`). Latent only.

`_is_income_total` matches by prefix, so `DisposalGroupIncludingDiscontinuedOperationOperatingIncomeLoss` is not recognised — the comment at `:373-379` already says the list is a prefix snapshot and that the stale direction is "unplaced, ask the model". Correctly bounded.

**Complexity is proportionate.** One field, one 7-entry prefix tuple with the rule-1 comment the rule asks for, one recursion. The recursion memoises inside a cycle guard (`:501-514`), which could in principle store a cycle-truncated answer; no cycle exists in the 52 cached linkbases. Leave as is.

---

## J. `33d3b01` (1c) — `evidence.product_aliases` franchise split

**Value.** Verified on invented names:
```
product_aliases('Calderon','calderinol',extra=['Calderon (calderinol)/Calderon XR']) -> [..., 'Calderon XR']
product_aliases('Calderon', extra=['Calderon/NuVessa'])                              -> ['Calderon','Calderon/NuVessa']
```

**Wrong / fragile — the one live-shaped defect I found in this scope.** `_spells` (`evidence.py:73`) tests unanchored substring containment **in both directions**, against the generic name as well as the brand. A generic that is a substring of the partner's brand re-opens 1c:

```
$ ./.venv/bin/python .../audit-parsing/p9.py
product_aliases("NuVessa", "acme-1", extra=["NuVessa/Acme"]) -> ['NuVessa','acme-1','NuVessa/Acme','Acme']
                                                                                          ^^^^^^^^
```
`"acme" in "acme-1"` is True, so the co-packaged partner's bare name is manufactured into this product's alias set — which is precisely the mechanism 1c describes (`read_label` then drops the marked partner name as our own, `combined_with` comes back empty). Also visible: `product_aliases("Calderon", extra=["Cal/Calderon"])` emits the 3-character alias `'Cal'` (pre-existing, not a regression).
**Do this:** require the part to *start with* a held name at a word boundary, rather than containment either way. `Calderon XR` still resolves to `Calderon`; `Acme` no longer resolves to `acme-1`. Loses: parts that abbreviate the product (`Cal`), which are junk aliases anyway. This is a smaller predicate than the current one.

---

## K. Trivial / correct, no action

- `documents.py`: the only change is deleting an unused `import pdfplumber` inside `_parse_pdf` (`830aad2`, ruff). Nothing in `table_caption` / `table_footnotes` / the character budgets changed in this range, so there is nothing in scope to audit there.
- `notes_datasets.py`, `members.py`, `bulk_tagged.py:70`: `typing.Iterable` -> `collections.abc`, `removesuffix`, quoted-forward-ref removal. Correct.
- `adjudicate.py` docstring and `fd153ad`: counts and eval histories removed from docstrings (rule 5). Correct; the replacement sentences describe the code.

## L. One now-false statement produced by the 5b fix

`extract.py:736` still does `skipped.append(f"{label}:{block.period}:{FLAG_NO_SALES}")` **after** the change that stops skipping the row — the figure is now carried out with the flag (`:746`). That string flows through `candidates.py` to the orchestrator's `table_skipped ... reasons=` log line, which will now report a row as skipped that was published flagged. One-line fix: drop the append, or rename the channel.

---

# Ranked: top simplifications (complexity removed x value preserved)

1. **Fold `_PERIOD_PHRASE_RE` into `_NAMED_PHRASE_RE`** — `periods.py:156,162`. Deletes one of two compiled spellings of one grammar plus the six-line comment justifying the split. *Risk: none measured* — 0 of 433 cached documents change date (`.../audit-parsing/p10.py`).
2. **One function for peer names, not two across two modules** — `parsing/tables.py:78` + `quality/candidate_filters.py:159`, composed verbatim at `orchestrator.py:2079` and `:2462` with four args threaded twice and `product_aliases` computed twice. *Risk: low; no test names either function, so the change is invisible to the suite — add one that does.*
3. **Return lineage instead of threading it** — `derive.py:117,286`. Removes an out-parameter from two public signatures and four `is not None` branches; the only production caller is `complete_series`. *Risk: low, ~10 test call sites churn.*
4. **Tighten `_spells` to a word-boundary prefix test** — `evidence.py:73`. Closes the verified re-opening of 1c through the generic name. *Risk: loses abbreviation aliases like `Cal`; measure against run13's stored merged aliases before landing.*
5. **Collapse `NoteReading.months` + `periods` into one `(months, key)` set** — `labels.py:358,362`. Deletes the dead `states_a_scope` (:365) and the `period_span(key, months)` mixed-span hazard in `_dates_part_of_the_period` (:456) in the same edit. *Risk: medium-low; `applies_to` and two tests move with it.*
6. **Make `_includes`'s negation guard symmetric** — `labels.py:415`. Replaces two brittle lookbehinds with the tempered window already used after the claim; the pattern gets shorter. *Risk: none; zero corpus notes change (all 6 negated claims are adjacent today).*
7. **Delete the unreachable `record is None` branch (`derive.py:532`) and the now-false skip note (`extract.py:736`).** *Risk: none.*
8. **Link `carried()`'s nine-key list to `_candidate_of`** — `derive.py:508-515`. Cheapest form is a test asserting the two key sets agree. *Risk: none.*

# Leave exactly as it is

- **`check.py` `Cell` / `cell_of` / `Finding.periods`** (`:86,:89,:103`) — one alias, one function, one derived property; the rejection unit now matches the grouping unit and nothing else moved.
- **`MONTHS_TO_PERIOD_TYPE` + `period_label` consolidation** (`periods.py:364,372`) — net *deletes* decision sites; five readers now share one producer, and `check.py:77` inverts rather than restating.
- **`quality/sentences.py` `_lines`** — 12 lines, one concept, decided from the document rather than from a flag on the row, with the residual 27-case shape documented rather than tuned away.
- **`xbrl.py` `roots` / `settles`** — verified to lose no revenue element across 52 cached linkbases; the prefix list carries exactly the rule-1 comment (what it is a snapshot of, what makes it stale, which direction it fails in).
- **`detect_period_context`'s "named throughout" guard** — three lines reusing a predicate that already existed in the same file, replacing a comment that violated rule 5.
- **`periods._dates_after` multi-date loop** (`:426`) — not built for one filing: 26 headings across 16 distinct corpus notes state two end dates.
- **`prose._spans_named_in`'s two-grammar union** — marginal value on this run is 1 quote of 549, but `_periods_with_positions` is needed anyway at `prose.py:496` and the union is ~6 lines. Qualify the docstring; do not delete.

# Verified vs inferred

Verified by command (all listed above): the 5b/5c/7d note-reading behaviour; the 964-note corpus counts; the clause-cut correctness on all 10 corpus instances; the negation asymmetry and its zero corpus incidence; the fold of the two phrase patterns being a no-op on 433 documents; the 26 multi-date headings; the cross-product's 79->3 effect on the veto and the two grammars' 1-quote marginal difference; `settles` losing no revenue element and no revenue element being its own top; the `Acme`/`acme-1` alias split; `states_a_scope` having no production caller; the duplicated orchestrator peer block; `complete_series` being the sole production caller of the two lineage functions; all in-scope tests green (`./.venv/bin/pytest -q -p no:cacheprovider` over 15 files, 223 passed).

Inferred, not measured: the cost of excluding `label_not_understood` rows from `value_supported_by_quote` (G); the `id()`-keyed-map fragility in `derive.py` (H) — I could not construct a failing case; the re-cache cost of `_includes`'s per-name compilation.
