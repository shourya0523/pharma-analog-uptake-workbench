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

The catch is that it does not reach far. Detail tagging of the revenue note
arrived with inline XBRL, phased by filer size: fiscal periods ending on or
after 15 June 2019 for large accelerated filers, 2020 for accelerated, 2021 for
everyone else. So the cutoff is a property of the issuer, not a date in this
file - ``filer_category`` reads the issuer's own declaration of which it is, and
``product_facts`` simply returns nothing for a filing that predates its own
cutoff, which is the honest way for a reader to say "not here".
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

PRODUCT_AXIS = "srt:ProductOrServiceAxis"
GEOGRAPHIC_AXES = ("srt:StatementGeographicalAxis", "us-gaap:StatementGeographicalAxis")

# What a revenue fact is called. Filers pick among these under ASC 606; the
# element matters less than the axis it sits on, so this is deliberately broad
# and the axis does the work of saying "this is a product's sales".
_REVENUE_ELEMENT = re.compile(r"Revenue|SalesRevenue|ProductSales", re.IGNORECASE)


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
        """Canonical label: 2024Q3 for a quarter, 2024 for a year."""
        if not self.end:
            return None
        months = self.months
        if months == 3:
            return f"{self.end.year}Q{(self.end.month - 1) // 3 + 1}"
        if months == 12:
            return str(self.end.year)
        return None

    @property
    def product_member(self) -> str | None:
        return self.members.get(PRODUCT_AXIS)

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


def product_facts(facts: list[Fact], *, worldwide_only: bool = True) -> list[Fact]:
    """Revenue facts sitting on the product axis.

    Empty is a real answer: a filing from before the issuer's tagging cutoff, or
    a product the issuer folds into an "other" line, states nothing here.
    """
    return [
        fact
        for fact in facts
        if fact.product_member
        and _REVENUE_ELEMENT.search(fact.element)
        and fact.period
        and (fact.is_worldwide or not worldwide_only)
    ]
