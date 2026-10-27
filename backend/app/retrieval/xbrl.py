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
# licence or milestone payment - which is not the product's sales. A snapshot
# of naming practice, not of any taxonomy: a filer naming its product revenue
# otherwise is reported as no revenue fact.
_REVENUE = re.compile(r"revenue|sales", re.IGNORECASE)
_NOT_REVENUE = re.compile(
    r"cost|deferred|unearned|receivable|allowance|reserve|returns|percent"
    r"|royalt|collaborat|alliance|licens|milestone",
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


@dataclass
class XbrlAnswer:
    """The first pass's result for one product-quarter.

    `status`: found | other_filer | dimensioned_only | no_revenue_fact |
    no_member | ambiguous | no_filing | not_a_quarter_in_10q. `other_filer`
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
    `{concept, value, unit, context}` with scale and sign already applied, and
    `unit` resolved from the unit's id to its measure (`iso4217:USD`).
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
        facts.append({"concept": concept, "value": value, "unit": unit, "context": el.get("contextRef")})
    return contexts, facts


def product_facts(contexts: dict[str, dict], facts: list[dict], product: str) -> list[Fact]:
    """Every fact whose context carries a member resolving to the product."""
    found = []
    for f in facts:
        ctx = contexts.get(f["context"])
        if not ctx:
            continue
        matches = [member_match(m, product) for m in ctx["members"].values()]
        match = next((m for m in matches if m), None)
        if match:
            found.append(Fact(f["concept"], f["value"], f["unit"], ctx["start"], ctx["end"], ctx["members"], match))
    return found


# ---------------------------------------------------------------- choosing


def quarter_end(period: str) -> dt.date | None:
    m = re.match(r"^\s*(?:Q([1-4])\s*(\d{4})|(\d{4})\s*Q([1-4]))\s*$", period, re.IGNORECASE)
    if not m:
        return None
    q, year = int(m.group(1) or m.group(4)), int(m.group(2) or m.group(3))
    return dt.date(year, q * 3, calendar.monthrange(year, q * 3)[1])


def _is_quarter(fact: Fact, filing_period_end: dt.date) -> bool:
    if not (fact.start and fact.end):
        return False
    start, end = dt.date.fromisoformat(fact.start[:10]), dt.date.fromisoformat(fact.end[:10])
    return (end - start).days in QUARTER_DAYS and abs((end - filing_period_end).days) <= PERIOD_END_SLACK_DAYS


def choose(found: list[Fact], filing_period_end: dt.date) -> tuple[str, Fact | None, list[Fact]]:
    """Pick the product's whole revenue for the filing's own quarter.

    Filers place a product under segment, portfolio or sub-segment members as
    well as its own, and split it by place. The whole figure is the fact whose
    dimensions every other candidate's dimensions contain - the root of the
    split - provided none of its own axes is geographic. Without such a fact
    only parts were tagged, which is `dimensioned_only`. Returns
    `(status, fact, candidates)`.
    """
    revenue = [
        f for f in found
        if _REVENUE.search(f.concept.split(":")[-1]) and not _NOT_REVENUE.search(f.concept.split(":")[-1])
        and (f.unit or "").startswith("iso4217:")
        and _is_quarter(f, filing_period_end)
    ]
    if not revenue:
        return "no_revenue_fact", None, found
    dims = [set(f.members.items()) for f in revenue]
    whole = [
        f for f, d in zip(revenue, dims)
        if all(d <= other for other in dims) and not any(_GEOGRAPHY_AXIS.search(axis) for axis in f.members)
    ]
    if not whole:
        return "dimensioned_only", None, revenue
    # Several revenue concepts can carry the same member - a standard and an
    # extension name for one figure, or a component beside the total. A us-gaap
    # concept is taken over an extension, then the larger figure as the total;
    # every alternative stays in `candidates` so the choice can be inspected.
    whole.sort(key=lambda f: (not f.concept.startswith("us-gaap:"), -abs(f.value)))
    return "found", whole[0], revenue


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
    time.sleep(REQUEST_GAP_SECONDS)
    response = httpx.get(url, params=params or None, headers={"User-Agent": agent}, timeout=120, follow_redirects=True)
    response.raise_for_status()
    return response


def find_10qs(product: str, end: dt.date, get: Callable = sec_get) -> list[dict]:
    """10-Qs mentioning the product whose period ends at this quarter's end."""
    params = dict(
        q=f'"{product.split("/")[0].strip()}"',
        forms="10-Q",
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
    ticker, the period or when the filer adopted inline XBRL.
    """
    folder = f"{ARCHIVES}/{filing['cik']}/{filing['accession'].replace('-', '')}"
    primary = f"{folder}/{filing['document']}"
    content = get(primary).content
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


def first_pass(product: str, period: str, issuer: str | None = None, *, get: Callable = sec_get) -> XbrlAnswer:
    """Read the product's revenue for a calendar quarter from 10-Q XBRL.

    `period` is a calendar quarter ("Q2 2023"). Q4 has no 10-Q and is reported
    as `not_a_quarter_in_10q`. `issuer`, when given, is the company whose
    figure is wanted: only its filing can supply `value`.
    """
    end = quarter_end(period)
    answer = XbrlAnswer("no_filing", product, period)
    if end is None or end.month == 12:
        answer.status = "not_a_quarter_in_10q"
        return answer

    results: list[tuple[dict, str, Fact | None, list[Fact], str]] = []
    for filing in find_10qs(product, end, get):
        answer.filings_checked.append(filing["accession"])
        try:
            document, url = instance_document(filing, get)
        except httpx.HTTPError:
            continue
        contexts, facts = parse_facts(document)
        found = product_facts(contexts, facts, product)
        if not found:
            continue
        status, fact, candidates = choose(found, dt.date.fromisoformat(filing["period_end"]))
        results.append((filing, status, fact, candidates, url))

    if not results:
        answer.status = "no_member" if answer.filings_checked else "no_filing"
        return answer
    winners = [r for r in results if r[1] == "found"]
    if issuer:
        named = [r for r in results if names_issuer(r[0]["filer"], issuer)]
        if not named:
            answer.status = "other_filer"
            answer.candidates = [
                {"filer": r[0]["filer"], "status": r[1], "value": r[2].value if r[2] else None,
                 "unit": r[2].unit if r[2] else None, "concept": r[2].concept if r[2] else None}
                for r in results
            ]
            return answer
        results = named
        winners = [r for r in named if r[1] == "found"]
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
        answer.period_start, answer.period_end = fact.start, fact.end
    return answer
