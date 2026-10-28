"""XBRL first pass: read a product's quarterly revenue from the filer's own 10-Q tags.

Nothing here knows which company sells a product. EDGAR full-text search lists
the 10-Qs that mention the product for the quarter; each one's XBRL is parsed,
and the filing whose contexts carry a member resolving to the product is the
issuer's. Competitors and partners mention a brand too, but do not tag their
revenue with a member named for it.

A figure found this way is the filer's tagged value, so it is exact, and its
source is the filing itself.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from lxml import etree

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

# A 10-Q for the quarter is filed after it ends; SEC's deadline is 40-45 days,
# late filers run longer. Searching this window from the quarter's end finds it.
FILING_WINDOW_DAYS = 120
# Issuers on a 52/53-week calendar end their quarter on a weekday near the
# month end rather than on it.
PERIOD_END_SLACK_DAYS = 10
# A quarter's context runs about 91 days; this admits 13-week and 14-week
# fiscal quarters and nothing as long as a half year.
QUARTER_DAYS = range(80, 100)
# SEC asks clients to stay under ten requests a second.
REQUEST_GAP_SECONDS = 0.15

# Concept names that read as revenue. Filers tag product revenue under the
# us-gaap revenue concepts or their own extensions, which nearly always say
# Revenue or Sales. The exclusions are of two kinds: balance-sheet and cost
# concepts that also contain those words, and revenue a filer earns from
# someone else's sales of the product - a royalty, a collaboration share, a
# licence or milestone payment - which is not the product's sales - and
# contract disclosures and corrections that carry revenue or sales in their
# name without being a period's sales (a transaction price allocated to
# performance obligations, an adjustment for a change in estimates).
# A snapshot
# of naming practice, not of any taxonomy: a filer naming its product revenue
# otherwise is reported as no revenue fact.
_REVENUE = re.compile(r"revenue|sales", re.IGNORECASE)
_NOT_REVENUE = re.compile(
    r"cost|deferred|unearned|receivable|allowance|reserve|returns|percent"
    r"|royalt|collaborat|alliance|licens|milestone|performanceobligation|transactionprice"
    r"|adjustment|changesinestimate",
    re.IGNORECASE,
)
# An axis splitting a figure by place. A product's figure carrying one is a
# part of its revenue (U.S., international), never the whole.
_GEOGRAPHY_AXIS = re.compile(r"geograph|country|region", re.IGNORECASE)

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


@dataclass
class Fact:
    """One numeric fact carrying the product's member."""

    concept: str
    value: float
    unit: str | None
    start: str | None
    end: str | None
    members: dict[str, str]  # axis -> member, both as prefixed names
    match: str  # exact | combined
    decimals: int | None = None  # as the filer declared; None is exact (INF)
    resolved: str | None = None  # the member that resolved to the product


@dataclass
class XbrlAnswer:
    """The first pass's result for one product-quarter.

    `status`: found | other_filer | dimensioned_only | no_revenue_fact |
    no_member | ambiguous | several_members | no_filing | not_a_quarter_in_10q,
    and from the
    single-product reader multi_product | unconfirmed. `match` is exact,
    combined or single_product. `other_filer`
    means a filer other than the named issuer tags the product - a licensee or
    partner selling it in another territory - and its figure is in
    `candidates`, not `value`.
    """

    status: str
    product: str
    period: str
    value: float | None = None
    unit: str | None = None
    concept: str | None = None
    member: str | None = None
    match: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    decimals: int | None = None
    filer: str | None = None
    cik: str | None = None
    accession: str | None = None
    filing_url: str | None = None
    by_geography: dict[str, float] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    filings_checked: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- matching


def tokens(name: str) -> list[str]:
    """Lower-case word tokens of a CamelCase or spaced name."""
    return [t.lower() for t in _CAMEL.findall(name)]


