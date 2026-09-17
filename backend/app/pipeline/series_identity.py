"""What makes a quarterly series one series, and which reading it holds.

An analyst fits a curve to a series. Two figures for one quarter are not a
curve, and two figures under one product name that are figures for different
things - the whole product and one region, the product alone and the pair it
is sold with - are not one series however consistently they are labelled.

So each reading says which series it belongs to, in a key built from what the
reading itself declares: who files it, which product, what revenue scope, what
geography, what formulation, what the filer reported it as, in what currency,
over what kind of period. Readings that agree on all of that are readings of
one series; readings that do not are separate series that happen to share a
product name.

Within a series and a quarter exactly one reading is the figure, and the rest
say what they are in relation to it. That decision is a column, so that every
surface reads the same answer instead of each one picking a row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.domain.models import RevenueScope, SeriesSelection
from app.parsing.periods import quarter_of_month

# The two spellings of "the whole product": a sentence says Worldwide, a
# schedule's family line says Product family. One group for reconciliation.
_WHOLE_PRODUCT_SCOPES = frozenset(
    {RevenueScope.PRODUCT_FAMILY.value, RevenueScope.WORLDWIDE.value}
)


def _scope_key(scope: str | None) -> str:
    whole = RevenueScope.PRODUCT_FAMILY.value
    return whole if (scope or "") in _WHOLE_PRODUCT_SCOPES else (scope or "")


# How strong a claim each producer makes about the figure it put on a row. The
# axis from SOURCE_PRIORITY, which ranks the document a figure came from: a
# product-sales schedule and a sentence of narrative can sit in the same 8-K
# exhibit, so the source type does not separate them and the schedule is
# plainly the better claim. Ordered by how much has to be inferred - a tagged
# fact states its own period, unit and product; a schedule declares its unit
# and its columns; a derivation is exact arithmetic over figures the issuer
# published; a sentence and a model's reading are recovered from running text.
# Two spellings reach this, and both are listed rather than normalised in one
# of them: a candidate carries the reader's own label ("table_fingerprint",
# "prose_sentence") and a stored datapoint carries the shorter one the export
# uses. One ranking, both vocabularies.
#
# A snapshot of the producers that can put a revenue figure on a datapoint:
# the tagged readers' `xbrl_fact`, the fingerprinted table and prose readers'
# own labels and the shorter spellings `_DETERMINISTIC_METHODS` maps them to,
# the model's `llm`, and the two `normalization_status` values `derive.py`
# stamps on a derivation. A producer added anywhere in `extraction/` without
# a line here is not refused - it ranks last, behind the model - so what makes
# this stale is a new reader, and the symptom is a strong claim losing to a
# sentence.
CLAIM_STRENGTH = {
    "xbrl_fact": 0,
    "table": 1,
    "table_fingerprint": 1,
    "derived_from_period_total": 2,
    "derived_sole_formulation": 2,
    "llm": 3,
    "prose": 4,
    "prose_sentence": 4,
}


def claim_rank(extraction_method: str | None) -> int:
    """Where a producer sits in CLAIM_STRENGTH; unknown producers rank last."""
    return CLAIM_STRENGTH.get(str(extraction_method or ""), len(CLAIM_STRENGTH))


GEOGRAPHY_UNSPECIFIED = "unspecified"
GEOGRAPHY_UNRECOGNISED = "unrecognised"

# The words a filing uses for each of the places a product's revenue is
# reported for. A snapshot of what this pipeline's readers have written into
# `geography` across the runs in the scratchpad, reduced to the distinct words:
# case, punctuation and the hyphen are handled by `_squash` below rather than
# spelled out here, so "U.S.", "US" and "u.s." are one entry and not three.
#
# What makes it stale is a filing that names a place in words that are not
# here - a country, a region, or two places in one label. Such a label is not
# mapped to the nearest entry and is not dropped: it comes back under
# `unrecognised`, keeps the spelling that produced it so that two unknown
# places never collapse into one, and is raised as a quality issue where a
# reading carries it. The symptom of a missing entry is therefore a visible
# bucket, never a silently merged series.
_GEOGRAPHY_VOCABULARY: dict[str, tuple[str, ...]] = {
    "worldwide": ("worldwide", "global"),
    "united-states": ("us", "usa", "united states"),
    "ex-united-states": ("ex us", "outside of us", "outside the us", "rest of world"),
    "north-america": ("north america", "americas"),
    "europe": ("europe",),
    "japan": ("japan",),
}

_NOT_A_WORD_RE = re.compile(r"[^a-z0-9]+")


def _squash(value: str) -> str:
    """A label reduced to its words: "Ex-U.S." and "ex US" are one spelling."""
    return " ".join(_NOT_A_WORD_RE.sub(" ", value.replace(".", "").lower()).split())


def _slug(value: object) -> str:
    """A label as one token of an identity key: lower case, no underscore.

    The parts of an identity are joined with `_`, and no part may contain one,
    so that a key can be split back into the parts that built it.
    """
    return _NOT_A_WORD_RE.sub("-", str(value or "").lower()).strip("-") or ""


_GEOGRAPHY_BY_SPELLING = {
    spelling: term
    for term, spellings in _GEOGRAPHY_VOCABULARY.items()
    for spelling in spellings
}


def normalize_geography(raw: str | None) -> str:
    """The controlled term for a geography label, or an unrecognised bucket.

    Returns `unspecified` where a reading named no place at all - which is a
    different thing from naming one nobody here recognises. An unrecognised
    label keeps its own spelling in the term, so that `Acme Territories` and
    `Calderon Territories` do not become one series by both being unknown.
    """
    squashed = _squash(raw or "")
    if not squashed:
        return GEOGRAPHY_UNSPECIFIED
    term = _GEOGRAPHY_BY_SPELLING.get(squashed)
    if term:
        return term
    return f"{GEOGRAPHY_UNRECOGNISED}-{_slug(squashed)}"


def is_unrecognised_geography(term: str | None) -> bool:
    """Whether a normalised term is the bucket a new spelling lands in."""
    return bool(term) and str(term).startswith(GEOGRAPHY_UNRECOGNISED)


# Each revenue scope as one token, from the enum's own member names, so that a
# scope added to `RevenueScope` reaches an identity key without a second list
# edited here.
_SCOPE_TOKENS = {scope.value: scope.name.lower().replace("_", "-") for scope in RevenueScope}

# What each part of an identity says when the reading declared nothing.
_UNSTATED = "unstated"
# What the `reported_as` part says for a figure that is the product's own.
_OWN_LINE = "own"
# What it says for a figure a reader flagged as covering more than this
# product without naming what else it covers.
_COMBINED_UNNAMED = "combined-unnamed"


def series_identity(
    *,
    issuer: str | None,
    product: str | None,
    revenue_scope: str | None,
    geography: str | None,
    formulation: str | None,
    reported_as: str | None,
    currency: str | None,
    period_type: str | None,
    combined_line: bool = False,
) -> str:
    """The key that says which series a reading belongs to.

    One key per series and the same key on every reading of it, built only
    from what the reading declares, so that two readings can be compared
    without asking anything else. A figure a filer prints for two products
    together belongs to the pair's series and says so, because published under
    the plain brand it is a figure for something the brand did not sell.

    Shape: `0000000000_calderon_product-family_worldwide_aggregate_own_usd_quarterly`,
    and for the pair's line `..._calderon-nuvessa_usd_quarterly`.
    """
    combined = _slug(reported_as) or (_COMBINED_UNNAMED if combined_line else _OWN_LINE)
    parts = (
        _slug(issuer) or _UNSTATED,
        _slug(product) or _UNSTATED,
        _SCOPE_TOKENS.get(_scope_key(revenue_scope), _slug(revenue_scope)) or _UNSTATED,
        _slug(normalize_geography(geography)),
        _slug(formulation) or _UNSTATED,
        combined,
        _slug(currency) or _UNSTATED,
        _slug(period_type) or _UNSTATED,
    )
    return "_".join(parts)


@dataclass(frozen=True)
class SeriesReading:
    """One reading, reduced to what deciding the series figure needs.

    `cell` is what makes two readings readings of the same quarter of the same
    product - the job, the period label and the span it covers. `strength`
    sorts stronger claims first, and the caller supplies it because what makes
    one reading stronger than another is the ranking reconciliation already
    used, not a second opinion invented here.
    """

    id: str
    cell: tuple
    identity: str
    value: float | None
    publishes: bool
    strength: tuple


@dataclass(frozen=True)
class SeriesStanding:
    """What a series does with one reading, and which row holds the figure.

    `held_by` is the reading whose figure the series holds for that quarter -
    the row itself where it was selected, and otherwise the row that
    superseded it or that it duplicates. A duplicate that names what resolved
    it is a question answered rather than one left open.
    """

    selection: str | None
    held_by: str | None


def select_series_figures(readings: list[SeriesReading]) -> dict[str, SeriesStanding]:
    """Which reading each series holds for each quarter, and what the rest are.

    Exactly one `selected` reading per (identity, cell), chosen by strength
    among the readings the pipeline stands behind. Then, in each cell, a
    figure selected twice under two identities is one figure labelled two
    ways: the strongest reader's identity keeps it and the others become
    duplicates, because a quarter's revenue read once cannot be two points of
    two series. Every remaining reading is a `duplicate` of the figure its
    cell holds where it states the same figure, `superseded` where it states a
    different one for a series that has a figure, and undecided where its cell
    has none - which is not a judgement, and says so by staying empty.
    """
    standing: dict[str, SeriesStanding] = {
        reading.id: SeriesStanding(None, None) for reading in readings
    }
    order = {reading.id: (reading.strength, reading.id) for reading in readings}

    by_series: dict[tuple, list[SeriesReading]] = {}
    for reading in readings:
        by_series.setdefault((reading.cell, reading.identity), []).append(reading)

    selected: list[SeriesReading] = []
    for group in by_series.values():
        candidates = [r for r in group if r.publishes and r.value is not None]
        if not candidates:
            continue
        winner = min(candidates, key=lambda r: order[r.id])
        standing[winner.id] = SeriesStanding(SeriesSelection.SELECTED.value, winner.id)
        selected.append(winner)

    by_cell: dict[tuple, list[SeriesReading]] = {}
    for winner in selected:
        by_cell.setdefault(winner.cell, []).append(winner)

    for cell, winners in list(by_cell.items()):
        by_value: dict[float, list[SeriesReading]] = {}
        for winner in winners:
            by_value.setdefault(float(winner.value), []).append(winner)
        for same_figure in by_value.values():
            if len(same_figure) < 2:
                continue
            keeps = min(same_figure, key=lambda r: order[r.id])
            for other in same_figure:
                if other.id != keeps.id:
                    standing[other.id] = SeriesStanding(SeriesSelection.DUPLICATE.value, keeps.id)
        by_cell[cell] = [
            w for w in winners if standing[w.id].selection == SeriesSelection.SELECTED.value
        ]

    for reading in readings:
        if standing[reading.id].selection is not None:
            continue
        winners = by_cell.get(reading.cell, [])
        same_figure = [
            winner
            for winner in winners
            if reading.value is not None and float(winner.value) == float(reading.value)
        ]
        same_series = [winner for winner in winners if winner.identity == reading.identity]
        if same_figure:
            standing[reading.id] = SeriesStanding(
                SeriesSelection.DUPLICATE.value, min(same_figure, key=lambda r: order[r.id]).id
            )
        elif same_series:
            standing[reading.id] = SeriesStanding(
                SeriesSelection.SUPERSEDED.value, min(same_series, key=lambda r: order[r.id]).id
            )
    return standing


def quarter_containing(when: date | None) -> str | None:
    """The quarter label a date falls in: 13 March 2024 -> 2024Q1."""
    if when is None:
        return None
    return f"{when.year}Q{quarter_of_month(when.month)}"


def commercial_start_quarter(
    quarters: list[tuple[str, bool]], *, launch_quarter: str | None
) -> str | None:
    """The first quarter of a series that is a quarter of selling.

    Two things are not one. A product is approved on a date, and that date
    anchors the x-axis. Selling starts later, and the first quarter with a
    figure is not always a quarter of it: an eighteen-day stub between launch
    and the quarter's end is a fraction of one, and a figure filed for a
    quarter before approval is not product revenue at all. Either one read as
    the first quarter of the ramp makes every uptake number after it wrong, so
    the series says where it begins rather than leaving a reader to assume.

    `quarters` is each quarter the series holds and whether its figure covers
    the whole of it.
    """
    for period, whole_quarter in sorted(quarters):
        if not whole_quarter:
            continue
        if launch_quarter and period < launch_quarter:
            continue
        return period
    return None
