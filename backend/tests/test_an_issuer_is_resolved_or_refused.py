"""Which company a product is bound to, and what happens when nobody knows.

Two paths answer that question and neither had a behavioural test.

`resolve_cik` reads the SEC's own name index. It compares normalised titles
exactly, so the whole of the answer is in the normalisation: "Calderon
Respiratory and Company" and "CALDERON RESPIRATORY & Co" are one registrant,
written with the two spellings of one conjunction, and read as different words
they are two. And a ticker nobody lists is not an answer - it is a symbol we
were handed - so the name is tried after it rather than instead of it.

`resolve_cik_from_search` asks a model. The model is asked for five things,
and the four that are not the CIK are what say whether the CIK is worth
having: without a confidence, a reply that is a guess binds every filing the
job goes on to fetch, and the surface cannot tell that apart from an issuer
that files nothing.

Both answers are represented here: a name that resolves and a name that must
not, a reply that is taken and replies that are refused for each of the three
reasons they can be.
"""

from __future__ import annotations

import pytest

from app.connectors.llm_search import (
    LLMSearchConnector,
    SearchedIdentity,
    read_searched_identity,
)
from app.connectors.sources import SECConnector, normalize_registrant
from app.storage.filestore import LocalFileStore

# An index shaped like the SEC's: a dict of rows, each with a ticker, a title
# and an integer CIK. Two registrants share the first word of their name, which
# is the case a prefix match gets wrong.
INDEX = {
    "0": {"cik_str": 59478, "ticker": "CLDN", "title": "CALDERON RESPIRATORY & Co"},
    "1": {"cik_str": 1082554, "ticker": "NVSA", "title": "NuVessa Therapeutics, Inc."},
    "2": {"cik_str": 1806837, "ticker": "NVSB", "title": "NuVessa Systems"},
    "3": {"cik_str": 200406, "ticker": "BETA", "title": "Beta Holdings Group"},
}


class _Index:
    def json(self) -> dict:
        return INDEX


def _connector(monkeypatch) -> SECConnector:
    calls: list[str] = []

    async def _get(self, client, url, *, budget_s=None):
        calls.append(url)
        return _Index()

    monkeypatch.setattr(SECConnector, "_get_with_retry", _get)
    connector = SECConnector(LocalFileStore("/tmp"))
    connector.index_fetches = calls  # type: ignore[attr-defined]
    return connector


def test_a_conjunction_is_one_word_however_it_is_spelled():
    assert (
        normalize_registrant("Calderon Respiratory and Company")
        == normalize_registrant("CALDERON RESPIRATORY & Co")
        == "calderon respiratory"
    )
    # And the suffix rule it rides with still holds on its own.
    assert normalize_registrant("Calderon Respiratory, Inc.") == "calderon respiratory"
    # A conjunction between two names, not between a name and its suffix, goes
    # the same way: the words that are left still name the registrant.
    assert normalize_registrant("Calderon & NuVessa") == "calderon nuvessa"


async def test_a_name_alone_resolves(monkeypatch):
    connector = _connector(monkeypatch)
    assert await connector.resolve_cik(company_name="Calderon Respiratory and Company") == (
        "0000059478"
    )
    # The same registrant asked for the way the index spells it.
    assert await connector.resolve_cik(company_name="Calderon Respiratory & Co") == (
        "0000059478"
    )


async def test_an_ambiguous_name_resolves_to_nothing(monkeypatch):
    """Two registrants whose names begin the same way. The honest answer to an
    ambiguous question is no answer, and a prefix match would give one."""
    connector = _connector(monkeypatch)
    assert await connector.resolve_cik(company_name="NuVessa") is None
    # Named in full, each one resolves.
    assert await connector.resolve_cik(company_name="NuVessa Therapeutics") == "0001082554"
    assert await connector.resolve_cik(company_name="NuVessa Systems") == "0001806837"


async def test_a_ticker_nobody_lists_falls_through_to_the_name(monkeypatch):
    connector = _connector(monkeypatch)
    assert await connector.resolve_cik("CLDN", "NuVessa Systems") == "0000059478", (
        "a ticker that is listed is exact and wins"
    )
    assert await connector.resolve_cik("NOSUCH", "NuVessa Systems") == "0001806837", (
        "a ticker the index does not carry is not an answer; the name is tried"
    )
    assert await connector.resolve_cik("NOSUCH", "Nobody At All") is None