def member_match(member: str, product: str) -> str | None:
    """How a member's local name resolves to a product, or None.

    The product's name must close the member name, starting on a word boundary,
    after any category words: `RespiratoryProductsCalderonMember` resolves to
    Calderon; `CalderonXRMember` does not. A member naming the product beside a
    sibling - `CalderonAndNuVessaMember`, `NuVessaAndCalderonMember` - is
    `combined`, so the figure is known to cover more than the product. Case is
    ignored, so `NUVESSAMember` resolves to NuVessa; and because a capital is
    read as a word break, `NuVessaMember` also resolves to a product named
    Vessa. A product written with
    alternatives (`Calderon / Calderon XR`) resolves if any alternative does.
    """
    local = member.split(":")[-1]
    local = local[: -len("Member")] if local.endswith("Member") else local
    letters = re.sub(r"[^A-Za-z0-9]", "", local)
    lower = letters.lower()
    for alternative in re.split(r"\s*/\s*", product):
        want = re.sub(r"[^a-z0-9]", "", alternative.lower())
        if not want:
            continue
        i = lower.find(want)
        while i != -1:
            j = i + len(want)
            if _boundary(letters, i):
                before, after = lower[:i], lower[j:]
                joined = before.endswith("and") and _boundary(letters, i - 3)
                if not after:
                    return "combined" if joined else "exact"
                if after.startswith("and") and _boundary(letters, j):
                    return "combined"
            i = lower.find(want, i + 1)
    return None


def _boundary(name: str, i: int) -> bool:
    """Whether a word starts at `name[i]` in a CamelCase or all-caps name."""
    if i <= 0:
        return i == 0
    if i >= len(name):
        return True
    prev, here = name[i - 1], name[i]
    nxt = name[i + 1] if i + 1 < len(name) else ""
    if here.isdigit() != prev.isdigit():
        return True
    if here.isupper() and prev.islower():
        return True
    # The last capital of a run that starts a capitalised word: AYVAKIT|And.
    if here.isupper() and prev.isupper() and nxt.islower():
        return True
    # In a name with no lower case at all, word breaks cannot be seen.
    return name.isupper()


# Words that say what kind of entity a name is rather than which one. A
# snapshot of how SEC display names and common usage spell issuers.
_ENTITY_WORDS = {"inc", "co", "corp", "corporation", "company", "plc", "ltd", "llc", "sa", "ag", "nv", "the", "and"}


