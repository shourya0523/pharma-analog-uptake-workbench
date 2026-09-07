# Structured-first extraction

A plan written after measuring, not before. The pipeline reads 998 of gold's
1,415 quarters end to end; this is about the 417 it does not, and about the 998
it gets right by inference where the filer has already stated the answer.

## How this problem is solved elsewhere

**The commercial vendors do not do it unattended.** Canalyst builds its models
with human analysts. Daloopa reports >99% accuracy from AI extraction *with
human QA*, and attaches a source hyperlink to every datapoint so a number can be
opened in its filing. Nobody in this market claims high-accuracy unattended
extraction. The realistic architecture is high precision, explicit refusal, and
a review queue — which is the shape this pipeline already has, so the goal is
not "automate the last mile" but "refuse less by needing to infer less".

**Structured-first is the standard order.** Read what the filer has tagged; parse
documents only for what is untagged. Under ASC 606 companies disaggregate
revenue in a tagged note, and the SEC's own XBRL guide and XBRL US's revenue
guidance describe `srt:ProductOrServiceAxis` as where product-level revenue
lives. Tools in this space (edgartools, sec-api, Arelle) all read XBRL first.

**Table structure recognition is for images.** The published state of the art —
Table Transformer trained on PubTables-1M, models trained on FinTabNet —
recovers cell structure from a *picture* of a table. Our filings carry their
text, so that is not our problem. For born-digital documents the classical
whitespace-projection method (Nurminen 2013, as implemented by Camelot's
`stream` and pdfplumber's `text` strategy) is the right tool, and is what the
PDF reader here already uses.

## What is true of our corpus, measured

Product-level revenue is XBRL-tagged for every issuer in gold, starting in 2019:

| | 2018 | 2019 | 2020 | 2021 |
|---|---|---|---|---|
| Gilead | 0 | 368 | 384 | 510 |
| Johnson & Johnson | 0 | 493 | 481 | 488 |
| United Therapeutics | 0 | 24 | 24 | 20 |

(product-axis contexts in that year's 10-Q; zero before 2019 for all three)

And it agrees with gold exactly. Gilead's Q3 2024 10-Q, worldwide facts, against
the four gold rows for that quarter:

| product | gold | XBRL | member |
|---|---|---|---|
| Biktarvy | 3472 | 3,472 | `HIVProductsBiktarvyMember` |
| Descovy | 586 | 586 | `HIVProductsDescovyMember` |
| Genvoya | 449 | 449 | `HIVProductsGenvoyaMember` |
| Odefsey | 326 | 326 | `HIVProductsOdefseyMember` |

Every hard problem solved by inference this week is *stated* there:

| inferred today | declared in XBRL |
|---|---|
| which column holds which period | the context's `startDate`/`endDate` |
| the unit, from a caption or header | the fact's `decimals`/`scale` |
| whether a row is the product | the axis member is an identity |
| regional line vs worldwide total | geography is a separate axis; its absence *is* worldwide |
| a heading split across two rows | nothing to read |

One 10-Q also carries the prior-year comparative as its own dated context, so
the class of error that motivated the column-geometry work — dating a
comparative column by the document's period — cannot occur.

**But the documents we read are the wrong ones.** The connector fetches 8-K
EX-99 earnings exhibits, which are not XBRL-tagged. The tagged documents are the
10-Q and 10-K, which it never fetches (`if form != "8-K": continue`).

## What that is worth, and what it is not

Of 1,415 gold rows, 637 fall in the tagged era (2019 onward) and 778 before it.
Of the 417 the pipeline misses, **153 are in the tagged era** and 264 are not.

So XBRL is not mostly a coverage fix — it is a *correctness and simplicity* fix
for 637 rows, and a coverage fix for at most 153. The 264 pre-2019 misses need
the document reader regardless, and 92 of them are 2000s quarters where EDGAR
carries `.txt` submissions rather than exhibit documents.

## The order of work

**1. Fetch 10-Q and 10-K at all.** A prerequisite for everything below, and
independently useful: 41 missed rows cite a 10-Q or 10-K directly.

**2. An XBRL reader, tried before the table reader, for filings that have one.**
Read product-axis revenue facts, map the member to a product, take the fact
whose context carries no geographic axis as worldwide. Falls through to the
table reader when the filing is untagged or the product has no member.

*Open question to settle first:* provenance. Every published datapoint currently
carries a quote verbatim in its document, and an XBRL fact has no quote. The
citation should become the fact itself — element, context id, the dates, the
value — which is a stronger claim than a quoted line, but it is a different
shape and `eval_provenance.py` must learn it before this ships.

**3. Give each table its caption.** For every table that holds the product's row
and declares no unit, the unit is stated in the 600 characters immediately above
it — 21 of 21 sampled. The reader is handed `doc.full_text[:4000]` instead, and
Gilead prints its product sales summary on page 9. This is the largest single
lever on the pre-2019 rows that XBRL cannot reach.

**4. The derivations gold performs and the pipeline does not** (95 rows): Q4 =
FY − 9M, retrospective restatement tables, an acquisition bridge, and the
identity split of Tyvaso into nebulized and DPI (50 rows).

**5. Decline, for now:** an investor-relations connector (the data are on EDGAR;
J&J went 0 → 84.7% without one), image-based table recognition (our documents
carry text), and 2000s `.txt` submissions (92 rows, a different parser).

## How each step will be judged

`scripts/eval_coverage.py` before and after, per issuer and per era, plus
`eval_provenance.py` and the two held-out gates. A step that raises coverage and
adds wrong values has not earned its place — that has happened twice this week,
and both times the wrong-value count was the only thing that showed it.
