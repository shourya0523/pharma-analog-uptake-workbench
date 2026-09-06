"""Column semantics from a table's own header, then alignment by constraint.

A table header is the only thing that says what its columns mean. Issuers
print that in dozens of shapes - "Three Months Ended June 30, 2024 2023",
"SECOND QUARTER SIX MONTHS 2018 2017 Reported Operational Currency",
"1Q 2Q 3Q 4Q Full Year", "March 31, 2022 | June 30, 2022 | ...", "U.S. Int'l
Total" nested under years, "Q2 through 6/15" - and none of those shapes is
known here by name. What is known is the vocabulary: a period token, a year,
a geography, a change-column marker, a partial-period marker. The header is
read as a sequence of those tokens and turned into candidate column layouts by
a small set of composition rules that only depend on how the tokens repeat.

Reading a row against a layout is then a constraint problem rather than a
positional guess. Cells go missing when a grid is flattened (a blank cell
leaves no token), so a row may be shorter than its layout; every way of
placing the gaps is tried and each placement is checked against what the
row's own arithmetic has to satisfy - change columns match the values they
describe, a year-to-date column bounds the quarters inside it, geography
parts add to their total, four quarters add to a full year. One placement
surviving is a reading; several is an ambiguity that is reported rather than
resolved by preference; none means the row is not laid out as the header
declares.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from app.parsing.grids import is_placeholder_token
from app.parsing.periods import quarter_of_month

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

_PERIOD_TYPE_BY_MONTHS = {3: "quarterly", 6: "six_month", 9: "nine_month", 12: "annual"}


@dataclass(frozen=True)
class ColumnSpec:
    kind: str                         # "value" | "change"
    months: int | None = None
    end_month: int | None = None
    year: int | None = None
    geography: str | None = None
    covers: tuple[str, str] | None = None
    label: str = ""

    @property
    def period(self) -> str | None:
        if self.kind != "value" or self.year is None or self.months is None:
            return None
        if self.months == 3 and self.end_month:
            return f"{self.year}Q{quarter_of_month(self.end_month)}"
        return str(self.year)

    @property
    def period_type(self) -> str:
        return _PERIOD_TYPE_BY_MONTHS.get(self.months or 0, "unknown")

    @property
    def quarter(self) -> int | None:
        if self.months == 3 and self.end_month:
            return quarter_of_month(self.end_month)
        return None


@dataclass(frozen=True)
class ColumnLayout:
    columns: tuple[ColumnSpec, ...]
    unit_label: str = "millions"
    currency: str = "USD"
    unit_declared: bool = False
    currency_declared: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def value_columns(self) -> list[int]:
        return [i for i, c in enumerate(self.columns) if c.kind == "value"]

    @property
    def signature(self) -> str:
        shape = "|".join(
            f"{c.kind}:{c.months}m@{c.end_month}:{c.year}:{c.geography or '-'}" for c in self.columns
        )
        return f"{self.unit_label}/{self.currency}/{shape}"

    @property
    def usable(self) -> bool:
        return bool(self.value_columns) and all(
            self.columns[i].year is not None and self.columns[i].months is not None
            for i in self.value_columns
        )


@dataclass(frozen=True)
class Cell:
    value: float | None      # None for a placeholder
    percent: bool = False
    text: str = ""


# A printed number: optional sign or opening parenthesis, a currency sign,
# digits with thousands separators as a comma, space, no-break space or
# apostrophe ("1,177", "1 177", "1'177"), decimals, a closing parenthesis and
# a percent sign with or without a space before it ("+14.7 %").
_NUMBER_CORE_RE = re.compile(
    r"^(?P<neg>[\(\-–−])?\+?\$?\s?(?P<num>\d{1,3}(?:[ ,\u00a0\u202f']\d{3})+(?:\.\d+)?|[\d,]*\.?\d+)\)?\s?(?P<pct>%)?$"
)


def parse_cell(token: str) -> Cell | None:
    text = token.strip()
    if is_placeholder_token(text):
        return Cell(None, text=text)
    match = _NUMBER_CORE_RE.match(text)
    if not match:
        return None
    value = float(re.sub(r"[ ,\u00a0\u202f']", "", match.group("num")))
    if match.group("neg"):
        value = -value
    return Cell(value, percent=bool(match.group("pct")), text=text)


@dataclass(frozen=True)
class Alignment:
    values: dict[int, float]         # column index -> value
    verified: tuple[str, ...]        # constraints that held
    gaps: tuple[int, ...]            # column indexes left blank


_PERCENT_TOLERANCE = 0.75
_MAX_GAP_COMBINATIONS = 6000


def _change_ok(current: float | None, prior: float | None, cell: Cell) -> str | None:
    """"pass", "near" or "fail" when checkable, None when the change cannot be computed.

    An issuer computes the change on unrounded figures and sometimes on a
    restated prior period, so a stated percentage can sit a point or two
    from what the printed figures give. That is "near": not proof of the
    layout, but not evidence against it either. "fail" is a change that no
    rounding or restatement explains, which means the cell is not the
    change of these two columns.
    """
    if cell.value is None:
        return None
    if current is None or prior is None or prior == 0:
        return None
    expected = (current - prior) / abs(prior) * 100.0
    difference = current - prior
    if not cell.percent and abs(abs(cell.value) - abs(difference)) <= 0.051 + 0.0005 * abs(difference):
        return "pass"
    if abs(expected) > 100 and abs(cell.value) > 100:
        return "pass"
    deviation = abs(abs(cell.value) - abs(expected))
    if deviation <= _PERCENT_TOLERANCE + 0.005 * abs(expected):
        return "pass"
    if deviation <= 5.0 + 0.1 * abs(expected):
        return "near"
    return "fail"


def _tolerance(parts: int) -> float:
    return 0.5 * (parts + 1) + 0.01


def _partitions(total: float, components: list[float]) -> bool:
    """The regions account for the total, flat or nested.

    Flat: every region sums to the total. Nested: some regions sum to the
    total and the rest sum to one of those (United States + International =
    Total, with International split into named regions beside it). Small
    headers are searched exhaustively; nothing about which region nests in
    which is assumed.
    """
    if abs(sum(components) - total) <= _tolerance(len(components)):
        return True
    n = len(components)
    if n > 10:
        return False
    for mask in range(1, 1 << n):
        chosen = [components[i] for i in range(n) if mask >> i & 1]
        rest = [components[i] for i in range(n) if not mask >> i & 1]
        if len(chosen) < 2 or not rest:
            continue
        if abs(sum(chosen) - total) > _tolerance(len(chosen)):
            continue
        if any(abs(sum(rest) - member) <= _tolerance(len(rest)) for member in chosen):
            return True
    return False


def _check(layout: ColumnLayout, placed: list[Cell | None]) -> tuple[bool, list[str]]:
    """Every arithmetic relation the header declares must hold for this row."""
    columns = layout.columns
    verified: list[str] = []

    # Percent cells can only sit on change columns, and a revenue value is
    # not negative: a negative number in a value slot is a change column
    # the header did not declare.
    for index, cell in enumerate(placed):
        if cell is not None and cell.percent and columns[index].kind != "change":
            return False, []
        if (
            cell is not None and cell.value is not None and cell.value < 0
            and columns[index].kind == "value" and any(c is None for c in placed)
        ):
            return False, []

    def value_at(index: int) -> float | None:
        cell = placed[index]
        return None if cell is None else cell.value

    # Change columns follow a group of value columns and describe the first
    # two of them (current versus prior). Verify whichever can be computed.
    change_checked = False
    index = 0
    while index < len(columns):
        if columns[index].kind != "value":
            index += 1
            continue
        group_start = index
        while index < len(columns) and columns[index].kind == "value":
            index += 1
        group = list(range(group_start, index))
        changes: list[int] = []
        while index < len(columns) and columns[index].kind == "change":
            changes.append(index)
            index += 1
        if not changes:
            continue
        results = []
        for c in changes:
            if placed[c] is None:
                continue
            geography = columns[c].geography
            if geography is not None:
                # "% Change: U.S. Int'l Total" describes the two value columns
                # of that geography, wherever the header put them; with only
                # one such column in the row there is nothing to check.
                same = [i for i, col in enumerate(columns) if col.kind == "value" and col.geography == geography]
                if len(same) < 2:
                    continue
                current, prior = value_at(same[0]), value_at(same[1])
            elif len(group) >= 2:
                # A change compares like periods: the first column against the
                # same span a year earlier when the group has one (a run of
                # quarters "Q3 2024 ... Q3 2023 | % change"), else its neighbour.
                first = columns[group[0]]
                like = [
                    j for j in group[1:]
                    if columns[j].months == first.months and columns[j].end_month == first.end_month
                    and columns[j].geography == first.geography and columns[j].year == (first.year or 0) - 1
                ]
                current = value_at(group[0])
                prior = value_at(like[0]) if like else value_at(group[1])
            else:
                continue
            results.append(_change_ok(current, prior, placed[c]))
        results = [r for r in results if r is not None]
        if results:
            change_checked = True
            if "pass" not in results and "near" not in results:
                return False, []
            if "pass" in results:
                verified.append("change_column")
            else:
                verified.append("change_column_near")

    # Geography parts sum to their total within the same period.
    by_period: dict[tuple[int | None, int | None, int | None], list[tuple[str, float]]] = {}
    for index, column in enumerate(columns):
        if column.kind != "value" or column.geography is None:
            continue
        value = value_at(index)
        if value is None:
            continue
        by_period.setdefault((column.months, column.end_month, column.year), []).append((column.geography, value))
    for parts in by_period.values():
        # Several regions the closed set does not name are all "Other" and
        # each is a part of its own.
        totals = [v for g, v in parts if g == "Worldwide"]
        total = totals[0] if totals else None
        components = [v for g, v in parts if g != "Worldwide"]
        if total is not None and len(components) >= 2:
            if not _partitions(total, components):
                return False, []
            verified.append("geography_sum")

    # A longer period bounds and, when complete, equals its quarters.
    by_year: dict[tuple[int, str | None], dict[int, float]] = {}
    totals: dict[tuple[int, str | None, int], float] = {}
    for index, column in enumerate(columns):
        if column.kind != "value" or column.year is None:
            continue
        value = value_at(index)
        if value is None:
            continue
        if column.months == 3 and column.quarter:
            by_year.setdefault((column.year, column.geography), {})[column.quarter] = value
        elif column.months in {6, 9, 12}:
            totals[(column.year, column.geography, column.months)] = value
    for (year, geo, months), total in totals.items():
        quarters = by_year.get((year, geo), {})
        inside = [q for q in range(1, months // 3 + 1) if q in quarters]
        if not inside:
            continue
        summed = sum(quarters[q] for q in inside)
        if summed - total > _tolerance(len(inside)) and total >= 0:
            return False, []
        if len(inside) == months // 3:
            if abs(summed - total) > _tolerance(len(inside)):
                return False, []
            verified.append("quarters_sum_to_total")
        else:
            verified.append("total_bounds_quarters")

    if not change_checked and any(c.kind == "change" for c in columns):
        # Change columns exist but none could be computed (dashes, first-year
        # products). Alignment then rests on the placeholders holding their
        # columns, which the token count already enforced.
        verified.append("change_uncheckable")
    return True, verified


def align_row(tokens: list[str], layout: ColumnLayout) -> tuple[list[Alignment], str | None]:
    """Every placement of the row's cells on the layout that survives the checks.

    A row shorter than its layout has blank cells that left no token; every
    way of placing those blanks is tried. A row longer than its layout cannot
    be aligned and is reported.
    """
    cells = [parse_cell(t) for t in tokens]
    if any(c is None for c in cells):
        return [], "unparseable_cell"
    columns = layout.columns
    if not columns:
        return [], "no_columns"
    missing = len(columns) - len(cells)
    if missing < 0:
        return [], "more_cells_than_columns"
    if missing == 0:
        gap_sets: list[tuple[int, ...]] = [()]
    else:
        # Blank cells are most often value columns an issuer left empty
        # (a product not yet launched); a change column left blank is rarer
        # but real. Try every placement, bounded.
        combos = itertools.combinations(range(len(columns)), missing)
        gap_sets = list(itertools.islice(combos, _MAX_GAP_COMBINATIONS + 1))
        if len(gap_sets) > _MAX_GAP_COMBINATIONS:
            return [], "too_many_blank_placements"

    alignments: list[Alignment] = []
    for gaps in gap_sets:
        placed: list[Cell | None] = []
        iterator = iter(cells)
        for index in range(len(columns)):
            placed.append(None if index in gaps else next(iterator))
        ok, verified = _check(layout, placed)
        if not ok:
            continue
        values = {
            i: c.value for i, c in enumerate(placed)
            if c is not None and c.value is not None and columns[i].kind == "value"
        }
        if not values:
            continue
        alignments.append(Alignment(values=values, verified=tuple(verified), gaps=gaps))

    # Distinct alignments that place the same values on the same columns are
    # one reading (the blanks fell on change columns either way).
    unique: dict[tuple[tuple[int, float], ...], Alignment] = {}
    for alignment in alignments:
        key = tuple(sorted(alignment.values.items()))
        if key not in unique or len(alignment.verified) > len(unique[key].verified):
            unique[key] = alignment
    result = list(unique.values())
    if not result:
        return [], "no_placement_satisfies_the_header"
    return result, None
