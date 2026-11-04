"""Brand-form identity from an FDA approval history given in memory."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.retrieval.identity import Application, aliases, first_found, split_brand

HISTORY = {
    "Calderon": (Application("NDA000001", "CALDERON", "SOLUTION", "INHALATION", dt.date(2009, 7, 30)),
                 Application("NDA000002", "CALDERON DPI", "POWDER", "INHALATION", dt.date(2022, 5, 23))),
}


def lookup(brand):
    return HISTORY.get(brand, ())


def nebulized(descriptor, form, route):
    return descriptor.lower() == "nebulized" and form == "SOLUTION"


def test_split_brand_finds_the_brand_fda_lists():
    assert split_brand("Nebulized Calderon", lookup) == ("Calderon", "Nebulized")
    assert split_brand("Calderon", lookup) == ("Calderon", "")
    assert split_brand("NuVessa", lookup) is None


def test_the_bare_brand_is_an_alias_only_while_it_had_one_form():
    before = aliases("Nebulized Calderon", dt.date(2013, 6, 30), lookup=lookup, describes=nebulized)
    assert [a["name"] for a in before] == ["Calderon"] and "NDA000001" in before[0]["evidence"]
    assert aliases("Nebulized Calderon", dt.date(2023, 6, 30), lookup=lookup, describes=nebulized) == []
    assert aliases("Dry Powder Calderon", dt.date(2013, 6, 30), lookup=lookup, describes=nebulized) == []
    assert aliases("Calderon", dt.date(2013, 6, 30), lookup=lookup, describes=nebulized) == []


def test_first_found_retries_under_the_alias_and_says_so():
    def read(product, period):
        return SimpleNamespace(status="found" if product == "Calderon" else "no_member", product=product)

    answer = first_found(read, "Nebulized Calderon", "Q2 2013", dt.date(2013, 6, 30), lookup=lookup, describes=nebulized)
    assert answer.status == "found" and answer.product == "Calderon" and answer.identity["name"] == "Calderon"