def names_issuer(filer: str, issuer: str) -> bool:
    """Whether a filer's display name is the issuer: every distinctive word of
    the issuer's name appears in it. `Acme` names `ACME PHARMA INC (CIK 1)`;
    `Acme KGaA` does not name `ACME & CO`. Words are compared whole and without
    case, so `NuVessa Bio` names `NUVESSA BIO INC`; an issuer written with
    alternatives (`Calderon Labs/Acme`) is named if any alternative is."""
    def words(name: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", name.lower())) - _ENTITY_WORDS

    have = words(filer.split("(")[0])
    return any(want and want <= have for want in map(words, issuer.split("/")))


# ---------------------------------------------------------------- parsing


def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _number(text: str, fmt: str | None) -> float | None:
    fmt = (fmt or "").lower()
    raw = "".join(text.split())
    if "zero" in fmt or raw in {"-", "—", "–"}:
        return 0.0
    if "comma" in fmt and "decimal" in fmt and "dot" not in fmt.split("comma")[0]:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_facts(document: bytes) -> tuple[dict[str, dict], list[dict]]:
    """Contexts and numeric facts from an inline-XBRL document or an XBRL instance.

    Returns `(contexts, facts)`. A context is `{start, end, members}`; a fact is
    `{concept, value, unit, context, decimals}` with scale and sign already
    applied, `unit` resolved from the unit's id to its measure (`iso4217:USD`),
    and `decimals` an int, or None where the filer declared INF or nothing.
    """
    root = etree.fromstring(document, etree.XMLParser(recover=True, huge_tree=True))
    units: dict[str, str] = {}
    for unit in root.iter():
        if _local(unit.tag) == "unit":
            measures = [(m.text or "").strip() for m in unit.iter() if _local(m.tag) == "measure"]
            units[unit.get("id", "")] = "/".join(measures)
    contexts: dict[str, dict] = {}
    for ctx in root.iter():
        if _local(ctx.tag) != "context":
            continue
        start = end = None
        members: dict[str, str] = {}
        for el in ctx.iter():
            name = _local(el.tag)
            if name == "startDate":
                start = (el.text or "").strip()
            elif name in ("endDate", "instant"):
                end = (el.text or "").strip()
            elif name == "explicitMember":
                members[el.get("dimension", "")] = (el.text or "").strip()
        contexts[ctx.get("id", "")] = {"start": start, "end": end, "members": members}

    facts: list[dict] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.get("contextRef") is None:
            continue
        if _local(el.tag) == "nonFraction":
            if el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
                continue
            value = _number("".join(el.itertext()), el.get("format"))
            if value is None:
                continue
            value *= 10 ** int(el.get("scale") or 0)
            if el.get("sign") == "-":
                value = -value
            concept = el.get("name", "")
        elif _local(el.tag) in ("nonNumeric", "continuation", "footnote") or el.get("unitRef") is None:
            continue
        else:
            value = _number(el.text or "", None)
            if value is None:
                continue
            prefix = el.prefix or ""
            concept = f"{prefix}:{_local(el.tag)}" if prefix else _local(el.tag)
        unit = units.get(el.get("unitRef", ""), el.get("unitRef"))
        declared = (el.get("decimals") or "").strip()
        decimals = int(declared) if re.fullmatch(r"-?\d+", declared) else None
        facts.append({"concept": concept, "value": value, "unit": unit, "context": el.get("contextRef"),
                      "decimals": decimals})
    return contexts, facts


def product_facts(contexts: dict[str, dict], facts: list[dict], product: str) -> list[Fact]:
    """Every fact whose context carries a member resolving to the product."""
    found = []
    for f in facts:
        ctx = contexts.get(f["context"])
        if not ctx:
            continue
        hits = [(m, member_match(m, product)) for m in ctx["members"].values()]
        hit = next(((m, how) for m, how in hits if how), None)
        if hit:
            found.append(Fact(f["concept"], f["value"], f["unit"], ctx["start"], ctx["end"], ctx["members"], hit[1],
                              f.get("decimals"), hit[0]))
    return found


# ---------------------------------------------------------------- choosing


def quarter_end(period: str) -> dt.date | None:
    m = re.match(r"^\s*(?:Q([1-4])\s*(\d{4})|(\d{4})\s*Q([1-4]))\s*$", period, re.IGNORECASE)
    if not m:
        return None
    q, year = int(m.group(1) or m.group(4)), int(m.group(2) or m.group(3))
    return dt.date(year, q * 3, calendar.monthrange(year, q * 3)[1])


def spans(start: str | None, end: str | None, period_end: dt.date, days: range) -> bool:
    """Whether a context runs `days` long and ends at `period_end`, give or take
    a 52/53-week calendar's few days."""
    if not (start and end):
        return False
    s, e = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    return (e - s).days in days and abs((e - period_end).days) <= PERIOD_END_SLACK_DAYS


def is_revenue(concept: str, unit: str | None) -> bool:
    """A monetary fact under a concept that reads as the filer's own sales."""
    name = concept.split(":")[-1]
    return bool(_REVENUE.search(name)) and not _NOT_REVENUE.search(name) and (unit or "").startswith("iso4217:")


def choose(
    found: list[Fact], filing_period_end: dt.date, days: range = QUARTER_DAYS
) -> tuple[str, Fact | None, list[Fact]]:
    """Pick the product's whole revenue for the span `days` long ending at the
    filing's period end - the quarter, by default.

    Filers place a product under segment, portfolio or sub-segment members as
    well as its own, and split it by place. The whole figure is the fact whose
    dimensions every other candidate's dimensions contain - the root of the
    split - provided none of its own axes is geographic. Without such a fact
    only parts were tagged, which is `dimensioned_only`. Returns
    `(status, fact, candidates)`.
    """
    revenue = [f for f in found if is_revenue(f.concept, f.unit) and spans(f.start, f.end, filing_period_end, days)]
    if not revenue:
        return "no_revenue_fact", None, found
    # Two different members resolving to the product - `CalderonMember` beside
    # `NebulizedCalderonMember` - are two scopes, and nothing in the filing says
    # which one the question means.
    resolving = {f.resolved for f in revenue if f.resolved}
    if len(resolving) > 1:
        return "several_members", None, revenue
    dims = [set(f.members.items()) for f in revenue]
    whole = [
        f for f, d in zip(revenue, dims)
        if all(d <= other for other in dims) and not any(_GEOGRAPHY_AXIS.search(axis) for axis in f.members)
    ]
    if not whole:
        summed = _us_plus_non_us(revenue)
        if summed is not None:
            return "found", summed, revenue
        return "dimensioned_only", None, revenue
    # Several facts can carry the same member - a table's figure and a rounded
    # one tagged in prose ("approximately $2.3 billion"), a standard and an
    # extension name, a component beside the total. The most precise declared
    # figure is taken, then a us-gaap concept over an extension, then the larger
    # figure as the total; every alternative stays in `candidates`.
    whole.sort(key=lambda f: (-precision(f.decimals), not f.concept.startswith("us-gaap:"), -abs(f.value)))
    return "found", whole[0], revenue


# The two members that partition a figure by place on their own: the United
# States and everything outside it. A split into other regions is not known to
# be exhaustive, so only this pair is summed into a whole.
_US, _NON_US = "country:US", "us-gaap:NonUsMember"


def _us_plus_non_us(revenue: list[Fact]) -> Fact | None:
    """The whole as the U.S. part plus the non-U.S. part, when those are the
    finest split tagged - one concept, the same other dimensions."""
    for us in revenue:
        geo = [(a, m) for a, m in us.members.items() if _GEOGRAPHY_AXIS.search(a)]
        if len(geo) != 1 or geo[0][1] != _US:
            continue
        axis = geo[0][0]
        rest = {a: m for a, m in us.members.items() if a != axis}
        for non_us in revenue:
            if non_us.concept == us.concept and non_us.members == {**rest, axis: _NON_US}:
                coarser = min((us.decimals, non_us.decimals), key=precision)
                return Fact(us.concept, us.value + non_us.value, us.unit, us.start, us.end,
                            rest, us.match, coarser)
    return None


def precision(decimals: int | None) -> float:
    """Declared decimals as a sortable precision; INF (None) is the finest."""
    return float("inf") if decimals is None else decimals


def declared_slack(decimals: int | None) -> float:
    """Half a unit of the last place the filer declared - how far the tagged
    figure may sit from the exact one. Zero for INF."""
    return 0.0 if decimals is None else 0.5 * 10.0 ** -decimals


def geography_parts(whole: Fact, candidates: list[Fact]) -> dict[str, float]:
    """The whole figure's split by place, as `{member: value}`.

    A part is a candidate with the whole's dimensions plus one geographic axis
    and the same concept.
    """
    root = set(whole.members.items())
    parts = {}
    for c in candidates:
        extra = set(c.members.items()) - root
        if c.concept == whole.concept and root <= set(c.members.items()) and len(extra) == 1:
            (axis, member), = extra
            if _GEOGRAPHY_AXIS.search(axis):
                parts[member] = c.value
    return parts


# ---------------------------------------------------------------- SEC access


def sec_get(url: str, **params: Any) -> httpx.Response:
    """GET from SEC with the declared User-Agent SEC requires, or fail saying so."""
    agent = os.environ.get("SEC_USER_AGENT")
    if not agent:
        raise RuntimeError("SEC_USER_AGENT is not set; SEC refuses requests without a contact in the User-Agent")
    # SEC's servers answer a 5xx under load now and then; it clears on retry.
    for attempt in range(3):
        time.sleep(REQUEST_GAP_SECONDS + (2 ** attempt - 1))
        response = httpx.get(url, params=params or None, headers={"User-Agent": agent}, timeout=120,
                             follow_redirects=True)
        if response.status_code < 500:
            break
    response.raise_for_status()
    return response


def find_10qs(product: str, end: dt.date, get: Callable = sec_get) -> list[dict]:
    """10-Qs mentioning the product whose period ends at this quarter's end."""
    return find_filings(product, end, "10-Q", get)


def find_filings(product: str, end: dt.date, form: str, get: Callable = sec_get) -> list[dict]:
    """Filings of `form` mentioning the product whose period ends at `end`."""
    params = dict(
        q=f'"{product.split("/")[0].strip()}"',
        forms=form,
        dateRange="custom",
        startdt=end.isoformat(),
        enddt=(end + dt.timedelta(days=FILING_WINDOW_DAYS)).isoformat(),
    )
    # Full-text search answers an over-eager client with a body that has no
    # hits; that is a refusal to retry, not an empty result.
    for attempt in range(3):
        body = get(SEARCH_URL, **params).json()
        if "hits" in body:
            break
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"full-text search refused {product!r}: {str(body)[:200]}")
    hits = body["hits"]["hits"]
    filings, seen = [], set()
    for hit in hits:
        src = hit["_source"]
        if not src.get("period_ending") or src["adsh"] in seen:
            continue
        if abs((dt.date.fromisoformat(src["period_ending"]) - end).days) > PERIOD_END_SLACK_DAYS:
            continue
        seen.add(src["adsh"])
        filings.append({
            "accession": src["adsh"],
            "cik": src["ciks"][0].lstrip("0"),
            "filer": src["display_names"][0],
            "document": hit["_id"].split(":", 1)[1],
            "period_end": src["period_ending"],
        })
    return filings