async def test_neither_a_ticker_nor_a_name_costs_no_request(monkeypatch):
    connector = _connector(monkeypatch)
    assert await connector.resolve_cik() is None
    assert connector.index_fetches == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "payload, refused",
    [
        ({"cik": "1234567890", "confidence": 0.9}, None),
        ({"cik": "1234567890", "confidence": 0.2}, "cik_search_refused_low_confidence"),
        ({"cik": "1234567890"}, "cik_search_refused_no_confidence"),
        ({"cik": "", "confidence": 0.9}, "cik_search_returned_no_cik"),
        ({"cik": "not a cik", "confidence": 0.9}, "cik_search_returned_no_cik"),
    ],
)
def test_a_reply_is_taken_or_refused_and_the_reason_is_named(payload, refused):
    resolution = read_searched_identity(payload, floor=0.6)
    assert resolution.refused == refused
    assert resolution.accepted is (refused is None)


def test_the_four_fields_the_prompt_asks_for_survive_the_read():
    payload = {
        "cik": "59478",
        "company_name": "Calderon Respiratory & Co",
        "confidence": "0.81",
        "source_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
        "notes": "the registrant that reports this product",
    }
    resolution = read_searched_identity(payload, floor=0.6)
    assert resolution.cik == "0000059478"
    assert resolution.company_name == "Calderon Respiratory & Co"
    assert resolution.confidence == pytest.approx(0.81)
    assert resolution.source_url.startswith("https://www.sec.gov/")
    assert resolution.notes
    assert resolution.accepted
    # A refused reply keeps them too - they are what says why it was refused.
    low = read_searched_identity({**payload, "confidence": 0.2}, floor=0.6)
    assert low.company_name == resolution.company_name and low.cik == resolution.cik
    assert not low.accepted


class _Model:
    def __init__(self, reply: dict) -> None:
        self.reply = reply

    async def resolve_cik_via_search(self, **_: object) -> dict:
        return self.reply


def _searcher(monkeypatch, reply: dict, floor: float = 0.6) -> LLMSearchConnector:
    connector = LLMSearchConnector(LocalFileStore("/tmp"), llm=_Model(reply))
    connector.settings = connector.settings.model_copy(
        update={"enable_llm_search": True, "llm_cik_min_confidence": floor}
    )
    return connector


async def test_a_guess_is_not_bound_to_the_product_and_the_job_is_told(monkeypatch):
    connector = _searcher(
        monkeypatch,
        {"cik": "1070494", "company_name": "Beta Holdings Group", "confidence": 0.2},
    )
    flags: list[str] = []
    cik = await connector.resolve_cik_from_search(
        product="Calderon", manufacturer=None, ticker=None, aliases=[], quality_flags=flags,
    )
    assert cik is None
    assert flags == ["cik_search_refused_low_confidence"]
    # And what was refused is still in hand, which is what makes the refusal
    # checkable rather than a silence.
    assert connector.last_resolution.company_name == "Beta Holdings Group"
    assert connector.last_resolution.cik == "0001070494"


async def test_a_confident_reply_is_taken_and_flagged_as_the_model_s(monkeypatch):
    connector = _searcher(
        monkeypatch,
        {"cik": "1070494", "company_name": "Beta Holdings Group", "confidence": 0.95},
    )
    flags: list[str] = []
    cik = await connector.resolve_cik_from_search(
        product="Calderon", manufacturer=None, ticker=None, aliases=[], quality_flags=flags,
    )
    assert cik == "0001070494"
    assert flags == ["cik_from_llm_search"]


async def test_a_search_that_was_never_asked_flags_nothing(monkeypatch):
    connector = _searcher(monkeypatch, {"cik": "1070494", "confidence": 0.95})
    connector.settings = connector.settings.model_copy(update={"enable_llm_search": False})
    flags: list[str] = []
    assert await connector.resolve_cik_from_search(
        product="Calderon", manufacturer=None, ticker=None, aliases=[], quality_flags=flags,
    ) is None
    assert flags == []
    assert connector.last_resolution == SearchedIdentity.nothing()
