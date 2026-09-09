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

# United Therapeutics 10-Q, filed 2016-10-27 (0001104659-16-152292).
# Its instance carries 100 facts on srt:ProductOrServiceAxis, five products.
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
