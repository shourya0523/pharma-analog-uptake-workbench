# Dating a figure in an SEC filing

Research note. The question is narrow and it is the one blocking the extraction
pipeline on every issuer but one: given a number in a filing's table, which
period does it belong to?

## The failure, stated precisely

`detect_period_context` resolves **one** period for a whole document and stamps
it on every value read from that document. A filing states several periods at
once - the quarter, the year to date, and both prior-year comparatives, printed
side by side - so a single document-level answer is not merely imprecise, it is
ill-posed.

It is also observed. In a run of the pipeline over the 422 backfilled gold
quarters, Harvoni 2015Q1 came back as **3,016** against gold's 3,579. That
figure is real: it is the U.S. prior-year column of the *Q1 2016* exhibit. The
model read the right cell of the wrong column, and nothing downstream could
tell, because the document's period is a single value applied to everything.

## What the literature says

The layout that breaks us is documented behaviour, not an oddity of one issuer.
The Stanford EDGAR Filings Dataset (arXiv 2606.18192) describes filing agents
"exploding a single semantic header into distinct table rows, each with its own
cell-level attributes", which "hard-codes vertical layout for browsers, but
disconnects semantically connected elements", and warns that "flattening EDGAR
tables can create ambiguity by detaching values from labels". That is exactly

    Three Months Ended | June 30, | Six Months Ended | June 30, | 2007 | 2006

read as flat text. Table-structure research treats the merged cell as a
first-class object for the same reason: PubTables-1M defines *spanning cell*
alongside row, column and header, because a header's meaning is the range of
columns it covers.

Two routes follow from that, and only one applies to our sources.

**Inline XBRL.** Where a document is tagged, every fact carries an explicit
context with period start and end - no inference at all. This is the right
answer for 10-K and 10-Q primary documents, and `edgartools` and `sec-api`
both expose it. It does not help here: of the SEC documents gold cites, **13
are inline-XBRL tagged and 141 are not**, because the product-level revenue
tables live in 8-K earnings exhibits, which are not tagged.

**Column geometry.** The heading's `colspan` states which columns it governs.
This is present in the markup we already download, for every issuer, and it is
what a browser uses to render the table a human reads correctly.

## The proof

Gilead's Q1 2016 PRODUCT SALES SUMMARY, as filed:

    [·  colspan=9]
    [·][·][Three Months Ended  colspan=7]
    [·][·][March 31,           colspan=7]
    [·][·][2016 colspan=3][·][2015 colspan=3]

109 of that table's 374 cells carry a `colspan`. Expanding the spans into a
grid so each header cell occupies every column it covers, and reading a value
column's period off the header cells above it, yields:

    columns 2,3,4 -> 2016Q1        columns 6,7,8 -> 2015Q1

derived from the markup alone, with nothing in the code that knows what Gilead
is. `scripts/research/column_periods_prototype.py` is that reading, in about
sixty lines.

The current flattener drops this. It emits `[cell.get_text() for cell in
tr.find_all(["td","th"])]`, so the heading row becomes three cells while the
value row has nine, and column indices stop being comparable between rows -
which is why the heading cannot be matched to anything and the period has to be
guessed from prose instead.

## What the measurements do and do not show

| | figure dated correctly |
|---|---|
| document-level context, on gold's rows | 278/278 |
| column geometry, on gold's rows | 268/278 |

Read carelessly this says the prose method is better. It is not a fair contest:
**gold only ever records the current-quarter column**, so one period per
document is sufficient for every gold row by construction. Gold cannot
distinguish the two approaches on the failure that matters, because it contains
no comparative-column rows to get wrong.

The discriminating test needs no gold at all. In the same Gilead table:

| row | value | column geometry | document-level |
|---|---|---|---|
| Harvoni - U.S. | 1,407 | 2016Q1 | 2016Q1 |
| Harvoni - U.S. | 3,016 | **2015Q1** | 2016Q1 (wrong) |
| Truvada - U.S. | 409 | **2015Q1** | 2016Q1 (wrong) |

Every prior-year column in every filing is a value the current design will date
wrongly if anything reads it - and the LLM pass does read them. This is a
silent error class: the number is real, the label is wrong, and no downstream
check fires.

## Recommendation

1. Preserve span geometry when a document is parsed. Expand `colspan`/`rowspan`
   into a grid rather than emitting ragged rows. This is a change to
   `app/parsing/documents.py` and it benefits every consumer, not just dating.
2. Derive the period **per column** from the header cells above it, and attach
   it to the value read from that column. `build_fingerprint` already owns
   per-table period blocks; this gives it a reliable source instead of prose.
3. Keep `detect_period_context` as a fallback for documents with no usable
   table geometry, not as the primary answer.
4. Use inline XBRL where a document has it. It is authoritative and needs no
   geometry, but it covers under a tenth of the sources here.

## What this does not solve

PDF schedules have no `colspan`; the equivalent signal is the horizontal extent
of a heading's text box against the columns beneath it, which is available from
`pdfplumber` word boxes but is a separate piece of work. Of the held-out
issuers, the documents where the prototype finds no quarterly column at all are
those whose press releases tabulate only year-to-date figures - for those, no
method can report a quarter the document does not print.
