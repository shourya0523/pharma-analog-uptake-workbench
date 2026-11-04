"""Quarters no filing prints, from year-to-date figures that filings do.

A fourth quarter is never filed as a quarter: it is the 10-K's year less the
nine months the third-quarter 10-Q states. A second or third quarter whose
10-Q tags only the year to date is that figure less the previous 10-Q's.

The previous period is subtracted as the filer stated it, not as a sum of the
quarters inside it. An issuer rounds each published period on its own, so a
stated nine months can differ from its three quarters added up by a unit of
the last digit; the stated figure is the one the issuer's own arithmetic
uses. The sum of quarters is used only when no stated year to date exists, and
the result says so.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from app.retrieval.xbrl import XbrlAnswer, declared_slack, first_pass, in_scope, quarter_end, read_span, sec_get

# The largest share of a derived quarter its inputs' declared rounding may
# account for. A year tagged as "approximately $2.3 billion" (decimals -8)
# leaves a quarter uncertain by tens of millions; such a quarter is refused.
MAX_ROUNDING_SHARE = 0.01

# Length of a year to date ending after the nth quarter, in days. Wide enough
# for 52/53-week fiscal calendars, narrow enough that no two overlap.
YTD_DAYS = {1: range(80, 100), 2: range(170, 195), 3: range(260, 285), 4: range(350, 380)}


@dataclass
class Derived:
    """A quarter computed from two stated periods, with both inputs kept.

    `status`: derived | missing_input | inputs_differ | negative |
    imprecise_input | no_sales (both inputs zero: the product had none to
    report yet, which is not a figure of zero sales). A first quarter is six months less the second quarter,
    both stated in the second-quarter 10-Q (method six_months_less_q2).
    `method`: full_year_less_nine_months | ytd_less_previous_ytd |
    full_year_less_quarters (each quarter stated, the sum is ours - can be a
    unit of the last digit off the issuer's own arithmetic).
    """

    status: str
    product: str
    period: str
    value: float | None = None
    unit: str | None = None
    method: str | None = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    # How far the result may sit from the exact figure: half a unit of each
    # input's declared last place, summed. A quarter the issuer printed itself
    # can differ from the derived one by up to this much.
    plus_minus: float | None = None


def _period_end(year: int, n: int) -> dt.date:
    month = n * 3
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def year_to_date(product: str, year: int, n: int, issuer: str | None, *, get: Callable, confirm: Callable | None,
                 family: bool = False) -> XbrlAnswer:
    """The stated year to date through calendar quarter n: the 10-K's year for
    n = 4, the nth 10-Q's year to date otherwise."""
    form = "10-K" if n == 4 else "10-Q"
    return read_span(product, f"YTD{n} {year}", _period_end(year, n), form, YTD_DAYS[n], issuer, get=get, confirm=confirm,
                     family=family)


def _input(answer: XbrlAnswer) -> dict[str, Any]:
    keep = ("status", "period", "value", "unit", "decimals", "concept", "member", "match", "period_start",
            "period_end", "filer", "accession", "filing_url")
    return {k: v for k, v in asdict(answer).items() if k in keep}


def _same_line(a: XbrlAnswer, b: XbrlAnswer) -> bool:
    """Whether two inputs are the same reported line: one filer, one member
    (or both the single-product line), one unit."""
    return a.cik == b.cik and a.unit == b.unit and a.match == b.match and (a.member or "") == (b.member or "")


def derive_quarter(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    get: Callable = sec_get,
    confirm: Callable | None = None,
    scope: str = "worldwide",
    family: bool = False,
) -> Derived:
    """Compute calendar quarter `period` ("Q4 2023") as a stated year to date
    less the stated year to date before it, both at `scope` (see `in_scope`)."""
    end = quarter_end(period)
    if end is None:
        return Derived("missing_input", product, period, reason="not a calendar quarter")
    n, year = end.month // 3, end.year
    if n == 1:
        return _first_quarter(product, period, year, issuer, get=get, confirm=confirm, scope=scope, family=family)

    current = in_scope(year_to_date(product, year, n, issuer, get=get, confirm=confirm, family=family), scope)
    if current.status != "found":
        return Derived("missing_input", product, period, inputs=[_input(current)],
                       reason=f"year to date through Q{n}: {current.status}")
    previous = in_scope(year_to_date(product, year, n - 1, issuer, get=get, confirm=confirm, family=family), scope)
    method = "full_year_less_nine_months" if n == 4 else "ytd_less_previous_ytd"
    inputs = [_input(current), _input(previous)]

    if previous.status != "found" and n == 4:
        quarters = [in_scope(first_pass(product, f"Q{q} {year}", issuer, get=get, confirm=confirm, family=family), scope)
                    for q in (1, 2, 3)]
        inputs = [_input(current)] + [_input(q) for q in quarters]
        if any(q.status != "found" for q in quarters):
            return Derived("missing_input", product, period, inputs=inputs,
                           reason="neither nine months nor all three quarters are stated")
        if not all(_same_line(current, q) for q in quarters):
            return Derived("inputs_differ", product, period, inputs=inputs,
                           reason="the year and the quarters are not the same reported line")
        value = current.value - sum(q.value for q in quarters)
        method = "full_year_less_quarters"
        used = [current, *quarters]
    elif previous.status != "found":
        return Derived("missing_input", product, period, inputs=inputs,
                       reason=f"year to date through Q{n - 1}: {previous.status}")
    else:
        if not _same_line(current, previous):
            return Derived("inputs_differ", product, period, inputs=inputs,
                           reason="the two periods are not the same reported line")
        value = current.value - previous.value
        used = [current, previous]

    rounding = sum(declared_slack(a.decimals) for a in used)
    if value > 0 and rounding > MAX_ROUNDING_SHARE * value:
        return Derived("imprecise_input", product, period, value=value, unit=current.unit, method=method,
                       inputs=inputs, reason=f"the inputs' declared rounding (up to {rounding:,.0f}) is too coarse "
                                             f"for a quarter of {value:,.0f}")
    if value < 0:
        return Derived("negative", product, period, value=value, unit=current.unit, method=method, inputs=inputs,
                       reason="a later year to date smaller than the earlier one: a restatement or a change of scope")
    if all(a.value == 0 for a in used):
        return Derived("no_sales", product, period, method=method, inputs=inputs,
                       reason="every input is zero: nothing was sold to report")
    return Derived("derived", product, period, value=value, unit=current.unit, method=method, inputs=inputs,
                   plus_minus=rounding)


def _first_quarter(product: str, period: str, year: int, issuer: str | None, *, get: Callable,
                   confirm: Callable | None, scope: str, family: bool = False) -> Derived:
    """Q1 as the second-quarter 10-Q's six months less its own second quarter."""
    six = in_scope(year_to_date(product, year, 2, issuer, get=get, confirm=confirm, family=family), scope)
    q2 = in_scope(first_pass(product, f"Q2 {year}", issuer, get=get, confirm=confirm, family=family), scope)
    inputs = [_input(six), _input(q2)]
    method = "six_months_less_q2"
    if six.status != "found" or q2.status != "found":
        return Derived("missing_input", product, period, inputs=inputs, method=method,
                       reason=f"six months: {six.status}; second quarter: {q2.status}")
    if not _same_line(six, q2):
        return Derived("inputs_differ", product, period, inputs=inputs, method=method,
                       reason="the six months and the quarter are not the same reported line")
    value = six.value - q2.value
    if six.value == 0 and q2.value == 0:
        return Derived("no_sales", product, period, method=method, inputs=inputs,
                       reason="every input is zero: nothing was sold to report")
    rounding = declared_slack(six.decimals) + declared_slack(q2.decimals)
    if value < 0:
        return Derived("negative", product, period, value=value, unit=six.unit, method=method, inputs=inputs)
    if value > 0 and rounding > MAX_ROUNDING_SHARE * value:
        return Derived("imprecise_input", product, period, value=value, unit=six.unit, method=method, inputs=inputs)
    return Derived("derived", product, period, value=value, unit=six.unit, method=method, inputs=inputs,
                   plus_minus=rounding)


def xbrl_quarter(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    get: Callable = sec_get,
    confirm: Callable | None = None,
    scope: str = "worldwide",
    family: bool = False,
) -> XbrlAnswer | Derived:
    """The quarter from XBRL: read where a 10-Q tags it, derived where none does.

    Derivation is tried for a calendar Q4 the issuer's 10-Qs do not give (most
    filers end their year in December, so none files one) and when the
    issuer's 10-Q gives no figure for the three months alone. Failing that, a
    Q1-Q3 is read from the comparative column of the same quarter's 10-Q a
    year on (`comparative` set on the answer).
    `scope` is "worldwide" or "us"; see `in_scope`. `family` sums the
    members naming the brand's forms (see `xbrl.member_match`).
    """
    answer = first_pass(product, period, issuer, get=get, confirm=confirm, family=family)
    end = quarter_end(period)
    q4_without_10q = end is not None and end.month == 12 and answer.status != "found"
    if q4_without_10q or answer.status in ("no_revenue_fact", "no_member", "no_filing"):
        derived = derive_quarter(product, period, issuer, get=get, confirm=confirm, scope=scope, family=family)
        if derived.status == "derived":
            return derived
        if end is not None and not q4_without_10q:
            later = in_scope(read_span(product, period, end, "10-Q", YTD_DAYS[1], issuer, get=get, confirm=confirm,
                                       filing_end=dt.date(end.year + 1, end.month, end.day), family=family), scope)
            if later.status == "found":
                later.comparative = True
                return later
        if q4_without_10q:
            return derived
    return in_scope(answer, scope)
