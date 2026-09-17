"""A quarter of a series has one figure, and the series says which one.

Two readings of one product and one quarter are not two points. Nothing said
which of them a curve was drawn from, so each surface picked one for itself -
the chart took whichever row it read last, the export wrote them all. That is
a wrong number on screen where the two readings are a worldwide figure and one
region's slice of it.

A reading now names the series it belongs to, and within a series and a
quarter exactly one reading is selected. Both answers are asserted here: a
product that reports a region as well as the whole keeps both series, and a
figure labelled four ways is one point.
"""

from __future__ import annotations

from app.domain.models import SeriesSelection, holds_the_series_figure
from app.pipeline.series_identity import (
    GEOGRAPHY_UNSPECIFIED,
    SeriesReading,
    is_unrecognised_geography,
    normalize_geography,
    select_series_figures,
    series_identity,
)


def _identity(**overrides) -> str:
    fields = {
        "issuer": "0000000001",
        "product": "Calderon",
        "revenue_scope": "Worldwide",
        "geography": None,
        "formulation": "aggregate",
        "reported_as": None,
        "currency": "USD",
        "period_type": "quarterly",
    }
    return series_identity(**{**fields, **overrides})


def test_a_place_spelled_two_ways_is_one_place():
    """The punctuation a filer uses is not a property of the market."""
    assert normalize_geography("U.S.") == normalize_geography("US") == "united-states"
    assert normalize_geography("ex-US") == normalize_geography("Ex-U.S.") == "ex-united-states"
    assert normalize_geography("Worldwide") == normalize_geography("global") == "worldwide"
    assert normalize_geography(None) == GEOGRAPHY_UNSPECIFIED
    assert not is_unrecognised_geography("united-states")


def test_a_place_the_vocabulary_does_not_know_is_visible_and_stays_itself():
    """A new spelling is neither dropped nor bent onto the nearest entry."""
    acme = normalize_geography("Acme Territories")
    calderon = normalize_geography("Calderon Territories")
    assert is_unrecognised_geography(acme) and is_unrecognised_geography(calderon)
    assert acme != calderon
    # Two places named in one label is one of those: nobody can say which of
    # them the figure is for.
    assert is_unrecognised_geography(normalize_geography("U.S.; Rest of world"))


def test_an_identity_separates_what_a_figure_is_a_figure_for():
    """The whole product, one region, and the pair are three series."""
    whole = _identity()
    assert _identity(revenue_scope="Product family") == whole
    assert _identity(geography="Worldwide") != whole
    assert _identity(revenue_scope="U.S.") != whole
    assert _identity(formulation="nebulized") != whole
    assert _identity(currency="CHF") != whole
    assert _identity(period_type="annual") != whole
    assert _identity(product="Calderon XR") != whole
    assert _identity(issuer="0000000002") != whole
    # Two spellings of one place are one series; the identity reads the
    # vocabulary rather than the label.
    assert _identity(geography="U.S.") == _identity(geography="US")


def test_a_pair_s_line_is_not_the_brand_s_own_series():
    """A figure for Calderon and NuVessa together is not Calderon's figure."""
    own = _identity()
    pair = _identity(reported_as="Calderon + NuVessa")
    unnamed = _identity(combined_line=True)
    assert pair != own
    assert unnamed != own
    assert unnamed != pair


def _reading(ident, value, *, id, publishes=True, strength=(0,), period="2024Q2"):
    return SeriesReading(
        id=id,
        cell=(period, "quarterly"),
        identity=ident,
        value=value,
        publishes=publishes,
        strength=strength,
    )


def test_a_region_and_the_whole_product_stay_two_series():
    """Three figures for one quarter that are figures for three things."""
    readings = [
        _reading(_identity(), 144.1, id="whole"),
        _reading(_identity(revenue_scope="U.S.", geography="US"), 124.1, id="us"),
        _reading(_identity(revenue_scope="ex-U.S.", geography="ex-US"), 20.0, id="exus"),
    ]
    standing = select_series_figures(readings)
    assert {key: value.selection for key, value in standing.items()} == {
        "whole": SeriesSelection.SELECTED.value,
        "us": SeriesSelection.SELECTED.value,
        "exus": SeriesSelection.SELECTED.value,
    }


def test_one_figure_labelled_four_ways_is_one_point():
    """The strongest reader's identity keeps it; the rest say what they are."""
    readings = [
        _reading(_identity(geography="United States"), 121.7, id="a", publishes=False, strength=(3,)),
        _reading(_identity(geography="Worldwide"), 121.7, id="b", publishes=False, strength=(3,)),
        _reading(_identity(revenue_scope="U.S."), 121.7, id="c", publishes=False, strength=(3,)),
        _reading(_identity(), 121.7, id="tagged", strength=(0,)),
    ]
    standing = select_series_figures(readings)
    assert standing["tagged"].selection == SeriesSelection.SELECTED.value
    for other in ("a", "b", "c"):
        assert standing[other].selection == SeriesSelection.DUPLICATE.value
        assert standing[other].held_by == "tagged"


