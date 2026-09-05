# Sourcing scripts

The code that produced the 2005–2018 backfill in `seed/gold/`. It is here so
the provenance of those rows is inspectable and re-runnable, not because the
application depends on it — nothing in `backend/` imports any of it, and the
gold builder does not either.

Each quarter is read from the filing that reports it **as current**, never from
a prior-year comparative column, and the unit is taken from what the document
declares beside its table rather than inferred from the filing date. Those two
rules are what the scripts exist to enforce.

    SEC_CONTACT='pharma-analog-uptake-workbench you@example.com'   # SEC asks for one
    SOURCING_WORKDIR=/tmp/gold-sourcing                            # cache + intermediates

## Gilead — SEC 8-K earnings exhibits

    python scripts/sourcing/edgar_index_filings.py 882095 $SOURCING_WORKDIR/edgar/gilead_8k.json
    python scripts/sourcing/edgar_find_earnings.py 882095 \
        $SOURCING_WORKDIR/edgar/gilead_8k.json 2004-01-01 2018-12-31 \
        $SOURCING_WORKDIR/edgar/gilead_earnings.json
    python scripts/sourcing/edgar_extract_gilead.py
    python scripts/sourcing/edgar_write_manifests.py

`edgar_find_earnings.py` caches every document it fetches (about 100 MB), so
re-running the extractor costs nothing and never refetches.

## J&J — quarterly Sales of Key Products/Franchises PDFs

    python scripts/sourcing/jnj_fetch.py
    python scripts/sourcing/jnj_extract_jnj.py
    python scripts/sourcing/jnj_write_manifests.py

`jnj_extract_jnj.py` rebuilds each line from pdfplumber word boxes rather than
the PDF's own text layer: some of these schedules split a number in two ("1
,613" for 1,613), which a naive read turns into the value 1.

## Both writers are additive

Neither `*_write_manifests.py` overwrites a row that is already in a manifest —
existing rows were verified against the issuer already, and a gold row that
changes silently is the thing this dataset most has to avoid. They add missing
periods, sort, and report any gap in the resulting series.

## What checking these rows rests on

Not on this code being right. Every row carries the URL and the verbatim line
it came from, so any figure can be checked by hand. Across the dataset, 208
product-years are reconciled against the full-year figure the issuer states in
a different column of the same table — see `seed/gold/README.md`.
