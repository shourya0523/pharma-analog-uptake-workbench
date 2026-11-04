"""One product, one calendar quarter: every route, in order, until one holds.

    quarter_revenue("Calderon", "Q2 2023", "Acme Pharma")

Routes, most exact first; the first that produces a checked figure answers:

1. XBRL - the issuer's 10-Q/10-K tags (a member named for the product, else
   a single-product filer's product line, else a quarter derived from
   stated years to date).
2. Earnings releases - the issuer's results exhibit read as a table grid,
   then as text, then later releases' comparative columns.
3. Grounded search - Gemini with Google Search, kept only if the figure is
   found beside the product in the document it cites. A page that refuses
   this client yields `needs_review`, never `found`.
4. Printed years to date - two stated figures from grounded search, each
   checked, subtracted (Q2-Q4).

Routes 1 and 2 are retried under the product's FDA-history aliases (a brand
line that can only have been this form in that period). Every route's result
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
from app.retrieval.xbrl import quarter_end


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
    allow_search: bool = True,
    xbrl: Callable = xbrl_quarter,
    earnings: Callable = read_earnings,
    search: Callable = ask_revenue,
    verify: Callable = verify_answer,
    printed: Callable = derive_from_printed,
    describe_aliases: Callable = fda_aliases,
    confirm: Callable | None = gemini_confirm,
    usd: Callable = fx.to_usd,
) -> QuarterResult:
    """Run the routes for `product` in calendar quarter `period` ("Q2 2023").

    `scope` is "worldwide" or "us"; a U.S. figure is taken only where a route
    can show it is the U.S. part (XBRL's tagged geography). `allow_search`
    turns off routes 3 and 4, which cost search fees. The route functions are
    parameters so the order can be tested without the network.
    """
    result = QuarterResult("not_found", product, period, issuer)
    end = quarter_end(period)
    if end is None:
        result.attempts.append({"route": "input", "status": "not_a_calendar_quarter"})
        return result

    def run(route: str, attempt: Callable) -> Any:
        try:
            return attempt()
        except QuotaExceeded as exc:
            result.attempts.append({"route": route, "status": "quota_exceeded", "reason": str(exc)[:200]})
        except Exception as exc:  # one route failing leaves the others to answer
            result.attempts.append({"route": route, "status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]})
        return None

    # 1. XBRL
    outcome = run("xbrl", lambda: _with_aliases(
        lambda name: xbrl(name, period, issuer, confirm=confirm, scope=scope), product, period, end, describe_aliases))
    if outcome is not None:
        _record(result, "xbrl", outcome)
        for c in getattr(outcome, "candidates", []) or []:
            if c.get("filer") and c.get("value") is not None:
                result.alternatives.append({"route": "xbrl", **c})
        if _found(outcome):
            derived = isinstance(outcome, Derived)
            return _answer(result, end, usd, value=outcome.value, currency=_currency_of(outcome.unit),
                           route=f"xbrl:{outcome.method}" if derived else "xbrl",
                           match=None if derived else outcome.match,
                           source=(outcome.inputs[0].get("filing_url") if derived and outcome.inputs else
                                   getattr(outcome, "filing_url", None)),
                           evidence=None if derived else f"{outcome.concept} {outcome.member}",
                           identity=getattr(outcome, "identity", None))

    # 2. Earnings releases (their lines are whole-product; a U.S. question is not answered here)
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

    if not allow_search or scope != "worldwide":
        return result

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
    return result


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

