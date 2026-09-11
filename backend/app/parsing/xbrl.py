"""Facts a filer tagged, rather than numbers read off a printed table.

Everything in ``app/parsing/documents.py`` and ``app/extraction/fingerprint.py``
exists to recover what a table means from how it is laid out: which column holds
which period, what unit the numbers are in, whether a row is the product or one
region of it. From 2019 the filers state all of that outright, in XBRL, and the
recovery is unnecessary where the statement exists:

    inferred from a table              declared in a fact
    ---------------------------------  ---------------------------------------
    which column holds which period    the context's startDate and endDate
    the unit, from a caption           the fact's own unit and decimals
    whether a row names the product    the ProductOrService axis member
    a region line vs the worldwide      the geographic axis, present or absent

How far this reaches is a property of the issuer rather than a date. The
2019-2021 inline-XBRL phase-in made detail tagging of the revenue note
mandatory; it did not invent it, and filers were tagging products on the
product axis years before their own deadline. ``filer_category`` reads the
issuer's own declaration
of which category it is in, and ``product_facts`` returns nothing for a filing
that genuinely tagged nothing, which is the honest way for a reader to say "not
here".

Believing the phase-in was the start of tagging cost real coverage twice, in
two places that each looked like a property of the data: the connector fetched
only instances named the inline-era way, and this module knew the product axis
only under the name the 2018 taxonomy gave it. Both silently read a fully
tagged filing as an empty one.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

# The product axis under the spellings this module can name without being told.
# It lived in the us-gaap namespace until the 2018 taxonomy moved the reporting
# axes into srt, so a filing from before that says "us-gaap:ProductOrServiceAxis".
#
# These are a fallback, not the rule. A filer states products on whatever axis
# its taxonomy gives it - an IFRS filer uses
# ``ifrs-full:ProductsAndServicesAxis``, and one of them also invents
# ``<prefix>:ProductPortfolioAxis`` of its own - so no list of spellings can be
# the way this is decided. ``product_facts`` takes a ``names_a_product``
# predicate and finds the axis by asking which of a fact's members resolves to
# a product; these names are what it falls back to when no caller supplies one,
# and what ``notes_datasets`` keys a normalised bulk row under.
PRODUCT_AXIS = "srt:ProductOrServiceAxis"
PRODUCT_AXES = (PRODUCT_AXIS, "us-gaap:ProductOrServiceAxis")
GEOGRAPHIC_AXES = ("srt:StatementGeographicalAxis", "us-gaap:StatementGeographicalAxis")

# The bodies that publish a taxonomy. Everything else in an instance is the
# filer's own extension, and an extension is by definition a measure the
# taxonomy has no name for - reading one as revenue is how
# ``<prefix>:GrossProfitExcludingOtherRevenue`` came to be published beside the
# revenue it is derived from, two figures for one quarter each citing a tag.
#
# What separates the two is authorship rather than spelling, so this is a
# snapshot of who the standard-setters are, not of what they call things. It
# goes stale when a filing cites a taxonomy published somewhere new; the
# failure is then that its facts read as extensions and are left unread, which
# is the safe direction. Hosts, because the year and the taxonomy name are in
# the path and change every year.
_STANDARD_TAXONOMY_HOSTS = ("fasb.org", "xbrl.ifrs.org", "xbrl.sec.gov", "xbrl.org")

# The same question for a fact that arrives with no namespaces to read: from an
# instance `parse_facts` answers it from the URI above, but a bulk extract row
# carries only a prefix, and so does a Fact built in a test. The bulk reader
# already makes the distinction - a standard tag's version is the taxonomy
# ("us-gaap/2025") and an extension's is the accession that defined it - so the
# prefix is a faithful fallback there. It is only ever a fallback.
_STANDARD_TAXONOMY_PREFIXES = frozenset({
    "us-gaap", "ifrs-full", "ifrs", "srt", "dei", "country", "currency", "invest",
})

# A currency, as ISO 4217 writes one. `parse_units` reduces a unit to its
# measures, so money is three letters and a rate, a count or a percentage is
# not ("USD/shares", "pure", "item", "shares").
_CURRENCY_UNIT = re.compile(r"[A-Z]{3}\Z")

# A forecast is not a report. A filer tags next quarter's expectation for a
# product on the same axis as the quarters it has closed, and nothing else about
# the fact says which it is.
_HYPOTHETICAL = ("Forecast", "Scenario", "ProForma", "Restatement")


@dataclass(frozen=True)
class Fact:
    """One tagged number, with everything the filer said about it."""

    element: str
    value: float
    members: dict[str, str] = field(default_factory=dict)
    start: date | None = None
    end: date | None = None
    unit: str | None = None
    decimals: str | None = None
    context_id: str = ""
    # Whether the element comes from a published taxonomy rather than from the
    # filer's own extension. `parse_facts` reads it from the instance; None
    # where there was no namespace to read, and the prefix answers instead.
    standard_element: bool | None = None

    @property
    def from_standard_taxonomy(self) -> bool:
        """Whether a standard-setter named this element, rather than the filer.

        An extension is by definition a measure the taxonomy has no name for,
        so reading one as revenue publishes something the filer never called
        revenue.
        """
        if self.standard_element is not None:
            return self.standard_element
        prefix, _, local = self.element.partition(":")
        return bool(local) and prefix in _STANDARD_TAXONOMY_PREFIXES

    @property
    def states_an_amount(self) -> bool:
        """Whether nothing about the unit says this is not money.

        A percentage or a count declares itself - "pure", "item", "shares" - and
        a rate carries a divisor. An absent unit is unknown rather than
        non-money, so a fact is not dropped for a unit its source never
        recorded.
        """
        return not self.unit or bool(_CURRENCY_UNIT.fullmatch(self.unit))

    @property
    def months(self) -> int | None:
        if not (self.start and self.end):
            return None
        return round((self.end - self.start).days / 30.44)

    @property
    def period(self) -> str | None:
        """Canonical label: 2024Q3 for a quarter, 2024 for a year.

        Labelled from the middle of the period rather than its end, because
        filers on a 52/53-week fiscal calendar end a year in early January of
        the next one. A fiscal 2022 can run to 1 January 2023; reading the end
        date alone files it as 2023, on top of the real 2023, and both then
        carry a citation saying so.
        """
        if not (self.start and self.end):
            return None
        middle = self.start + (self.end - self.start) / 2
        months = self.months
        if months == 3:
            return f"{middle.year}Q{(middle.month - 1) // 3 + 1}"
        if months == 12:
            return str(middle.year)
        return None

    @property
    def rounding_unit(self) -> float | None:
        """What the filer rounded this value to, in the fact's own unit.

        XBRL's ``decimals`` says how far a value is accurate: ``-6`` is the
        nearest million, so the true figure is within half a million of what is
        tagged. ``INF`` is exact. None where the filer said nothing, which is
        not the same as exact.

        Callers use this to bound a derived figure: a fourth quarter is never
        tagged and is computed by subtraction, so it inherits the rounding of
        every input it was computed from.
        """
        raw = (self.decimals or "").strip()
        if not raw:
            return None
        if raw.upper() == "INF":
            return 0.0
        try:
            return float(10.0 ** -int(raw))
        except ValueError:
            return None

    @property
    def product_member(self) -> str | None:
        for axis in PRODUCT_AXES:
            if axis in self.members:
                return self.members[axis]
        return None

    @property
    def is_worldwide(self) -> bool:
        """A product fact with no geography on it is the worldwide figure.

        The filer states a region by adding an axis. Saying nothing about
        geography is how a total is expressed, so the absence is the claim -
        which is the same figure the table reader has to recover by adding the
        regional lines up and finding the row that matches.
        """
        return not any(axis in self.members for axis in GEOGRAPHIC_AXES)

    @property
    def citation(self) -> str:
        """What is quoted for this value, in place of a line of text.

        A tagged fact has no prose to quote. What it has is stronger: the
        filer's own machine-readable assertion, which this names precisely
        enough that a reader can find it in the instance and check it.
        """
        where = " ".join(f"{axis.split(':')[-1]}={member.split(':')[-1]}"
                         for axis, member in sorted(self.members.items()))
        span = f"{self.start}..{self.end}" if self.start else "instant"
        # Written out, not in the shortest form. `:g` renders anything from a
        # million upwards in scientific notation, and a citation that does not
        # contain the figure it cites cannot be checked against it - which is
        # what every reader downstream does before publishing.
        amount = f"{self.value:,.4f}".rstrip("0").rstrip(".")
        return f"{self.element} [{span}] {where} = {amount}".strip()


def _prefixes(raw: bytes) -> dict[str, str]:
    """Namespace URI -> the prefix the filer used, so facts keep their names."""
    found: dict[str, str] = {}
    for event, item in ET.iterparse(io.BytesIO(raw), events=("start-ns",)):
        prefix, uri = item
        found.setdefault(uri, prefix)
    return found


def _qname(tag: str, prefixes: dict[str, str]) -> str:
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    prefix = prefixes.get(uri)
    return f"{prefix}:{local}" if prefix else local


def _as_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def parse_contexts(root: ET.Element, prefixes: dict[str, str]) -> dict[str, tuple[dict[str, str], date | None, date | None]]:
    contexts: dict[str, tuple[dict[str, str], date | None, date | None]] = {}
    for node in root.iter():
        if not _qname(node.tag, prefixes).endswith("context"):
            continue
        members: dict[str, str] = {}
        start = end = None
        for child in node.iter():
            name = _qname(child.tag, prefixes)
            if name.endswith("explicitMember"):
                axis = child.get("dimension")
                if axis and child.text:
                    members[axis.strip()] = child.text.strip()
            elif name.endswith("startDate"):
                start = _as_date(child.text)
            elif name.endswith("endDate"):
                end = _as_date(child.text)
        contexts[node.get("id", "")] = (members, start, end)
    return contexts


def parse_units(root: ET.Element, prefixes: dict[str, str]) -> dict[str, str]:
    units: dict[str, str] = {}
    for node in root.iter():
        if not _qname(node.tag, prefixes).endswith("unit"):
            continue
        measures = [child.text.strip() for child in node.iter()
                    if _qname(child.tag, prefixes).endswith("measure") and child.text]
        if measures:
            units[node.get("id", "")] = "/".join(m.split(":")[-1] for m in measures)
    return units


def _standard_namespaces(prefixes: dict[str, str]) -> set[str]:
    """The prefixes in this instance that belong to a published taxonomy.

    Read from the instance's own namespace declarations, so a filer using an
    unusual prefix for a standard taxonomy is still read as standard, and one
    declaring its extension as "us-gaap-ish" is not.
    """
    standard = set()
    for uri, prefix in prefixes.items():
        host = uri.split("//", 1)[-1].split("/", 1)[0].lower()
        if any(host == known or host.endswith("." + known)
               for known in _STANDARD_TAXONOMY_HOSTS):
            standard.add(prefix)
    return standard


def parse_facts(raw: bytes) -> list[Fact]:
    """Every numeric fact in the instance, with its context resolved."""
    prefixes = _prefixes(raw)
    standard = _standard_namespaces(prefixes)
    root = ET.fromstring(raw)
    contexts = parse_contexts(root, prefixes)
    units = parse_units(root, prefixes)
    facts: list[Fact] = []
    for node in root.iter():
        context_id = node.get("contextRef")
        if not context_id or node.text is None:
            continue
        text = node.text.strip().replace(",", "")
        if not text or not re.fullmatch(r"-?\d+(\.\d+)?", text):
            continue
        members, start, end = contexts.get(context_id, ({}, None, None))
        sign = -1.0 if (node.get("sign") or "") == "-" else 1.0
        element = _qname(node.tag, prefixes)
        facts.append(Fact(
            element=element,
            standard_element=element.split(":")[0] in standard if ":" in element else True,
            value=sign * float(text),
            members=members,
            start=start,
            end=end,
            unit=units.get(node.get("unitRef") or ""),
            decimals=node.get("decimals"),
            context_id=context_id,
        ))
    return facts


def filer_category(raw: bytes) -> str | None:
    """The issuer's own statement of its filer size.

    This is what decides when detail tagging became mandatory for it, so it is
    read from the filing rather than assumed from a date.
    """
    match = re.search(rb"<dei:EntityFilerCategory[^>]*>([^<]+)<", raw)
    return match.group(1).decode("utf-8", "ignore").strip() if match else None


def _is_hypothetical(fact: Fact) -> bool:
    """Whether the fact describes something other than what happened."""
    return any(
        mark in axis or mark in member
        for axis, member in fact.members.items()
        for mark in _HYPOTHETICAL
    )


def _product_member(fact: Fact, names_a_product) -> str | None:
    """Which of a fact's members names a product, by asking rather than by name.

    ``names_a_product`` is the caller's resolver. A fact carrying exactly one
    member it accepts is about that product; one carrying two is a cross-tab of
    two products, which states neither on its own and is declined the way the
    resolver declines a tie.
    """
    if names_a_product is None:
        return fact.product_member
    found = [member for member in fact.members.values() if names_a_product(member)]
    return found[0] if len(found) == 1 else None


def revenue_elements(facts: list[Fact], *, names_a_product=None) -> frozenset[str]:
    """Which elements this filing states product revenue in.

    Read from the instance instead of named. Among facts that are money, cover
    a span of time, come from a published taxonomy and sit on the axis naming a
    product, the element the filer uses most is the one it reports sales in: a
    company breaks its products out by revenue far more often than by anything
    else, so the count separates them without a vocabulary of element names.

    Counting alone is not enough, and the shape that defeats it is ordinary: a
    filer reporting product profitability tags one cost against every revenue,
    so the two tie exactly and a cost of goods sold is read as a sale. What
    separates them is that a cost is a part of what it is taken from - for the
    same product and period it is smaller, every time. So an element another
    element beats wherever both appear is dropped as a component of it.

    A genuine tie survives: two elements a filer states revenue under are of
    the same size, neither dominates, and dropping either would lose a product.
    """
    counts: dict[str, int] = {}
    values: dict[tuple[str, str, int | None], dict[str, float]] = {}
    for fact in facts:
        member = _product_member(fact, names_a_product)
        if (
            fact.from_standard_taxonomy
            and fact.states_an_amount
            and fact.months
            and member
            and not _is_hypothetical(fact)
        ):
            counts[fact.element] = counts.get(fact.element, 0) + 1
            key = (member, fact.period or "", fact.months)
            values.setdefault(key, {})[fact.element] = fact.value
    if not counts:
        return frozenset()
    most = max(counts.values())
    top = {element for element, n in counts.items() if n == most}
    if len(top) < 2:
        return frozenset(top)
    return frozenset(
        element for element in top
        if not any(_dominates(other, element, values) for other in top if other != element)
    )


def _dominates(bigger: str, smaller: str, values: dict) -> bool:
    """Whether one element outranks another everywhere the two are comparable."""
    shared = [row for row in values.values() if bigger in row and smaller in row]
    return bool(shared) and all(row[bigger] > row[smaller] for row in shared)


def _is_a_slice(candidates: list[Fact], members: list[str]) -> set[int]:
    """Which facts are a piece of a product rather than the whole of it.

    A filer states a region by adding an axis, so a piece carries a qualifier
    the whole does not. Which axis that is cannot be known by name - an IFRS
    filer splits by ``MarketsOfCustomersAxis``, a us-gaap one by
    ``StatementGeographicalAxis``, and a filer may invent its own - so it is
    read from what the filing does with it: an axis carrying more than one
    member for the same product and period is a breakdown of that product, and
    every fact carrying that axis is one of the pieces.

    A filer disclosing a single region and no total defeats this, because one
    member is not yet a breakdown. The named geographic axes are kept as a
    second signal for that case.
    """
    split: dict[tuple[str, str, int | None], dict[str, set[str]]] = {}
    keys = [(members[i], fact.period or "", fact.months)
            for i, fact in enumerate(candidates)]
    for i, fact in enumerate(candidates):
        seen = split.setdefault(keys[i], {})
        for axis, member in fact.members.items():
            if member != members[i]:
                seen.setdefault(axis, set()).add(member)
    slices: set[int] = set()
    for i, fact in enumerate(candidates):
        broken = {axis for axis, values in split[keys[i]].items() if len(values) > 1}
        if any(axis in broken for axis in fact.members) or not fact.is_worldwide:
            slices.add(i)
    return slices


def product_facts(
    facts: list[Fact], *, worldwide_only: bool = True, names_a_product=None
) -> list[Fact]:
    """Revenue facts sitting on the product axis, each the whole of its product.

    Empty is a real answer: a filing from before the issuer's tagging cutoff, or
    a product the issuer folds into an "other" line, states nothing here.

    ``names_a_product`` is how the product axis is found. Without it this reads
    only the spellings in ``PRODUCT_AXES`` - enough for a bulk row already
    normalised to them and for a us-gaap filing, and blind to every filer whose
    taxonomy names the axis something else.

    A filer qualifies a product's revenue in two different ways and only one of
    them makes a smaller figure. One tags a product's year twice - once plainly,
    once again under a collaboration arrangement that covers a part of it - and
    the second is a part of the first. Another tags every product under its
    business segment, and that qualification takes nothing away, because there
    is no less-qualified fact about the same product for it to be a part of.

    So the total is not recognised from a list of axes known to be harmless; it
    is the least-qualified statement the filer makes about that product and
    period, with any qualifier the filing itself uses to split that product
    read as a slice of it.
    """
    elements = revenue_elements(facts, names_a_product=names_a_product)
    candidates: list[Fact] = []
    members: list[str] = []
    for fact in facts:
        member = _product_member(fact, names_a_product)
        # Every condition `revenue_elements` selected the element under has to
        # hold of the fact as well: the element says what a number means, not
        # that this particular one is money over a period.
        if (
            member
            and fact.element in elements
            and fact.states_an_amount
            and fact.period
            and not _is_hypothetical(fact)
        ):
            candidates.append(fact)
            members.append(member)
    if not worldwide_only:
        # The caller has asked for the lines as well as the totals, so the
        # qualified facts are the point rather than something to see past.
        return candidates
    slices = _is_a_slice(candidates, members)
    kept = [(members[i], fact) for i, fact in enumerate(candidates) if i not in slices]
    plainest: dict[tuple[str, str, int | None], int] = {}
    for member, fact in kept:
        key = (member, fact.period or "", fact.months)
        qualifiers = len(fact.members) - 1
        if key not in plainest or qualifiers < plainest[key]:
            plainest[key] = qualifiers
    return [
        fact for member, fact in kept
        if len(fact.members) - 1 == plainest[(member, fact.period or "", fact.months)]
    ]
