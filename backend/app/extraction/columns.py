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
from dataclasses import dataclass, field, replace

from app.extraction.fingerprint import detect_currency, detect_unit
from app.parsing.grids import is_number_token, is_placeholder_token, is_year_token
from app.parsing.periods import MONTHS, quarter_of_month

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
_LENGTH_WORDS = {"three": 3, "six": 6, "nine": 9, "twelve": 12}
_MONTH_NAME = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"(?:19|20)\d{2}"

# Ordered so that longer, more specific forms are matched first.
_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "three months ended June 30, 2024" / "quarter ended June 30" / "year ended December 31, 2023"
    (
        "months_ended",
        re.compile(
            rf"\b(?:(?P<len>three|six|nine|twelve)\s+months?|(?P<qword>quarter)|(?P<fy>(?:fiscal\s+)?years?))\s+"
            rf"end(?:ed|ing)\s+(?P<month>{_MONTH_NAME})\.?\s*(?P<day>\d{{1,2}})?,?\s*(?:(?P<year>{_YEAR})(?!\s*{_YEAR}\b))?",
            re.I,
        ),
    ),
    # "Three Months Ended" / "Nine Months" with the month stated elsewhere
    (
        "months_bare",
        re.compile(
            r"\b(?:(?P<len>three|six|nine|twelve)\s+months?(?:\s+end(?:ed|ing))?|(?P<fy>(?:fiscal\s+)?years?\s+end(?:ed|ing)))\b(?!\s+" + _MONTH_NAME + r")",
            re.I,
        ),
    ),
    # "through 6/15", "through June 15, 2017", "from June 16, 2017"
    (
        "partial",
        re.compile(
            rf"\b(?P<dir>through|thru|to|from|since|beginning)\s+(?:(?P<m>\d{{1,2}})/(?P<d>\d{{1,2}})(?:/(?P<y>\d{{2,4}}))?"
            rf"|(?P<month>{_MONTH_NAME})\.?\s+(?P<day>\d{{1,2}}),?\s*(?P<year>{_YEAR})?)",
            re.I,
        ),
    ),
    # "first quarter 2018", "fourth quarter of 2023", "SECOND QUARTER"
    (
        "quarter_word",
        re.compile(
            rf"\b(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter(?:\s+(?:of\s+)?(?P<year>{_YEAR})(?!\s*{_YEAR}\b))?",
            re.I,
        ),
    ),
    # "Q2 2018", "Q2'18", "2Q18", "2Q 2018", "2018Q2", "Q2"
    (
        "quarter_code",
        re.compile(
            rf"\b(?:(?P<year1>{_YEAR})\s*Q(?P<q1>[1-4])\b|Q(?P<q2>[1-4])\s*['’]?\s*(?P<year2>{_YEAR}|\d{{2}}(?!\d))?\b|(?P<q3>[1-4])Q\s*['’]?\s*(?P<year3>{_YEAR}|\d{{2}}(?!\d))?\b)",
            re.I,
        ),
    ),
    # "six months", "first half", "nine months", "twelve months", "full year", "FY2018", "year to date"
    (
        "ytd",
        re.compile(
            rf"\b(?:(?P<len>six|nine|twelve)\s+months|(?P<half>first\s+half|half[-\s]year|h1)|(?P<full>full[-\s]?year|twelve[-\s]months|fy|year[-\s]to[-\s]date|ytd|annual|full\s+twelve\s+months))"
            rf"(?:\s*(?:of\s+)?(?P<year>{_YEAR})(?!\s*{_YEAR}\b))?\b",
            re.I,
        ),
    ),
    # "March 31, 2022" / "Sept. 30, 2023" / "December 31"
    (
        "date",
        re.compile(rf"\b(?P<month>{_MONTH_NAME})\.?\s+(?P<day>\d{{1,2}}),?\s*(?:(?P<year>{_YEAR})(?!\s*{_YEAR}\b))?\b", re.I),
    ),
    (
        "geography",
        re.compile(
            r"\b(?P<geo>united\s+states|u\.?s\.?a?\b|domestic|international|intl\.?|int['’]l|worldwide|world\s*wide|ww\b|w\.w\.|global|"
            r"total|europe|japan|other\s+international|rest\s+of\s+(?:the\s+)?world|row\b|ex[-\s]?u\.?s\.?)",
            re.I,
        ),
    ),
    (
        "change",
        re.compile(
            r"%\s*change|\$\s*change|\b(?:percent(?:age)?\s+change|dollar\s+change|change|reported|operational|currency|"
            r"nom\.?\s*%?|ex[-\s]?exch\.?\s*%?|growth|constant\s+currenc\w*|local\s+currenc\w*|\bcer\b|variance|var\.|"
            r"incr(?:ease)?/\(?decr(?:ease)?\)?)(?!\w)|%(?!\d)",
            re.I,
        ),
    ),
    ("year", re.compile(rf"\b(?P<year>{_YEAR})\b")),
)