def test_a_weaker_reading_of_the_same_series_is_superseded_and_names_the_figure():
    """A restated comparative does not replace the quarter as it was filed."""
    readings = [
        _reading(_identity(), 139.9, id="as-filed", strength=(0,)),
        _reading(_identity(), 138.5, id="comparative", strength=(2,)),
    ]
    standing = select_series_figures(readings)
    assert standing["as-filed"].selection == SeriesSelection.SELECTED.value
    assert standing["comparative"].selection == SeriesSelection.SUPERSEDED.value
    assert standing["comparative"].held_by == "as-filed"


def test_a_quarter_nothing_published_is_left_undecided():
    """Not selecting is not a decision against a row, and does not read as one."""
    readings = [
        _reading(_identity(), 10.0, id="held", publishes=False),
        _reading(_identity(revenue_scope="U.S."), 4.0, id="also-held", publishes=False),
    ]
    standing = select_series_figures(readings)
    assert [value.selection for value in standing.values()] == [None, None]
    assert holds_the_series_figure(None)
    assert holds_the_series_figure(SeriesSelection.SELECTED.value)
    assert not holds_the_series_figure(SeriesSelection.DUPLICATE.value)
    assert not holds_the_series_figure(SeriesSelection.SUPERSEDED.value)


def test_a_quarter_and_a_year_of_the_same_size_are_not_one_reading():
    """A product with one quarter of selling states that figure twice."""
    year = SeriesReading(
        id="year",
        cell=("2024", "annual"),
        identity=_identity(period_type="annual"),
        value=8.0,
        publishes=True,
        strength=(0,),
    )
    quarter = _reading(_identity(), 8.0, id="quarter", period="2024Q4")
    standing = select_series_figures([year, quarter])
    assert standing["year"].selection == SeriesSelection.SELECTED.value
    assert standing["quarter"].selection == SeriesSelection.SELECTED.value


def test_one_figure_printed_to_two_precisions_is_one_reading_of_it():
    """The selection means by "same figure" what reconciliation means.

    Reconciliation calls two readings one figure when they agree within what
    their sources declared; the selection compared the digits, so a reading of
    the figure a series holds, printed one place coarser, was not recognised as
    a reading of it at all.

    Both answers, with neither source declaring a bound so the fraction stands
    in: the rounded reading is a reading of the figure the cell holds, and a
    reading a million away is not.
    """
    rounded = [
        _reading(_identity(), 159.186, id="tagged", strength=(0,)),
        _reading(_identity(geography="US"), 159.2, id="printed",
                 publishes=False, strength=(1,)),
    ]
    standing = select_series_figures(rounded)
    assert standing["tagged"].selection == SeriesSelection.SELECTED.value
    assert standing["printed"].selection == SeriesSelection.DUPLICATE.value
    assert standing["printed"].held_by == "tagged"

    apart = [
        _reading(_identity(), 159.186, id="tagged", strength=(0,)),
        _reading(_identity(geography="US"), 161.0, id="printed",
                 publishes=False, strength=(1,)),
    ]
    standing = select_series_figures(apart)
    assert standing["tagged"].selection == SeriesSelection.SELECTED.value
    assert standing["printed"].selection is None


def test_what_the_sources_declared_bounds_how_far_one_figure_may_sit_apart():
    """A source that declared itself exact is not rounded to the next million.

    Both answers on one pair of values: read as two exact figures they are two,
    and read as two figures rounded to the nearest million they are one.
    """
    def pair(uncertainty):
        return [
            SeriesReading(id="a", cell=("2024Q2", "quarterly"), identity=_identity(),
                          value=159.0, publishes=True, strength=(0,),
                          rounding_uncertainty=uncertainty),
            SeriesReading(id="b", cell=("2024Q2", "quarterly"),
                          identity=_identity(geography="US"),
                          value=160.0, publishes=False, strength=(1,),
                          rounding_uncertainty=uncertainty),
        ]

    exact = select_series_figures(pair(0.0))
    assert exact["b"].selection is None

    rounded = select_series_figures(pair(0.5))
    assert rounded["b"].selection == SeriesSelection.DUPLICATE.value


def test_two_series_that_hold_one_number_are_still_two_series():
    """A figure is not a series, so two of them are not one reading twice.

    A product sold only in one place reports its worldwide line and its one
    region as the same number, quarter after quarter. Collapsing them dropped
    the region from the curve.

    Both answers: two identities each holding the figure both keep it, and two
    readings of one identity do not.
    """
    two_series = [
        _reading(_identity(), 124.1, id="whole", strength=(0,)),
        _reading(_identity(revenue_scope="U.S.", geography="US"), 124.1,
                 id="region", strength=(1,)),
    ]
    standing = select_series_figures(two_series)
    assert standing["whole"].selection == SeriesSelection.SELECTED.value
    assert standing["region"].selection == SeriesSelection.SELECTED.value

    one_series = [
        _reading(_identity(), 124.1, id="tagged", strength=(0,)),
        _reading(_identity(), 124.1, id="printed", strength=(1,)),
    ]
    standing = select_series_figures(one_series)
    assert standing["tagged"].selection == SeriesSelection.SELECTED.value
    assert standing["printed"].selection == SeriesSelection.DUPLICATE.value