def instance_document(filing: dict, get: Callable = sec_get) -> tuple[bytes, str]:
    """The filing's XBRL: the primary document if it is inline, else its instance.

    An instance filed beside the document shares the stem of the filing's own
    `.xsd` schema, so it is found from the filing index without knowing the
    ticker, the period or when the filer adopted inline XBRL. The primary
    document itself is kept on the filing as `filing["primary"]`, for readers
    of its text.
    """
    folder = f"{ARCHIVES}/{filing['cik']}/{filing['accession'].replace('-', '')}"
    primary = f"{folder}/{filing['document']}"
    content = get(primary).content
    filing["primary"] = content
    if b"nonFraction" in content:
        return content, primary
    names = [item["name"] for item in get(f"{folder}/index.json").json()["directory"]["item"]]
    stems = [n[: -len(".xsd")] for n in names if n.endswith(".xsd")]
    for stem in stems:
        for name in names:
            if name.endswith(".xml") and name.startswith(stem) and not re.search(r"_(cal|def|lab|pre)\.xml$", name):
                return get(f"{folder}/{name}").content, primary
    return content, primary


# ---------------------------------------------------------------- entry point


def first_pass(
    product: str,
    period: str,
    issuer: str | None = None,
    *,
    get: Callable = sec_get,
    confirm: Callable | None = None,
) -> XbrlAnswer:
    """Read the product's revenue for a calendar quarter from 10-Q XBRL.

    `period` is a calendar quarter ("Q2 2023"). A calendar Q4 is looked for in
    10-Qs too - a filer whose fiscal year ends in June files one for it - and
    is `not_a_quarter_in_10q` when none exists. `issuer`, when given, is the
    company whose figure is wanted: only its filing can supply `value`.
    `confirm` is passed to the single-product reader; see `read_span`.
    """
    end = quarter_end(period)
    if end is None:
        return XbrlAnswer("not_a_quarter_in_10q", product, period)
    answer = read_span(product, period, end, "10-Q", QUARTER_DAYS, issuer, get=get, confirm=confirm)
    if end.month == 12 and answer.status == "no_filing":
        answer.status = "not_a_quarter_in_10q"
    return answer