_GEO_CANONICAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(united\s+states|u\.?s\.?a?\.?|domestic)$", re.I), "United States"),
    (re.compile(r"^(worldwide|world\s*wide|ww|w\.w\.|global|total)$", re.I), "Worldwide"),
    (re.compile(r"^(international|intl\.?|int['’]l|rest\s+of\s+(?:the\s+)?world|row|ex[-\s]?u\.?s\.?|other\s+international)$", re.I), "International"),
    (re.compile(r"^europe$", re.I), "Europe"),
    (re.compile(r"^japan$", re.I), "Japan"),
)


def canonical_geography(label: str) -> str | None:
    text = label.strip().strip(":").strip()
    for pattern, name in _GEO_CANONICAL:
        if pattern.match(text):
            return name
    return None


_GEO_LABEL_RE = re.compile(
    r"^(?P<geo>united\s+states|u\.?s\.?a?\.?|domestic|international|intl\.?|int['’]l|worldwide|world\s*wide|ww|w\.w\.|global|total|europe|japan|other\s+international|rest\s+of\s+(?:the\s+)?world|ex[-\s]?u\.?s\.?)$",
    re.I,
)
_GEO_SUFFIX_RE = re.compile(
    r"^(?P<label>.*?)\s*[-–—:(]+\s*(?P<geo>united\s+states|u\.?s\.?a?\.?|domestic|international|intl\.?|int['’]l|worldwide|world\s*wide|ww|global|europe|japan|other\s+international|rest\s+of\s+(?:the\s+)?world|ex[-\s]?u\.?s\.?)\s*\)?$",
    re.I,
)
_GEO_TAIL_RE = re.compile(
    r"^(?P<label>.+?)\s+(?P<geo>united\s+states|u\.?s\.?a?\.?|domestic|international|intl\.?|int['’]l|worldwide|world\s*wide|ww|global)$",
    re.I,
)
_GEO_HEAD_RE = re.compile(
    r"^(?P<geo>total|worldwide|u\.?s\.?|international|intl\.?)\s+(?P<label>.+)$",
    re.I,
)


def split_geography(label: str) -> tuple[str, str | None]:
    """(label without its geography, geography) for a row label.

    "Biktarvy – U.S." and "OPSUMIT US" both carry the geography inside the
    label; "Intl" and "WW" are nothing but geography and belong to whatever
    product row preceded them.
    """
    text = label.strip()
    if _GEO_LABEL_RE.match(text):
        return "", canonical_geography(text)
    for pattern in (_GEO_SUFFIX_RE, _GEO_TAIL_RE):
        match = pattern.match(text)
        if match:
            geo = canonical_geography(match.group("geo"))
            if geo:
                return match.group("label").strip(" -–—:"), geo
    match = _GEO_HEAD_RE.match(text)
    if match and match.group("geo").lower() != "total":
        geo = canonical_geography(match.group("geo"))
        if geo:
            return match.group("label").strip(), geo
    return text, None


# --------------------------------------------------------------------------
# Header tokens
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HeaderToken:
    kind: str
    text: str
    start: int
    months: int | None = None      # period length in months
    end_month: int | None = None   # month the period ends
    quarter: int | None = None
    year: int | None = None
    geography: str | None = None
    covers: tuple[tuple | None, tuple | None] | None = None  # ((y, m, d) | None) bounds


