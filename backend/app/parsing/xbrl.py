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
product axis long before their own deadline - Gilead from 2009, United
Therapeutics from 2011. ``filer_category`` reads the issuer's own declaration
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

# The axis that says which product a fact is about, in both of its spellings.
# It lived in the us-gaap namespace until the 2018 taxonomy moved the reporting
# axes into srt, so a filing from before that names the same axis differently -
# Gilead's 2013 Q3 instance and United Therapeutics' 2016 Q3 one both say
# "us-gaap:ProductOrServiceAxis". Knowing only the modern spelling reads those
# filings as having no product facts at all, which is what happened.
#
# PRODUCT_AXIS stays the canonical one: it is what a fact is keyed under once
# `notes_datasets` normalises a bulk row, and what the tests construct.
PRODUCT_AXIS = "srt:ProductOrServiceAxis"
PRODUCT_AXES = (PRODUCT_AXIS, "us-gaap:ProductOrServiceAxis")
GEOGRAPHIC_AXES = ("srt:StatementGeographicalAxis", "us-gaap:StatementGeographicalAxis")

# What a revenue fact is called. Only the standard taxonomy's revenue elements
# count: a filer's own extension is, by definition, a measure the taxonomy has
# no name for, and reading one as the product's revenue is how
# ``uthr:GrossProfitExcludingOtherRevenue`` came to be published beside the
# revenue it is derived from - two different figures for one quarter, each with
# a citation saying it was tagged.
#
# Matching on the word "Revenue" is what admitted them. It also admits a
# percentage (``CollaborationArrangementPercentOfGlobalProductRevenues``) and a
# contract term (``...NetProductSalesThreshold``), neither of which is money.
_REVENUE_ELEMENTS = frozenset({
    "us-gaap:Revenues",
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    # Pre-606 names, for filings tagged before the standard was adopted.
    "us-gaap:SalesRevenueNet",
    "us-gaap:SalesRevenueGoodsNet",
})

# A forecast is not a report. Merck tags next quarter's expectation for a
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
        the next one. Johnson & Johnson's fiscal 2022 runs to 1 January 2023;
        reading the end date alone files it as 2023, on top of the real 2023,
        and both then carry a citation saying so.
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
        return f"{self.element} [{span}] {where} = {self.value:g}".strip()


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


def parse_facts(raw: bytes) -> list[Fact]:
    """Every numeric fact in the instance, with its context resolved."""
    prefixes = _prefixes(raw)
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
        facts.append(Fact(
            element=_qname(node.tag, prefixes),
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


def product_facts(facts: list[Fact], *, worldwide_only: bool = True) -> list[Fact]:
    """Revenue facts sitting on the product axis, each the whole of its product.

    Empty is a real answer: a filing from before the issuer's tagging cutoff, or
    a product the issuer folds into an "other" line, states nothing here.

    A filer qualifies a product's revenue in two different ways and only one of
    them makes a smaller figure. United Therapeutics tags Adcirca's year twice -
    once plainly at $41.3m, once under the Eli Lilly arrangement at $1.0m - and
    the second is a part of the first. Johnson & Johnson tags every product
    under its business segment, and that qualification takes nothing away,
    because there is no less-qualified Stelara fact to be a part of.

    So the total is not recognised from a list of axes that are known to be
    harmless; it is the least-qualified statement the filer makes about that
    product and period. An axis nobody has seen before subsets a figure or it
    does not, and either way this reads it the same as the filer wrote it.
    """
    candidates = [
        fact
        for fact in facts
        if fact.product_member
        and fact.element in _REVENUE_ELEMENTS
        and fact.period
        and not _is_hypothetical(fact)
        and (fact.is_worldwide or not worldwide_only)
    ]
    if not worldwide_only:
        # The caller has asked for the lines as well as the totals, so the
        # qualified facts are the point rather than something to see past.
        return candidates
    plainest: dict[tuple[str, str, int | None], int] = {}
    for fact in candidates:
        key = (fact.product_member or "", fact.period or "", fact.months)
        qualifiers = len(fact.members) - 1
        if key not in plainest or qualifiers < plainest[key]:
            plainest[key] = qualifiers
    return [
        fact
        for fact in candidates
        if len(fact.members) - 1
        == plainest[(fact.product_member or "", fact.period or "", fact.months)]
    ]
