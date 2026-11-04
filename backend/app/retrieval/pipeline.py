"""One product, one calendar quarter: every route, in order, until one holds.

    quarter_revenue("Calderon", "Q2 2023", "Acme Pharma")

Routes, most exact first; the first that produces a checked figure answers:

0. Whose figure - the issuer named, or `booked_by`; with neither, the
   seller the ownership timeline finds for that quarter. A quarter in which
   the product changed hands is returned for review with both sellers.
1. XBRL - the issuer's 10-Q/10-K tags (a member named for the product, else
   a single-product filer's product line, else a quarter derived from
   stated years to date, else a later 10-Q's comparative column).
2. Earnings releases - the issuer's results exhibit read as a table grid,
   then as text, then later releases' comparative columns; failing those,
   the issuer's exhibits are read whole by Gemini (layout reader).
2b. The issuer's own results archive, when the issuer files nothing with
   the SEC.
3. Grounded search - Gemini with Google Search, kept only if the figure is
   found beside the product in the document it cites. A page that refuses
   this client yields `needs_review`, never `found`.
4. Printed years to date - two stated figures from grounded search, each
   checked, subtracted (Q2-Q4).

Routes 1 and 2 are retried under the product's FDA-history aliases (a brand
line that can only have been this form in that period).

5. Parent brand - when every route declines and the product's name starts
   with a shorter name ("Calderon XR" -> "Calderon"), the issuer's release
   line for that name answers, labelled `parent_brand`: the brand's forms
   are reported under one line. Every route's result
is kept in `attempts`; a figure another filer reports for the product (a
partner, a licensee) is kept in `alternatives` and never becomes the answer
when an issuer is named. A spent Gemini quota stops the route that hit it and
is recorded; the routes after it still run.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

from app import fx
from app.retrieval.derive import Derived, xbrl_quarter
from app.retrieval.earnings import read_earnings
from app.retrieval.gemini_search import QuotaExceeded, ask_revenue
from app.retrieval.identity import aliases as fda_aliases
from app.retrieval.printed_derive import SCALE, derive_from_printed
from app.retrieval.single_product import gemini_confirm
from app.retrieval.verify import verify_answer
from app.retrieval.layout import read_layout
from app.retrieval.xbrl import names_issuer, quarter_end


# Exhibits the layout reader is given per question, the issuer's first.
MAX_LAYOUT_READS = 2


@dataclass
class QuarterResult:
    """The answer for one product-quarter, and how it was reached.

    `status`: found | needs_review | not_found. `value` is in units of
    `currency`; `value_usd` converts it at the quarter's average H.10 rate.
    `route` names the route that answered; `match` carries a qualifier such as
    `combined` (the line also covers a sibling the filer names) or
    `single_product`.
    """

    status: str
    product: str
    period: str
    issuer: str | None
    value: float | None = None
    currency: str | None = None
    value_usd: float | None = None
    route: str | None = None
    match: str | None = None
    source: str | None = None
    evidence: str | None = None
    identity: dict[str, Any] | None = None
    plus_minus: float | None = None  # a derived figure's spread from the filer's declared rounding
    corroboration: str | None = None  # a second route that agreed with the answer
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _record(result: QuarterResult, route: str, outcome: Any) -> None:
    if is_dataclass(outcome):
        summary = asdict(outcome)
    elif isinstance(outcome, dict):
        summary = dict(outcome)
    else:
        summary = dict(vars(outcome))
    for extra in ("alias_error", "identity"):
        if getattr(outcome, extra, None) is not None:
            summary[extra] = getattr(outcome, extra)
    summary.pop("candidates", None)
    summary.pop("readings", None)
    summary.pop("raw", None)
    result.attempts.append({"route": route, **summary})


def _with_aliases(read: Callable, product: str, period: str, end: dt.date, describe_aliases: Callable) -> Any:
    """`read(name)` under the product's own name, then under each alias."""
    outcome = read(product)
    if _found(outcome):
        return outcome
    try:
        found_aliases = describe_aliases(product, end)
    except Exception as exc:  # an alias that cannot be established is no alias
        outcome.alias_error = f"{type(exc).__name__}: {str(exc)[:120]}"
        return outcome
    for alias in found_aliases:
        alt = read(alias["name"])
        if _found(alt):
            alt.identity = alias
            return alt
    return outcome