def _two_digit_year(text: str | None) -> int | None:
    if not text:
        return None
    if len(text) == 2:
        return 2000 + int(text)
    return int(text)


def tokenize_header(text: str) -> list[HeaderToken]:
    """Every vocabulary token the header text contains, in reading order."""
    found: list[HeaderToken] = []
    claimed: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in claimed)

    for kind, pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            if not free(match.start(), match.end()):
                continue
            groups = match.groupdict()
            token: HeaderToken | None = None
            if kind == "months_ended":
                month = MONTHS.get((groups.get("month") or "").lower())
                if not month:
                    continue
                if groups.get("fy"):
                    months = 12
                elif groups.get("qword"):
                    months = 3
                else:
                    months = _LENGTH_WORDS[groups["len"].lower()]
                token = HeaderToken(
                    kind, match.group(0), match.start(), months=months, end_month=month,
                    quarter=quarter_of_month(month) if months == 3 else None,
                    year=int(groups["year"]) if groups.get("year") else None,
                )
            elif kind == "months_bare":
                months = 12 if groups.get("fy") else _LENGTH_WORDS[groups["len"].lower()]
                token = HeaderToken(kind, match.group(0), match.start(), months=months)
            elif kind == "partial":
                if groups.get("m"):
                    m, d = int(groups["m"]), int(groups["d"])
                    y = _two_digit_year(groups.get("y"))
                else:
                    m = MONTHS.get((groups.get("month") or "").lower())
                    d = int(groups["day"])
                    y = int(groups["year"]) if groups.get("year") else None
                if not m or not 1 <= d <= 31:
                    continue
                direction = groups["dir"].lower()
                point = (y, m, d)
                covers = (None, point) if direction in {"through", "thru", "to"} else (point, None)
                token = HeaderToken(kind, match.group(0), match.start(), covers=covers, end_month=m)
            elif kind == "quarter_word":
                quarter = _ORDINALS[groups["ord"].lower()]
                token = HeaderToken(
                    kind, match.group(0), match.start(), months=3, quarter=quarter,
                    end_month=quarter * 3, year=int(groups["year"]) if groups.get("year") else None,
                )
            elif kind == "quarter_code":
                quarter = int(groups.get("q1") or groups.get("q2") or groups.get("q3"))
                year = _two_digit_year(groups.get("year1") or groups.get("year2") or groups.get("year3"))
                token = HeaderToken(
                    kind, match.group(0), match.start(), months=3, quarter=quarter,
                    end_month=quarter * 3, year=year,
                )
            elif kind == "ytd":
                if groups.get("len"):
                    months = _LENGTH_WORDS[groups["len"].lower()]
                elif groups.get("half"):
                    months = 6
                else:
                    months = 12
                token = HeaderToken(
                    kind, match.group(0), match.start(), months=months,
                    end_month=months if months < 12 else 12,
                    year=int(groups["year"]) if groups.get("year") else None,
                )
            elif kind == "date":
                month = MONTHS.get((groups.get("month") or "").lower())
                if not month:
                    continue
                token = HeaderToken(
                    kind, match.group(0), match.start(), months=3, end_month=month,
                    quarter=quarter_of_month(month),
                    year=int(groups["year"]) if groups.get("year") else None,
                )
            elif kind == "geography":
                geo = canonical_geography(groups["geo"])
                if not geo:
                    continue
                word = match.group(0).strip().lower()
                if word == "total":
                    # "total revenues", "components of total ..." - a word in
                    # a sentence, not the label of a column.
                    after = text[match.end() : match.end() + 20].lstrip().lower()
                    before = text[max(0, match.start() - 4) : match.start()].lower()
                    if re.match(r"(?:net\b|product|revenue|sales|assets|liabilit|other|cost)", after) or before.strip().endswith("of"):
                        continue
                token = HeaderToken(kind, match.group(0), match.start(), geography=geo)
            elif kind == "change":
                token = HeaderToken(kind, match.group(0), match.start())
            elif kind == "year":
                token = HeaderToken(kind, match.group(0), match.start(), year=int(groups["year"]))
            if token is None:
                continue
            found.append(token)
            claimed.append((match.start(), match.end()))
    found.sort(key=lambda t: t.start)
    return found