def read_span(
    product: str,
    label: str,
    end: dt.date,
    form: str,
    days: range,
    issuer: str | None = None,
    *,
    get: Callable = sec_get,
    confirm: Callable | None = None,
) -> XbrlAnswer:
    """The product's revenue for the span `days` long ending at `end`, from
    filings of `form` (a 10-Q's quarter or year to date, a 10-K's year).

    When the named issuer's filing tags no member for the product, the
    single-product reader is tried on it (`app.retrieval.single_product`):
    a filer that sells only this product reports it as its product revenue.
    It runs only with an issuer named, since otherwise nothing says which
    filing mentioning the product is the seller's, and only when `confirm` is
    given, since the figure is accepted only once the filing's own text says
    no other product was sold.
    """
    answer = XbrlAnswer("no_filing", product, label)
    results: list[tuple[dict, str, Fact | None, list[Fact], str]] = []
    issuer_filings: list[tuple[dict, dict, list[dict], str]] = []
    for filing in find_filings(product, end, form, get):
        answer.filings_checked.append(filing["accession"])
        try:
            document, url = instance_document(filing, get)
        except httpx.HTTPError:
            continue
        contexts, facts = parse_facts(document)
        if issuer and names_issuer(filing["filer"], issuer):
            issuer_filings.append((filing, contexts, facts, url))
        found = product_facts(contexts, facts, product)
        if not found:
            continue
        status, fact, candidates = choose(found, dt.date.fromisoformat(filing["period_end"]), days)
        results.append((filing, status, fact, candidates, url))

    if issuer:
        named = [r for r in results if names_issuer(r[0]["filer"], issuer)]
        # A member that carries no revenue - a count of countries, a milestone -
        # says nothing about whether the filer sells other products, so the
        # single-product reader is tried then too. Failing, it leaves the
        # answer as it was, so a quarter tagged only year to date can still be
        # derived.
        untagged_revenue = not named or all(r[1] == "no_revenue_fact" for r in named)
        if untagged_revenue and issuer_filings and confirm is not None:
            from app.retrieval.single_product import read_single_product

            filing, contexts, facts, url = issuer_filings[0]
            single = read_single_product(XbrlAnswer("no_member", product, label, filings_checked=list(answer.filings_checked)),
                                         filing, contexts, facts, url, days, confirm)
            if single.status == "found" or not named:
                return single
        if not named and results:
            answer.status = "other_filer"
            answer.candidates = [
                {"filer": r[0]["filer"], "status": r[1], "value": r[2].value if r[2] else None,
                 "unit": r[2].unit if r[2] else None, "concept": r[2].concept if r[2] else None}
                for r in results
            ]
            return answer
        results = named
    if not results:
        answer.status = "no_member" if answer.filings_checked else "no_filing"
        return answer
    winners = [r for r in results if r[1] == "found"]
    if len({w[0]["cik"] for w in winners}) > 1:
        answer.status = "ambiguous"
        answer.candidates = [{"filer": w[0]["filer"], "value": w[2].value, "concept": w[2].concept} for w in winners]
        return answer
    filing, status, fact, candidates, url = (winners or results)[0]
    answer.status = status
    answer.filer, answer.cik, answer.accession, answer.filing_url = filing["filer"], filing["cik"], filing["accession"], url
    answer.candidates = [
        {"concept": c.concept, "value": c.value, "unit": c.unit, "start": c.start, "end": c.end, "members": c.members}
        for c in candidates
    ]
    if fact:
        answer.by_geography = geography_parts(fact, candidates)
        member = next(m for m in fact.members.values() if member_match(m, product))
        answer.value, answer.unit, answer.concept, answer.member, answer.match = (
            fact.value, fact.unit, fact.concept, member, fact.match,
        )
        answer.decimals = fact.decimals
        answer.period_start, answer.period_end = fact.start, fact.end
    return answer
