"""Finding the XBRL instance in a filing whose naming predates inline XBRL.

The selection used to be `name.endswith("_htm.xml")`, which is the inline-era
spelling only. Every filing before a filer adopted inline XBRL was logged as
"no instance" and skipped, and the comment beside the filter explained that as
a property of the SEC's data rather than of the filter.

The listings below are the real `index.json` contents of four filings, so the
test fails if the assumption about how filings are named is wrong rather than
only if the code is.
"""

from __future__ import annotations

from app.connectors.sources import _instance_document

# United Therapeutics 10-Q, filed 2016-10-27 (0001104659-16-152292). Its
# instance tags products on the ProductOrService axis.
UTHR_2016 = [
    "FilingSummary.xml", "uthr-20160930.xsd", "uthr-20160930.xml",
    "uthr-20160930_cal.xml", "uthr-20160930_def.xml",
    "uthr-20160930_lab.xml", "uthr-20160930_pre.xml",
    "a16-18889_110q.htm",
]

# Gilead 10-Q, filed 2013-10-31 (0000882095-13-000046): twelve products tagged.
GILD_2013 = [
    "FilingSummary.xml", "gild-20130930.xsd", "gild-20130930.xml",
    "gild-20130930_cal.xml", "gild-20130930_def.xml",
    "gild-20130930_lab.xml", "gild-20130930_pre.xml",
]

# United Therapeutics 10-Q, filed 2025-10-29 (0001082554-25-000034). Inline:
# the schema stem is the same, and the instance carries an "_htm" suffix.
UTHR_2025 = [
    "FilingSummary.xml", "uthr-20250930.xsd", "uthr-20250930_htm.xml",
    "uthr-20250930_cal.xml", "uthr-20250930_def.xml",
    "uthr-20250930_lab.xml", "uthr-20250930_pre.xml",
]

# The 2010 filing also renders each statement as R1.xml, R2.xml, and so on.
UTHR_2010 = [
    "defnref.xml", "FilingSummary.xml", "R1.xml", "R2.xml", "R10.xml",
    "uthr-20100930.xsd", "uthr-20100930.xml",
    "uthr-20100930_cal.xml", "uthr-20100930_def.xml",
    "uthr-20100930_lab.xml", "uthr-20100930_pre.xml",
]


def test_the_pre_inline_instance_is_found():
    """The defect, stated twice: both filings were skipped as having none."""
    assert _instance_document(UTHR_2016) == "uthr-20160930.xml"
    assert _instance_document(GILD_2013) == "gild-20130930.xml"


def test_the_inline_extracted_instance_is_still_preferred():
    assert _instance_document(UTHR_2025) == "uthr-20250930_htm.xml"


def test_a_linkbase_is_never_mistaken_for_the_instance():
    """`_cal`, `_def`, `_lab` and `_pre` are .xml files in the same directory.

    They describe the facts rather than stating them, so a rule that took any
    .xml sharing the filer's prefix would parse a calculation linkbase and find
    no revenue in it - the same silence as before, differently caused.
    """
    for listing in (UTHR_2016, GILD_2013, UTHR_2025, UTHR_2010):
        found = _instance_document(listing)
        assert found is not None
        assert not found.endswith(("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml"))


def test_the_rendered_statements_are_not_the_instance():
    """R1.xml..R20.xml are the viewer's per-statement renderings."""
    assert _instance_document(UTHR_2010) == "uthr-20100930.xml"


def test_a_filing_with_no_xbrl_at_all_yields_nothing():
    assert _instance_document(["a16-18889_18k.htm", "ex-99.htm"]) is None
    assert _instance_document([]) is None


def test_the_old_spelling_still_works_without_a_schema():
    """The schema is the anchor, not a requirement. If one is ever missing,
    finding the instance by its inline-era name beats finding nothing."""
    assert _instance_document(["gild-20250930_htm.xml"]) == "gild-20250930_htm.xml"


async def test_a_filing_is_taken_for_its_xbrl_and_not_for_its_form(monkeypatch):
    """Which filings carry facts is not a property of the form.

    This asked only 10-Q and 10-K, which is a domestic filer's shape. A foreign
    private issuer reports its quarter on a 6-K, and one that tags its product
    schedule inline was read as having filed nothing. EDGAR states per filing
    whether it has XBRL, which answers the same question without the guess.
    """
    from app.connectors.sources import SECConnector
    from app.storage.filestore import LocalFileStore

    connector = SECConnector(LocalFileStore("/tmp"))
    listed: list[str] = []

    async def _documents(self, client, cik_int, acc_nodash):
        listed.append(acc_nodash)
        return ["acme-20260630.xsd", "acme-20260630x6k_htm.xml",
                "acme-20260630_lab.xml", "acme-20260630x6k.htm"]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return b"<xbrl/>", False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_list_filing_documents", _documents)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    recent = {
        "form": ["6-K", "6-K", "4"],
        "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
        "filingDate": ["2026-07-27", "2026-07-20", "2026-07-19"],
        "isXBRL": [1, 0, 0],
    }
    sources = await connector._retrieve_xbrl_instances(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_filings=5, since=None, until=None,
    )

    assert [s.accession_number for s in sources] == ["0001-26-000001"], (
        "the 6-K EDGAR flags as tagged is the one to take, and its form is not why"
    )
    assert listed == ["000126000001"], (
        "a filing the index says carries no XBRL costs no directory listing"
    )
    assert sources[0].url.endswith("acme-20260630x6k_htm.xml")
