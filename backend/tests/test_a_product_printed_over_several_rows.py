"""Reading a schedule that prints a product as a heading over geography rows.

Johnson & Johnson tags no product on any axis before 2019 - its instances of
that era carry plenty of facts and none of them sits on the product axis - so
the only place those quarters exist is the printed
"Sales of Key Products/Franchises" schedule attached to each quarterly 8-K.

That schedule prints a product as a heading with no figures on it, then the
slices beneath:

    REMICADE
    US               1,211
    US Exports (3)     233
    Intl               335
    WW               1,779

The heading logic already existed. Two things stopped it working:

* "US Exports" reduced to "exports", which is no product's name. The peer guard
  read Remicade's own export line as a competitor's; the components then no
  longer added to the total, the total could not be identified by its
  arithmetic, and the whole product was refused as "several lines and no total".
  Few brands carry an export line, and the one that does is refused entirely.
* A slash was treated as joining two products. It is what a filer writes
  between the names of one: CONCERTA/METHYLPHENIDATE is a brand and its
  generic, PROCRIT/EPREX one drug in two markets, INVOKANA/INVOKAMET a brand
  and its own combination. Four products read zero of their eighteen quarters
  because the only line the issuer ever published for them was refused.

Rows and figures below are from the exhibit to Johnson & Johnson's 8-K of
19 April 2016 (0000200406-16-000074).
"""

from __future__ import annotations

import pytest

from app.extraction.candidates import extract_revenue_candidates
from app.quality.candidate_filters import names_a_competing_product

HEADER = ["", "", "", "FIRST QUARTER"]
COLUMNS = ["", "", "", "2016", "", "2015"]


def _schedule():
    """The shape, with two products and the slices each is printed over."""
    return [
        ["Johnson & Johnson"],
        ["Segment Sales"],
        ["(Dollars in Millions)"],
        HEADER,
        COLUMNS,
        ["REMICADE", "", "", "", "", ""],
        ["US", "", "", "1,211", "", "1,055"],
        ["US Exports (3)", "", "", "233", "", "181"],
        ["Intl", "", "", "335", "", "364"],
        ["WW", "", "", "1,779", "", "1,600"],
        ["STELARA", "", "", "", "", ""],
        ["US", "", "", "511", "", "364"],
        ["Intl", "", "", "224", "", "185"],
        ["WW", "", "", "735", "", "549"],
    ]


def _read(product, tables=None):
    cands, _findings, skipped = extract_revenue_candidates(
        tables or [_schedule()], product=product, context="Johnson & Johnson"
    )
    return {str(c["period"]): c["value_normalized_usd_millions"] for c in cands}, skipped


def test_the_worldwide_line_is_the_products_revenue():
    got, skipped = _read("Remicade")
    assert got.get("2016Q1") == pytest.approx(1779.0), skipped
    assert got.get("2015Q1") == pytest.approx(1600.0)


def test_an_export_line_is_the_products_own_and_not_a_competitors():
    """The defect, stated on the guard itself."""
    siblings = ["REMICADE", "US", "US Exports (3)", "Intl", "WW", "STELARA"]
    assert names_a_competing_product("REMICADE US Exports (3)", ["remicade"], siblings) is None
    assert names_a_competing_product("REMICADE US", ["remicade"], siblings) is None


def test_a_product_without_the_extra_row_is_unaffected():
    got, skipped = _read("Stelara")
    assert got.get("2016Q1") == pytest.approx(735.0), skipped


class TestASlashJoinsOneProductsNames:
    """A slash is a brand and another name for the same brand."""

    def _named(self, label, product):
        rows = _schedule()
        rows[5] = [label, "", "", "", "", ""]
        return _read(product, tables=[rows])

    @pytest.mark.parametrize(
        "label, product",
        [
            ("CONCERTA/METHYLPHENIDATE", "Concerta"),   # brand and its generic
            ("PROCRIT/EPREX", "Procrit"),               # one drug, two markets
            ("INVOKANA/INVOKAMET", "Invokana"),         # brand and its combination
            ("INVEGA SUSTENNA/XEPLION/TRINZA/TREVICTA", "Invega Sustenna"),
            ("SIMPONI / SIMPONI ARIA", "Simponi"),      # injected two ways
        ],
    )
    def test_the_line_is_read_as_that_products(self, label, product):
        got, skipped = self._named(label, product)
        assert got.get("2016Q1") == pytest.approx(1779.0), skipped

    def test_but_not_when_the_other_name_has_a_row_of_its_own(self):
        """Then the filer reports them separately and the line covers both."""
        siblings = ["OPSUMIT/OPSYNVI", "OPSUMIT", "US", "Intl", "WW"]
        assert names_a_competing_product("OPSUMIT/OPSYNVI", ["opsynvi"], siblings) == "opsumit"

    def test_and_a_slash_does_not_smuggle_an_aggregate_through(self):
        siblings = ["REMICADE/OTHER", "US", "Intl", "WW"]
        assert names_a_competing_product("REMICADE/OTHER", ["remicade"], siblings) == "other"


def test_commas_and_and_still_join_different_products():
    """Biogen's profit-share line covers three separately marketed drugs, and
    is the case the held-out disambiguation set exists to protect."""
    label = "Biogen's share of pre-tax profits in the U.S. for RITUXAN, GAZYVA and LUNSUMIO"
    for product in ("rituxan", "gazyva", "lunsumio"):
        assert names_a_competing_product(label, [product], []) is not None