def _found(outcome: Any) -> bool:
    return getattr(outcome, "status", None) in ("found", "derived")


def _currency_of(unit: str | None) -> str | None:
    return unit.split(":")[-1].upper() if unit else None


def quarter_revenue(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    scope: str = "worldwide",
    line: str = "brand",
    booked_by: str | None = None,
    allow_search: bool = True,
    find_seller: bool = True,
    xbrl: Callable = xbrl_quarter,
    layout: Callable = read_layout,
    ir: Callable | None = None,
    sellers: Callable | None = None,
    earnings: Callable = read_earnings,
    search: Callable = ask_revenue,
    verify: Callable = verify_answer,
    printed: Callable = derive_from_printed,
    describe_aliases: Callable = fda_aliases,
    parent_brand: Callable | None = None,
    confirm: Callable | None = gemini_confirm,
    usd: Callable = fx.to_usd,
) -> QuarterResult:
    """Run the routes for `product` in calendar quarter `period` ("Q2 2023").

    `scope` is "worldwide" or "us"; a U.S. figure is taken only where a route
    can show it is the U.S. part (XBRL's tagged geography, a release table's
    U.S. column). `allow_search`
    turns off routes 3 and 4, which cost search fees. `line` is "brand" or
    "family" (the brand's forms summed); `booked_by` names the filer whose
    booking is wanted when partners report the product in different
    territories (see `app.retrieval.question`). The route functions are
    parameters so the order can be tested without the network.
    """
    issuer = booked_by or issuer
    result = QuarterResult("not_found", product, period, issuer)
    end = quarter_end(period)
    if end is None:
        result.attempts.append({"route": "input", "status": "not_a_calendar_quarter"})
        return result
    family = line == "family"

    def run(route: str, attempt: Callable) -> Any:
        try:
            return attempt()
        except QuotaExceeded as exc:
            result.attempts.append({"route": route, "status": "quota_exceeded", "reason": str(exc)[:200]})
        except Exception as exc:  # one route failing leaves the others to answer
            result.attempts.append({"route": route, "status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]})
        return None

    # 0. Whose figure
    if not issuer and find_seller:
        if sellers is None:
            from app.retrieval.ownership import seller_for as sellers
        owners = run("ownership", lambda: sellers(product, period))
        if owners is not None:
            named = [s for s in owners.sellers if s.filer]
            result.attempts.append({"route": "ownership", "status": owners.status,
                                    "sellers": [{"filer": s.filer, "cik": s.cik, "start": str(s.start),
                                                 "end": str(s.end), "note": s.note} for s in owners.sellers],
                                    "evidence": owners.evidence[:3]})
            if owners.status in ("split", "split_undated"):
                result.status = "needs_review"
                return result
            if owners.status == "single" and len(named) == 1:
                issuer = result.issuer = named[0].filer

    # 1. XBRL
    outcome = run("xbrl", lambda: _with_aliases(
        lambda name: xbrl(name, period, issuer, confirm=confirm, scope=scope, family=family),
        product, period, end, describe_aliases))
    if outcome is not None:
        _record(result, "xbrl", outcome)
        for c in getattr(outcome, "candidates", []) or []:
            if c.get("filer") and c.get("value") is not None:
                result.alternatives.append({"route": "xbrl", **c})
        if _found(outcome):
            derived = isinstance(outcome, Derived)
            if derived and scope == "worldwide":
                printed_figure = _printed_within(outcome, product, period, issuer, earnings, run)
                if printed_figure is not None:
                    _record(result, "earnings", printed_figure)
                    return _answer(result, end, usd, value=printed_figure.value, currency=printed_figure.currency,
                                   route="earnings", source=printed_figure.url, evidence=printed_figure.scope,
                                   corroboration=f"xbrl:{outcome.method} {outcome.value:,.0f} "
                                                 f"± {outcome.plus_minus or 0:,.0f}")
            comparative = getattr(outcome, "comparative", False)
            return _answer(result, end, usd, value=outcome.value, currency=_currency_of(outcome.unit),
                           route=f"xbrl:{outcome.method}" if derived else ("xbrl:comparative" if comparative else "xbrl"),
                           plus_minus=outcome.plus_minus if derived else None,
                           match=None if derived else outcome.match,
                           source=(outcome.inputs[0].get("filing_url") if derived and outcome.inputs else
                                   getattr(outcome, "filing_url", None)),
                           evidence=None if derived else f"{outcome.concept} {outcome.member}",
                           identity=getattr(outcome, "identity", None))

    # 2. Earnings releases; a U.S. question only from a table's U.S. column
    if scope == "us":
        outcome = run("earnings", lambda: _with_aliases(
            lambda name: earnings(name, period, issuer, scope="us"), product, period, end, describe_aliases))
        if outcome is not None:
            _record(result, "earnings", outcome)
            if _found(outcome):
                return _answer(result, end, usd, value=outcome.value, currency=outcome.currency,
                               route="earnings:later" if outcome.later else "earnings", source=outcome.url,
                               evidence=outcome.scope, identity=getattr(outcome, "identity", None))
    if scope == "worldwide":
        outcome = run("earnings", lambda: _with_aliases(
            lambda name: earnings(name, period, issuer), product, period, end, describe_aliases))
        if outcome is not None:
            _record(result, "earnings", outcome)
            for r in getattr(outcome, "readings", []) or []:
                if r.status == "accepted" and issuer and outcome.filer != r.filer:
                    result.alternatives.append({"route": "earnings", "filer": r.filer, "value": r.value,
                                                "currency": r.currency, "scope": r.row_quote, "url": r.url})
            if _found(outcome):
                return _answer(result, end, usd, value=outcome.value, currency=outcome.currency,
                               route="earnings:later" if outcome.later else "earnings", source=outcome.url,
                               evidence=outcome.scope, identity=getattr(outcome, "identity", None))
            readings = getattr(outcome, "readings", []) or []
            own = [r.url for r in readings if not issuer or names_issuer(r.filer, issuer)]
            for url in list(dict.fromkeys(own))[:MAX_LAYOUT_READS]:
                reading = run("layout", lambda url=url: layout(url, product, period))
                if reading is None:
                    continue
                _record(result, "layout", reading)
                if reading.status == "accepted":
                    return _answer(result, end, usd, value=reading.value, currency=reading.currency,
                                   route="layout", source=reading.url, evidence=reading.row_label)
            issuer_files_with_sec = any(names_issuer(r.filer, issuer) for r in readings) if issuer else True
        else:
            issuer_files_with_sec = True

        # 2b. The issuer's own results archive
        if issuer and not issuer_files_with_sec and allow_search:
            if ir is None:
                from app.retrieval.ir_archive import read_ir_quarter as ir
            site = run("ir_archive", lambda: ir(product, period, issuer))
            if site is not None:
                _record(result, "ir_archive", site)
                if site.status == "found":
                    return _answer(result, end, usd, value=site.value, currency=site.currency,
                                   route=f"ir_archive:{site.method}", source=site.url, evidence=site.evidence)

    def declined() -> QuarterResult:
        if result.status == "not_found" and scope == "worldwide":
            _parent_brand_line(result, end, usd, product, period, issuer, parent_brand or parent_names, earnings, run)
        return result

    if not allow_search or scope != "worldwide":
        return declined()

    # 3. Grounded search, checked against the cited document
    answer = run("search", lambda: search(product, period, issuer))
    if answer is not None:
        parsed = answer.parsed or {}
        check = verify(parsed, product)
        _record(result, "search", {"status": check.status, "parsed": parsed, "needs_review": check.needs_review,
                                   "near_text": check.near_text, "archive_url": check.archive_url})
        unit = str(parsed.get("unit") or "").lower()
        if parsed.get("value") is not None and unit in SCALE:
            value = float(parsed["value"]) * SCALE[unit]
            currency = str(parsed.get("currency") or "").upper() or None
            if check.status in ("verified", "verified_from_archive"):
                return _answer(result, end, usd, value=value, currency=currency, route="search",
                               source=check.archive_url or check.source_url, evidence=check.near_text)
            if check.needs_review:
                result.status = "needs_review"
                result.value, result.currency, result.route = value, currency, "search"
                result.source, result.evidence = check.source_url, parsed.get("source_quote")

    # 4. Printed years to date
    if end.month > 3 and result.status != "needs_review":
        derived = run("printed_years_to_date", lambda: printed(product, period, issuer))
        if derived is not None:
            _record(result, "printed_years_to_date", derived)
            if derived.status == "derived":
                return _answer(result, end, usd, value=derived.value, currency=derived.unit,
                               route=f"printed:{derived.method}", source=derived.inputs[0].get("source_url"))
    return declined()


def parent_names(product: str) -> list[str]:
    """Each shorter leading run of the product's words, longest first:
    `Calderon XR Pen` -> [`Calderon XR`, `Calderon`]."""
    words = product.split()
    return [" ".join(words[:n]) for n in range(len(words) - 1, 0, -1)]


def _parent_brand_line(result: QuarterResult, end: dt.date, usd: Callable, product: str, period: str,
                       issuer: str | None, parent_brand: Callable, earnings: Callable, run: Callable) -> None:
    """Answer from the release line of a name the product's own name starts
    with, when the issuer prints its forms under that one line. Leaves
    `result` alone when no such line is found."""
    for name in run("parent_brand", lambda: parent_brand(product)) or []:
        outcome = run("parent_brand", lambda name=name: earnings(name, period, issuer))
        if outcome is None:
            continue
        _record(result, "parent_brand", outcome)
        if _found(outcome):
            _answer(result, end, usd, value=outcome.value, currency=outcome.currency, route="earnings:parent_brand",
                    match="parent_brand", source=outcome.url, evidence=f"{name} line: {outcome.scope}")
            return


def _printed_within(derived: Derived, product: str, period: str, issuer: str | None, earnings: Callable,
                    run: Callable) -> Any:
    """The issuer's printed quarter, if its release prints one within the derived
    figure's declared spread: the issuer's own figure, which the derivation
    corroborates, rather than ours, which can sit a unit of the last place
    away from it."""
    printed_figure = run("earnings", lambda: earnings(product, period, issuer))
    if printed_figure is None or getattr(printed_figure, "status", None) != "found":
        return None
    if _currency_of(derived.unit) != printed_figure.currency:
        return None
    spread = (derived.plus_minus or 0.0) + 1e-6
    return printed_figure if abs(printed_figure.value - derived.value) <= spread else None


def _answer(result: QuarterResult, end: dt.date, usd: Callable, **fields: Any) -> QuarterResult:
    result.status = "found"
    for name, value in fields.items():
        setattr(result, name, value)
    if result.value is not None and result.currency:
        try:
            result.value_usd = usd(result.value, result.currency, end.year, end.month // 3)
        except (KeyError, LookupError):
            result.value_usd = None
    return result


def main(argv: list[str] | None = None) -> int:
    """`python -m app.retrieval.pipeline "<product>" "Q2 2023" --issuer "<issuer>"`"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("product")
    parser.add_argument("period")
    parser.add_argument("--issuer")
    parser.add_argument("--scope", default="worldwide", choices=["worldwide", "us"])
    parser.add_argument("--no-search", action="store_true", help="skip the routes that cost search fees")
    args = parser.parse_args(argv)
    result = quarter_revenue(args.product, args.period, args.issuer, scope=args.scope, allow_search=not args.no_search)
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0 if result.status == "found" else 1


if __name__ == "__main__":
    raise SystemExit(main())

