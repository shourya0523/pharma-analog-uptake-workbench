"""An amendment is the filing it amends, and retrieval reads it as one.

Three gates decide what a job reads: the primary pass's allowed set, the order
it reads that set in, and the earnings pass's form test. A form string carries
its amendment suffix, so a gate that compares the string answers "no" for
`10-K/A`, `10-Q/A` and `8-K/A` - the filings that carry a restatement, and the
second reading of a quarter a figure can be corroborated against.

Each gate asks for the family instead: `form_family("10-K/A") == "10-K"`. The
read order is derived the same way, from what the form reports rather than
from how it is spelled, so an amendment sits beside the form it amends rather
than behind everything.
"""

from __future__ import annotations

from datetime import date

from app.connectors.sources import (
    PERIODIC_FORM_FAMILIES,
    SECConnector,
    form_family,
    reading_order,
    reports_a_period,
    states_item,
)
from app.storage.filestore import LocalFileStore

# The two families the primary pass does not read by default, together: the
# ones it reads only on request, and the one that is not the registrant's own
# report. Taken from the connector rather than restated here.
NOT_READ_BY_DEFAULT = frozenset(SECConnector.SECONDARY | SECConnector.NOT_THE_REGISTRANT)


class _Payload:
    def json(self) -> dict:
        return {}


def _connector(monkeypatch, **settings) -> SECConnector:
    """A connector whose submissions fetch is stubbed, and whose settings are
    stated rather than inherited from the environment the test runs in."""

    async def _get(self, client, url, *, budget_s=None):
        return _Payload()

    monkeypatch.setattr(SECConnector, "_get_with_retry", _get)
    connector = SECConnector(LocalFileStore("/tmp"))
    connector.settings = connector.settings.model_copy(update=settings)
    return connector


def test_the_allowed_families_are_derived_from_what_a_form_reports():
    """PRIMARY is what the module already calls periodic, less the two
    families it reads only on request and the one that is not the
    registrant's own report."""
    assert SECConnector.PRIMARY == frozenset(
        PERIODIC_FORM_FAMILIES - {form_family(f) for f in NOT_READ_BY_DEFAULT}
    )
    assert all(reports_a_period(family) for family in SECConnector.PRIMARY)
    # And every allowed entry is a family, so it can be compared with one.
    for family in SECConnector.PRIMARY | SECConnector.SECONDARY:
        assert form_family(family) == family


def test_the_earnings_form_is_spelled_as_its_own_family():
    """Both answers: the constant is already a family, an amendment is not.

    The earnings gate compares a form's family against `EARNINGS_FORM`
    directly, which is only right while the constant is spelled as a family.
    """
    assert form_family(SECConnector.EARNINGS_FORM) == SECConnector.EARNINGS_FORM
    assert form_family(f"{SECConnector.EARNINGS_FORM}/A") == SECConnector.EARNINGS_FORM
    assert f"{SECConnector.EARNINGS_FORM}/A" != SECConnector.EARNINGS_FORM


def test_an_amendment_sorts_with_the_form_it_amends():
    """The read order is what the form reports, not how it is spelled.

    Keyed on the string, `10-K/A` is absent from the order and sorts behind
    every unamended filing, so a budget that truncates the queue never
    reaches it.
    """
    assert reading_order("10-K/A") == reading_order("10-K") == 0
    assert reading_order("10-Q/A") == reading_order("10-Q") == 1
    assert reading_order("8-K/A") == reading_order("8-K") == 2
    assert reading_order("10-K") < reading_order("10-Q") < reading_order("8-K")