# --------------------------------------------------------------------------
# Column layouts
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


def _period_tokens(tokens: list[HeaderToken]) -> list[HeaderToken]:
    """Period tokens, with bare lengths paired to the dates that complete them.

    "Three Months Ended | Six Months Ended | June 30, | June 30," states two
    lengths and then the two month-ends they share; one bare length before a
    run of dates ("Three Months Ended | March 31, 2022 | June 30, 2022 ...")
    applies to every date. Pairing is by order, never by proximity guesses.
    """
    kinds = {"months_ended", "quarter_word", "quarter_code", "ytd", "date", "months_bare"}
    periods = [t for t in tokens if t.kind in kinds]
    if any(t.kind == "months_bare" for t in periods):
        # "Three Months Ended | Six Months Ended June 30," - the month after
        # the second phrase is the first of a run of dates shared by both, so
        # a phrase-with-month is split back into a phrase and a date.
        split: list[HeaderToken] = []
        for token in periods:
            if token.kind == "months_ended" and token.year is None and token.months:
                split.append(replace(token, kind="months_bare", end_month=None, quarter=None))
                split.append(
                    HeaderToken("date", token.text, token.start + 1, months=3, end_month=token.end_month,
                                quarter=quarter_of_month(token.end_month or 3))
                )
            else:
                split.append(token)
        periods = sorted(split, key=lambda t: t.start)
    bare = [t for t in periods if t.kind == "months_bare"]
    dates = [t for t in periods if t.kind == "date"]
    if bare and dates:
        resolved: list[HeaderToken] = []
        if len(bare) == len(dates):
            pairs = dict(zip((id(d) for d in dates), bare))
            for token in periods:
                if token.kind == "months_bare":
                    continue
                if token.kind == "date":
                    length = pairs[id(token)]
                    months = length.months or 3
                    token = replace(
                        token, kind="months_ended", months=months,
                        quarter=quarter_of_month(token.end_month) if months == 3 else None,
                    )
                resolved.append(token)
            return resolved
        if len(bare) == 1:
            months = bare[0].months or 3
            for token in periods:
                if token.kind == "months_bare":
                    continue
                if token.kind == "date":
                    token = replace(
                        token, kind="months_ended", months=months,
                        quarter=quarter_of_month(token.end_month) if months == 3 else None,
                    )
                resolved.append(token)
            return resolved
    # A date with no length phrase anywhere in the header is a point in time
    # (a balance-sheet column), not a reporting period; it names no column.
    if dates and not bare and not any(t.kind == "months_ended" for t in periods):
        periods = [t for t in periods if t.kind != "date"]
    # A bare length with no date at all still names a period length; it is
    # kept so that year runs can attach to it ("Three Months Ended" + "2016 2015"
    # when the month sits in a caption the grid lost).
    return [t if t.kind != "months_bare" else replace(t, kind="ytd" if t.months != 3 else "quarter_word") for t in periods]


def _runs(tokens: list[HeaderToken], kind: str) -> list[list[HeaderToken]]:
    """Maximal runs of one token kind, broken by any other kind in between."""
    runs: list[list[HeaderToken]] = []
    current: list[HeaderToken] = []
    for token in tokens:
        if token.kind == kind:
            current.append(token)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _change_count_after(tokens: list[HeaderToken], position: int, until: int) -> int:
    return sum(1 for t in tokens if t.kind == "change" and position < t.start < until)


def _bare_period_word(token: HeaderToken) -> bool:
    """A period token that only names a length ("Three Months Ended" with no month)."""
    return token.kind == "ytd" and token.year is None


