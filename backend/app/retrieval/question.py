"""What a revenue question asks, stated as fields rather than left to the reader.

A product-quarter is not yet a question. The same filing answers "Calderon,
Q2 2023" several ways - the brand's own line, the brand with its other
presentations, the U.S. part, the figure a partner books - and choosing among
them by what an answer key happens to hold is the answer key deciding. Each
field below is one such choice, made by whoever asks, and each has a general
policy the routes apply to every product alike.

Policy - what the fields cannot settle, and goes to a review queue instead of
being answered:

- A territory other than the whole world or the United States ("ex-U.S.",
  "Europe", "Japan", "U.S. and Canada"). Filers split by place in ways that
  are not known to be exhaustive, so a region is not derived from parts; it
  is read only where a route prints that region itself, which no route here
  does yet.
- "Worldwide as booked by X" without `booked_by`. When a partner or licensee
  sells the product in its own territory, the issuer's figure and the
  partner's are different questions, and nothing in either filing says which
  one was meant.
- A share rather than sales: collaboration revenue, a royalty, a profit split.
  The XBRL route excludes those concepts by design, so a question for them is
  outside every route.
- A quarter in which the product changed hands. Neither owner's filing covers
  the whole quarter; the ownership route has to establish who booked which
  days before the two partial figures can be added.
- A brand family whose members are not all tagged, or a combined line when
  `combined_ok` is false. The first is a sum missing an addend; the second
  is a figure known to cover more than the product.
- Wording `from_text` does not recognise. It is kept in `unresolved` rather
  than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

SCOPES = ("worldwide", "us")
LINES = ("brand", "family", "form")


@dataclass(frozen=True)
class Question:
    """One product-quarter question and the judgement calls it carries.

    `product` - the name the routes look for. For `line="form"` this is the
    form's own name ("Nebulized Calderon"), not the brand's.

    `period` - a calendar quarter, "Q2 2023".

    `issuer` - the company that markets the product, as a name the filings
    use. None lets the routes find the filer that tags the product.

    `scope` - "worldwide", the product's whole revenue as the booking filer
    reports it, or "us", the part that filer attributes to the United States.
    A U.S. figure is accepted only where a route shows it is the U.S. part; a
    figure with no U.S. split is not taken to be U.S. because the product
    happens to sell only there.

    `line` - which line of the filer's report:
    "brand", the line named for the brand alone (`CalderonMember`, or a
    category prefix before it); two lines that both close with the brand's
    name are two scopes, and the question is declined rather than summed.
    "family", the brand and every presentation of it the filer reports apart
    (`CalderonCreamMember` + `CalderonFoamMember`, `CalderonMember` +
    `NebulizedCalderonMember`), summed; a total over the presentations is
    taken alone.
    "form", one presentation named in `product`; read as a brand line under
    that name.

    `booked_by` - the company whose booking is wanted, when that is not the
    issuer: a partner selling the product in its own territory. The routes
    read that company's filings; `issuer` stays the product's owner. None is
    the issuer's own booking.

    `combined_ok` - whether a line that also covers a named sibling
    (`CalderonAndNuVessaMember`) answers the question. False declines it.

    `unresolved` - words of a description that no field expresses; a
    question carrying any is a review item.
    """

    product: str
    period: str
    issuer: str | None = None
    scope: str = "worldwide"
    line: str = "brand"
    booked_by: str | None = None
    combined_ok: bool = False
    unresolved: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}, not {self.scope!r}")
        if self.line not in LINES:
            raise ValueError(f"line must be one of {LINES}, not {self.line!r}")

    @property
    def family(self) -> bool:
        """Whether the routes should sum the brand's presentations."""
        return self.line == "family"

    @property
    def filer(self) -> str | None:
        """The company whose filings answer: the booking partner if named,
        else the issuer."""
        return self.booked_by or self.issuer

    def accepts(self, match: str | None) -> bool:
        """Whether an answer read with `match` (exact | combined | family |
        single_product | None) answers this question."""
        if match == "combined":
            return self.combined_ok
        if match == "family":
            return self.family
        return True

    def review_reason(self) -> str | None:
        """Why the question cannot be answered by the routes alone, or None."""
        if self.unresolved:
            return "unresolved wording: " + ", ".join(self.unresolved)
        return None

    def pipeline_kwargs(self) -> dict[str, Any]:
        """Arguments for `quarter_revenue` and the XBRL readers: the filer to
        read, the scope and whether the family policy applies."""
        return {"issuer": self.filer, "scope": self.scope, "family": self.family}

    def with_(self, **changes: Any) -> Question:
        """A copy with fields changed, validated again."""
        return replace(self, **changes)

    @classmethod
    def from_text(cls, product: str, period: str, issuer: str | None = None, description: str = "") -> Question:
        """A question from a free-text description of its scope.

        Each recognised phrase sets a field and is removed from the text:
        "worldwide"/"global"; "U.S."/"United States" (scope us); "family",
        "franchise", "brand total", "all formulations" (line family);
        "formulation-specific" (line form); "booked by X", "recorded by X",
        "reported by X", "X-booked" (booked_by); "combined" (combined_ok).
        Words left over that only describe the figure ("net product sales")
        are dropped; any other word - a region, a share, a qualifier - is
        kept in `unresolved`, so wording this does not know becomes a review
        item rather than a default. "Ex-U.S." and "U.S. and Canada" are
        not read as U.S.: the phrase is consumed whole and left unresolved.
        `product` and `issuer` are dropped from the leftovers too.
        """
        text = " ".join(description.split())
        scope, line, booked_by, combined_ok = "worldwide", "brand", None, False
        unresolved: list[str] = []

        def take(pattern: str, flags: int = re.IGNORECASE) -> re.Match | None:
            nonlocal text
            m = re.search(pattern, text, flags)
            if m:
                text = text[: m.start()] + " " + text[m.end():]
            return m

        for pattern in _TERRITORIES_BEYOND:
            m = take(pattern)
            if m:
                unresolved.append(m.group(0))
        booked = take(r"\b(?:booked|recorded|reported)\s+by\s+([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*)*)", 0) \
            or take(r"\b([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*)*)-booked\b", 0)
        # "Company-booked" names the issuer's own booking, which is the default.
        if booked and booked.group(1).lower() not in ("company", "issuer"):
            booked_by = booked.group(1)
        if take(r"\b(?:u\.\s?s\.?|us|united states)(?![a-z])"):
            scope = "us"
        if take(r"\b(?:worldwide|global)\b") and scope == "us":
            unresolved.append("both worldwide and U.S.")
        if take(r"\b(?:product\s+)?(?:family|franchise)\b|\bbrand total\b|\ball (?:formulations|forms|presentations)\b"):
            line = "family"
        if take(r"\b(?:formulation|form)[- ]specific\b"):
            line = "form" if line == "brand" else line
            if line == "family":
                unresolved.append("both family and formulation-specific")
        if take(r"\bcombined\b"):
            combined_ok = True
        for name in (product, issuer or ""):
            if name:
                text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
        leftover = [w for w in re.findall(r"[A-Za-z][A-Za-z.'-]*", text) if w.lower().strip(".") not in _DESCRIPTIVE]
        if leftover:
            unresolved.append(" ".join(leftover))
        return cls(product, period, issuer, scope, line, booked_by, combined_ok, tuple(dict.fromkeys(unresolved)))


# Phrases that name more or less than the U.S. while containing it. Each is
# consumed whole before "U.S." is looked for, so that it cannot set the U.S.
# scope; it is left unresolved.
_TERRITORIES_BEYOND = (
    r"\b(?:ex|non|outside(?:\s+the)?)[- ]?(?:u\.\s?s\.?|us|united states)(?![a-z])",
    r"\b(?:u\.\s?s\.?|us|united states)\s+(?:and|plus|&)\s+[A-Za-z]+",
)
# Words that say what kind of figure is meant without changing which one: a
# snapshot of how revenue lines are labelled. A word outside it is not
# dropped but kept as unresolved.
_DESCRIPTIVE = {
    "net", "gross", "product", "products", "sales", "sale", "revenue", "revenues", "line", "lines", "as",
    "printed", "reported", "the", "of", "a", "an", "in", "its", "total", "brand", "only", "and", "by", "s",
}