async def test_the_primary_pass_fetches_an_amendment_and_not_a_benefit_plan(monkeypatch):
    """The gate in `retrieve`, exercised end to end.

    A filer's restatement and its transition-period annual report are read;
    the benefit plan's own annual report is not, though it states a period,
    because the period is the plan's and the document names no product.
    """
    connector = _connector(monkeypatch, sec_include_8k=False, sec_max_filings=10)
    fetched: list[str] = []

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        fetched.append(doc)
        return b"<html></html>", False, f"key/{doc}"

    forms = ["10-K/A", "10-Q/A", "10-K405", "11-K", "8-K"]
    docs = ["amended_annual.htm", "amended_quarter.htm", "old_annual.htm",
            "benefit_plan.htm", "cover.htm"]

    async def _covering(self, client, payload, cik, _since, _until):
        return {
            "form": forms,
            "accessionNumber": [f"0000000001-24-00000{n}" for n in range(len(forms))],
            "filingDate": ["2024-05-01"] * len(forms),
            "primaryDocument": docs,
            "items": [""] * len(forms),
        }

    monkeypatch.setattr(SECConnector, "_filings_covering", _covering)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    await connector.retrieve(
        run_id="r", job_id="j", cik="0000000001", ticker=None, company_name=None,
        include_primary=True, include_earnings=False, include_xbrl=False,
        earnings_since=date(2024, 1, 1), earnings_until=date(2024, 12, 31),
    )
    assert "amended_annual.htm" in fetched, "a restatement is the annual report restated"
    assert "amended_quarter.htm" in fetched
    assert "old_annual.htm" in fetched, "a form family the written-down set predated"
    assert "benefit_plan.htm" not in fetched
    assert "cover.htm" not in fetched, "the 8-K family is off unless asked for"
    # And the amendment is not read last: an annual report is read first
    # whether or not it is an amendment.
    assert fetched[0] == "amended_annual.htm"


async def test_asking_for_the_8k_family_asks_for_its_amendments_too(monkeypatch):
    """The secondary half of the same gate. Turning the 8-K family on admits
    the amendment with it, and it is read after the periodic reports."""
    connector = _connector(monkeypatch, sec_include_8k=True, sec_max_filings=10)
    fetched: list[str] = []

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        fetched.append(doc)
        return b"<html></html>", False, f"key/{doc}"

    forms = ["8-K/A", "8-K", "10-Q"]
    docs = ["amended_cover.htm", "cover.htm", "quarter.htm"]

    async def _covering(self, client, payload, cik, _since, _until):
        return {
            "form": forms,
            "accessionNumber": [f"0000000001-24-00000{n}" for n in range(len(forms))],
            "filingDate": ["2024-05-01"] * len(forms),
            "primaryDocument": docs,
            "items": [""] * len(forms),
        }

    monkeypatch.setattr(SECConnector, "_filings_covering", _covering)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    await connector.retrieve(
        run_id="r", job_id="j", cik="0000000001", ticker=None, company_name=None,
        include_primary=True, include_earnings=False, include_xbrl=False,
        earnings_since=date(2024, 1, 1), earnings_until=date(2024, 12, 31),
    )
    assert fetched == ["quarter.htm", "amended_cover.htm", "cover.htm"]


async def test_an_item_2_02_amendment_is_an_earnings_filing(monkeypatch):
    """The earnings gate reads the family, so an amendment furnishing the same
    results is fetched, and the source says which form it came from."""
    connector = _connector(monkeypatch, sec_include_8k=False)

    async def _declared(self, client, cik_int, accession):
        return [("EX-99.1", f"{accession}-release.htm")]

    async def _fetch(self, client, *, url, accession, doc, run_id, job_id, source_id):
        return b"<html></html>", False, f"key/{doc}"

    monkeypatch.setattr(SECConnector, "_declared_documents", _declared)
    monkeypatch.setattr(SECConnector, "_fetch_document", _fetch)

    forms = ["8-K", "8-K/A", "8-K", "10-Q"]
    recent = {
        "form": forms,
        "accessionNumber": [f"0000000001-25-00000{n}" for n in range(len(forms))],
        "filingDate": ["2025-05-08", "2025-05-09", "2025-05-10", "2025-05-12"],
        # The third 8-K is furnished under a different item and the 10-Q
        # states none, so neither is an earnings filing.
        "items": ["2.02,9.01", "2.02,9.01", "5.02", ""],
    }
    sources = await connector._retrieve_earnings_exhibits(
        None, run_id="r", job_id="j", cik="0000000001", recent=recent,
        max_exhibits=6, since=date(2025, 1, 1), until=date(2025, 12, 31),
    )
    assert sorted(s.filing_type for s in sources) == ["8-K", "8-K/A"]


def test_an_item_is_a_whole_entry_in_the_list_not_a_substring():
    """EDGAR writes the items as comma-separated codes, and a code read as a
    substring is also found inside a longer one."""
    assert states_item("2.02,9.01", SECConnector.EARNINGS_ITEM)
    assert states_item(" 9.01 , 2.02 ", SECConnector.EARNINGS_ITEM)
    assert not states_item("5.02,9.01", SECConnector.EARNINGS_ITEM)
    assert not states_item("", SECConnector.EARNINGS_ITEM)
    assert not states_item(None, SECConnector.EARNINGS_ITEM)
    assert not states_item("12.02", SECConnector.EARNINGS_ITEM)