def _dedupe_repeated_periods(periods: list[HeaderToken]) -> list[HeaderToken]:
    """A header repeated for every page restates the same periods; keep one copy."""
    signature = [(t.kind, t.months, t.end_month, t.quarter, t.year) for t in periods]
    n = len(signature)
    for size in range(1, n // 2 + 1):
        if n % size == 0 and all(signature[i] == signature[i % size] for i in range(n)):
            return periods[:size]
    return periods


def _nested_sequence(tokens: list[HeaderToken], repeats: int) -> list[HeaderToken] | None:
    """The block of period labels that, repeated once per year, makes the columns.

    "1Q 2Q 3Q 4Q Full Year" printed under each of two years is that block
    twice. Labels left over in front of it ("4Q Full Year" above the change
    columns) must each name a member of the block, otherwise the header is
    not this shape.
    """
    signature = [(t.months, t.quarter) for t in tokens]
    for prefix in range(0, len(tokens)):
        remaining = len(tokens) - prefix
        if remaining % repeats or remaining == 0:
            continue
        size = remaining // repeats
        body = signature[prefix:]
        if all(body[i] == body[i % size] for i in range(len(body))):
            block = tokens[prefix : prefix + size]
            if all(signature[i] in body[:size] for i in range(prefix)):
                return block
    return None


def _infer_years_for_sequence(
    periods: list[HeaderToken], year_candidates: list[int]
) -> list[list[int | None]]:
    """Years for a run of quarter labels that carry none of their own.

    A retrospective grid lists "Q2 Q1 Q4 Q3 Q2 Q1 Full Year" and states the
    years once, elsewhere. Quarters in strict order can only change year at
    a Q4/Q1 boundary, so the sequence pins every year once one is known; the
    years the document names are the only anchors tried, and every anchor
    that reproduces exactly that set of years is returned.
    """
    quarters = [t.quarter for t in periods]
    if not quarters or all(q is None for q in quarters):
        return []
    results: list[list[int | None]] = []
    for direction in (1, -1):
        for anchor in sorted(set(year_candidates)):
            years: list[int | None] = []
            year = anchor
            previous: int | None = None
            ok = True
            for token in periods:
                q = token.quarter
                if token.months == 12:
                    years.append(None)  # resolved after the quarters
                    continue
                if q is None:
                    ok = False
                    break
                if previous is not None:
                    step = q - previous
                    if direction == 1 and step < 0:
                        year += 1
                    elif direction == 1 and step > 1:
                        ok = False
                        break
                    elif direction == -1 and step > 0:
                        year -= 1
                    elif direction == -1 and step < -1:
                        ok = False
                        break
                    elif step == 0:
                        ok = False
                        break
                years.append(year)
                previous = q
            if not ok:
                continue
            used = {y for y in years if y is not None}
            if used != set(year_candidates):
                continue
            # A full-year column belongs to the year the grid states completely.
            complete = [
                y for y in sorted(used)
                if {t.quarter for t, yy in zip(periods, years) if yy == y and t.quarter} == {1, 2, 3, 4}
            ]
            filled = [
                (complete[-1] if complete else max(used)) if y is None else y for y in years
            ]
            if filled not in results:
                results.append(filled)
    return results


def _expand_geography(columns: list[ColumnSpec], geo_runs: list[list[HeaderToken]]) -> list[ColumnSpec]:
    """Nest a repeated geography run under each value column."""
    if not geo_runs:
        return columns
    values = [c for c in columns if c.kind == "value"]
    for run in geo_runs:
        geos = [t.geography for t in run]
        # A nesting names at least two different geographies; "total" on
        # its own is a word in a caption, not a column.
        if len(geos) < 2 or len(set(geos)) < 2:
            continue
        if all(t.text.strip().lower() == "total" for t in run):
            continue
        # The run either repeats once per value column, or lists the whole
        # nesting at once (len == values * cycle). A stray label before the
        # repetition begins is prose, and is dropped until the rest repeats.
        for skip in range(0, len(geos) - 1):
            geos_trimmed = geos[skip:]
            found = False
            for cycle in range(2, len(geos_trimmed) + 1):
                if len(geos_trimmed) % cycle:
                    continue
                pattern = geos_trimmed[:cycle]
                if len(set(pattern)) < 2 or geos_trimmed != pattern * (len(geos_trimmed) // cycle):
                    continue
                if len(geos_trimmed) // cycle in {1, len(values)}:
                    found = True
                    break
            if found:
                geos = geos_trimmed
                break
        for cycle in range(2, len(geos) + 1):
            if len(geos) % cycle:
                continue
            pattern = geos[:cycle]
            if geos != pattern * (len(geos) // cycle):
                continue
            if len(geos) // cycle in {1, len(values)}:
                expanded: list[ColumnSpec] = []
                for column in columns:
                    if column.kind != "value":
                        expanded.append(column)
                        continue
                    for geo in pattern:
                        expanded.append(replace(column, geography=geo, label=f"{column.label} {geo}".strip()))
                return expanded
    return columns


def _attach_partials(columns: list[ColumnSpec], partials: list[HeaderToken]) -> list[ColumnSpec]:
    """A "through 6/15" marker bounds the value column whose period contains it."""
    if not partials:
        return columns
    out = list(columns)
    for partial in partials:
        start, end = partial.covers or (None, None)
        bound = end or start
        if not bound:
            continue
        year_hint, month, day = bound
        candidates = [
            i for i, c in enumerate(out)
            if c.kind == "value" and c.months == 3 and c.end_month and c.year
            and (c.end_month - 2) <= month <= c.end_month
            and (year_hint is None or c.year == year_hint)
        ]
        if not candidates:
            continue
        # The most recent matching quarter is the one an issuer cuts short.
        index = max(candidates, key=lambda i: (out[i].year, out[i].end_month))
        column = out[index]
        quarter_start = f"{column.year}-{column.end_month - 2:02d}-01"
        if end:
            covers = (quarter_start, f"{column.year}-{month:02d}-{day:02d}")
        else:
            from calendar import monthrange
            last = monthrange(column.year, column.end_month)[1]
            covers = (f"{column.year}-{month:02d}-{day:02d}", f"{column.year}-{column.end_month:02d}-{last:02d}")
        out[index] = replace(column, covers=covers)
    return out


def build_layouts(
    header_text: str,
    *,
    context: str = "",
    year_candidates: list[int] | None = None,
) -> list[ColumnLayout]:
    """Every column layout the header can be read as.

    Usually one. More than one comes back only when the header genuinely
    underdetermines the columns (a quarter sequence with no year attached,
    say), and the row constraints decide between them.
    """
    tokens = tokenize_header(header_text)
    unit_label, unit_declared = detect_unit([[header_text]], context)
    currency, currency_declared = detect_currency([[header_text]], context)
    notes: list[str] = []
    if not unit_declared:
        notes.append("unit_not_declared")
    if not currency_declared:
        notes.append("currency_not_declared")

    periods = _period_tokens(tokens)
    year_runs = _runs([t for t in tokens if t.kind in {"year", "change", "geography", "months_ended", "quarter_word", "quarter_code", "ytd", "date"}], "year")
    geo_runs = _runs(tokens, "geography")
    partials = [t for t in tokens if t.kind == "partial"]
    change_tokens = [t for t in tokens if t.kind == "change"]
    text_end = len(header_text) + 1

    def finish(columns: list[ColumnSpec], extra_notes: list[str] = []) -> ColumnLayout:
        columns = _expand_geography(columns, geo_runs)
        columns = _attach_partials(columns, partials)
        return ColumnLayout(
            columns=tuple(columns),
            unit_label=unit_label,
            currency=currency,
            unit_declared=unit_declared,
            currency_declared=currency_declared,
            notes=tuple(notes + extra_notes),
        )

    layouts: list[ColumnLayout] = []

    # Case A: every period token carries its own year (dates, "Q2 2018",
    # "three months ended June 30, 2024"). Columns are the periods in order.
    dated = [t for t in periods if t.year is not None]
    stray_years = {t.year for run in year_runs for t in run} <= {t.year for t in dated}
    if periods and len(dated) == len(periods) and (not year_runs or stray_years):
        # A caption above the header restates periods the header then lists;
        # the header is the last run of distinct periods.
        keys = [(t.months, t.end_month, t.year) for t in periods]
        start = 0
        for index in range(len(keys)):
            if keys[index] in keys[index + 1 :]:
                start = index + 1
        periods = periods[start:]
        columns: list[ColumnSpec] = []
        for index, token in enumerate(periods):
            columns.append(ColumnSpec("value", token.months, token.end_month, token.year, label=token.text))
            until = periods[index + 1].start if index + 1 < len(periods) else text_end
            for _ in range(_change_count_after(tokens, token.start, until)):
                columns.append(ColumnSpec("change", label="change"))
        return [finish(columns)]

    # Case B: period phrases without years, plus one or more runs of years.
    # "Three Months Ended June 30," + "2024 2023" is the common filing shape;
    # "SECOND QUARTER SIX MONTHS" + "2018 2017 ... 2018 2017 ..." nests a run
    # of years under each period.
    undated = [t for t in periods if t.year is None]
    if undated and year_runs:
        first_year_start = year_runs[0][0].start
        # Period words printed before the first year run and repeated after
        # it label change columns ("4Q Full Year" above "Nom % Ex-Exch %"),
        # not value columns.
        after = [t for t in undated if t.start > first_year_start]
        before = [t for t in undated if t.start < first_year_start]
        if after and before and all(
            any((b.months, b.quarter) == (a.months, a.quarter) for a in after) for b in before
        ):
            undated = after
        years_flat_all = [t for run in year_runs for t in run]
        codes = [t for t in undated if t.kind in {"quarter_code", "quarter_word", "ytd"}]
        if codes and len(codes) == len(undated) and len(year_runs) == 1 and len(undated) > len(years_flat_all):
            nested = _nested_sequence(undated, len(years_flat_all))
            if nested is not None:
                columns = []
                for year_index, year_token in enumerate(years_flat_all):
                    for token in nested:
                        columns.append(
                            ColumnSpec("value", token.months, token.end_month, year_token.year, label=token.text)
                        )
                for _ in change_tokens:
                    columns.append(ColumnSpec("change", label="change"))
                return [finish(columns)]
        phrases = _dedupe_repeated_periods(undated)
        factor = len(undated) // max(len(phrases), 1)
        if factor > 1 and len(year_runs) == factor * len(phrases):
            signature = [tuple(t.year for t in run) for run in year_runs]
            size = len(phrases)
            if all(signature[i] == signature[i % size] for i in range(len(signature))):
                year_runs = year_runs[:size]
        # Two "Three Months Ended" tokens plus dated month tokens describe
        # a retrospective grid: handled as case A once the dates are paired.
        dated_months = [t for t in periods if t.kind == "date"]
        if dated_months and len(dated_months) >= 2 and all(t.year for t in dated_months):
            columns = []
            length = next((p.months for p in phrases if p.months), 3)
            for token in dated_months:
                columns.append(ColumnSpec("value", length, token.end_month, token.year, label=token.text))
            return [finish(columns)]
        years_flat = [t for run in year_runs for t in run]
        if len(year_runs) == len(phrases):
            groups = list(zip(phrases, year_runs))
        elif len(year_runs) == 1 and len(years_flat) % len(phrases) == 0:
            per = len(years_flat) // len(phrases)
            groups = [(phrase, years_flat[i * per : (i + 1) * per]) for i, phrase in enumerate(phrases)]
        elif len(phrases) == 1:
            groups = [(phrases[0], years_flat)]
        elif len(years_flat) % len(phrases) == 0:
            per = len(years_flat) // len(phrases)
            groups = [(phrase, years_flat[i * per : (i + 1) * per]) for i, phrase in enumerate(phrases)]
        else:
            groups = []
            notes.append(f"unmapped_columns years={len(years_flat)} periods={len(phrases)}")
        if groups:
            columns = []
            # Change markers printed between the period phrases ("Three
            # Months Ended ... Dollar Change Percentage Change Year Ended
            # ...") belong to the phrase they follow; markers after a run of
            # years belong to that run. Whichever the header uses, each
            # group gets its own change columns.
            between: list[int] = []
            for index, phrase in enumerate(phrases):
                until = phrases[index + 1].start if index + 1 < len(phrases) else (
                    year_runs[0][0].start if year_runs and year_runs[0][0].start > phrase.start else text_end
                )
                between.append(_change_count_after(tokens, phrase.start, until))
            for index, (phrase, years) in enumerate(groups):
                last_year_start = years[-1].start if years else phrase.start
                next_start = text_end
                for run in year_runs:
                    if run[0].start > last_year_start:
                        next_start = min(next_start, run[0].start)
                for year_token in years:
                    columns.append(
                        ColumnSpec("value", phrase.months, phrase.end_month, year_token.year, label=f"{phrase.text} {year_token.year}")
                    )
                after_years = _change_count_after(tokens, last_year_start, next_start)
                count = after_years if after_years else between[index]
                for _ in range(count):
                    columns.append(ColumnSpec("change", label="change"))
            # Change markers printed before any year run (a "% Change"
            # column heading above the years) are counted once at the end.
            leading = _change_count_after(tokens, -1, year_runs[0][0].start) if year_runs else 0
            trailing_declared = sum(1 for c in columns if c.kind == "change")
            if leading and not trailing_declared:
                for _ in range(leading):
                    columns.append(ColumnSpec("change", label="change"))
            return [finish(columns)]

    # Case C: a run of quarter labels and no years at all in the header. The
    # document's own years are the only anchors.
    if undated and not year_runs and year_candidates:
        sequences = _infer_years_for_sequence(undated, year_candidates)
        for years in sequences:
            columns = [
                ColumnSpec("value", t.months, t.end_month if t.months != 12 else 12, y, label=t.text)
                for t, y in zip(undated, years)
            ]
            for _ in change_tokens:
                columns.append(ColumnSpec("change", label="change"))
            layouts.append(finish(columns, ["years_inferred_from_sequence"]))
        if layouts:
            return layouts

    # Case D: only years. "2013 2012 2011" over an annual table.
    if not periods and year_runs:
        years_flat = [t for run in year_runs for t in run]
        columns = [ColumnSpec("value", 12, 12, t.year, label=t.text) for t in years_flat]
        for _ in change_tokens:
            columns.append(ColumnSpec("change", label="change"))
        return [finish(columns, ["annual_assumed_from_bare_years"])]

    return [finish([], ["no_period_header"])]


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Cell:
    value: float | None      # None for a placeholder
    percent: bool = False
    text: str = ""


_NUMBER_CORE_RE = re.compile(r"^(?P<neg>[\(\-–−])?\$?\s?(?P<num>[\d,]*\.?\d+)\)?(?P<pct>%)?$")


def parse_cell(token: str) -> Cell | None:
    text = token.strip()
    if is_placeholder_token(text):
        return Cell(None, text=text)
    match = _NUMBER_CORE_RE.match(text)
    if not match:
        return None
    value = float(match.group("num").replace(",", ""))
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


def _change_ok(current: float | None, prior: float | None, cell: Cell) -> bool | None:
    """True/False when checkable, None when the change cannot be computed."""
    if cell.value is None:
        return None
    if current is None or prior is None or prior == 0:
        return None
    expected = (current - prior) / abs(prior) * 100.0
    difference = current - prior
    if not cell.percent and abs(abs(cell.value) - abs(difference)) <= 0.051 + 0.0005 * abs(difference):
        return True
    if abs(expected) > 100 and abs(cell.value) > 100:
        return True
    return abs(abs(cell.value) - abs(expected)) <= _PERCENT_TOLERANCE + 0.005 * abs(expected)


def _tolerance(parts: int) -> float:
    return 0.5 * (parts + 1) + 0.01


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
        if cell is not None and cell.value is not None and cell.value < 0 and columns[index].kind == "value":
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
        if not changes or len(group) < 2:
            continue
        current, prior = value_at(group[0]), value_at(group[1])
        results = [
            _change_ok(current, prior, placed[c]) for c in changes if placed[c] is not None
        ]
        results = [r for r in results if r is not None]
        if results:
            change_checked = True
            if not any(results):
                return False, []
            verified.append("change_column")

    # Geography parts sum to their total within the same period.
    by_period: dict[tuple[int | None, int | None, int | None], dict[str, float]] = {}
    for index, column in enumerate(columns):
        if column.kind != "value" or column.geography is None:
            continue
        value = value_at(index)
        if value is None:
            continue
        by_period.setdefault((column.months, column.end_month, column.year), {})[column.geography] = value
    for parts in by_period.values():
        total = parts.get("Worldwide")
        components = [v for g, v in parts.items() if g != "Worldwide"]
        if total is not None and len(components) >= 2:
            if abs(sum(components) - total) > _tolerance(len(components)):
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
