"""Read an openFDA drug record by the keys the record itself carries.

Two datasets answer with different shapes: ``api.fda.gov/drug/drugsfda.json``
returns an application with a ``products`` array and a ``submissions`` array,
and ``api.fda.gov/drug/label.json`` returns an SPL with prose sections and no
``products`` array at all. Both may carry an ``openfda`` block, and on an older
or discontinued application that block can be absent entirely.

So nothing here decides in advance which keys a record has. The keys are read
off the record, and each value records the path it came from, so a citation can
name a key that exists and a fact stated in two places can be reported as two
readings rather than one preference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# FDA label sections often arrive as "12.1 Mechanism of Action <prose>".
_MOA_SECTION_HEADER = re.compile(
    r"^\s*\d+(?:\.\d+)*\s+(?:Mechanism of Action|CLINICAL PHARMACOLOGY)\b[:\s]*",
    re.IGNORECASE,
)

# A record enumerates its own keys but not which key carries which fact, and
# that mapping is the one thing here that cannot be read off the document. It
# is a snapshot of the two openFDA drug endpoints named in the module
# docstring, in preference order per fact; it goes stale when either endpoint
# renames a key, and the symptom is a fact that comes back empty with no path
# in `ParsedFDALabel.paths` rather than a wrong value. Everything else - which
# of these paths a given record actually has, and every other key in its
# `openfda` block - is derived at read time.
#
# Dosage form is drugsFDA-only on purpose: the label dataset states a strength
# sentence (`dosage_forms_and_strengths`) and an ingredient blob
# (`spl_product_data_elements`), neither of which is the structured dosage form
# that `products[].dosage_form` is, so a label-only record reports none.
_PATHS: dict[str, tuple[str, ...]] = {
    "brand_name": ("openfda.brand_name", "products[].brand_name"),
    "generic_name": ("openfda.generic_name",),
    "active_ingredients": (
        "openfda.substance_name",
        "products[].active_ingredients[].name",
    ),
    "application_number": ("openfda.application_number", "application_number"),
    "route": ("products[].route", "openfda.route"),
    "dosage_form": ("products[].dosage_form",),
    "pharm_class_epc": ("openfda.pharm_class_epc",),
    "pharm_class_moa": ("openfda.pharm_class_moa",),
    "moa_summary": ("mechanism_of_action",),
    "indications": ("indications_and_usage",),
}

# Route terms are written as a list on one path and as one comma-joined string
# on another, so a reading is compared as a set of terms rather than as text.
_ROUTE_SPLIT = re.compile(r"[;,/]")


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if item is not None and str(item).strip()]


def _first_section(record: dict[str, Any], key: str) -> str | None:
    values = _strings(record.get(key))
    return "\n".join(values) if values else None


def openfda_block(record: dict[str, Any]) -> dict[str, list[str]]:
    """Every key the record's own ``openfda`` block carries, as string lists."""
    block = record.get("openfda") or {}
    if not isinstance(block, dict):
        return {}
    return {str(key): _strings(value) for key, value in block.items()}


def product_columns(record: dict[str, Any]) -> dict[str, list[str]]:
    """Every scalar column the record's own ``products[]`` entries carry.

    Derived from the entries, so a column drugsFDA adds is readable without
    this being changed. Nested columns are skipped here and read by path.
    """
    columns: dict[str, list[str]] = {}
    for product in record.get("products") or []:
        if not isinstance(product, dict):
            continue
        for key, value in product.items():
            if isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if not text:
                continue
            seen = columns.setdefault(str(key), [])
            if text not in seen:
                seen.append(text)
    return columns


def _nested_product_values(record: dict[str, Any], column: str, inner: str) -> list[str]:
    values: list[str] = []
    for product in record.get("products") or []:
        if not isinstance(product, dict):
            continue
        for item in product.get(column) or []:
            if not isinstance(item, dict):
                continue
            for text in _strings(item.get(inner)):
                if text not in values:
                    values.append(text)
    return values


_NESTED_PRODUCT_PATH = re.compile(r"^products\[\]\.(\w+)\[\]\.(\w+)$")


def read_path(record: dict[str, Any], path: str) -> list[str]:
    """The values a record states at one of `_PATHS`' paths, or an empty list."""
    nested = _NESTED_PRODUCT_PATH.match(path)
    if nested:
        return _nested_product_values(record, nested.group(1), nested.group(2))
    if path.startswith("products[]."):
        return product_columns(record).get(path[len("products[].") :], [])
    if path.startswith("openfda."):
        return openfda_block(record).get(path[len("openfda.") :], [])
    return _strings(record.get(path))


def _readings(record: dict[str, Any], fact: str) -> dict[str, list[str]]:
    """Every path for one fact that the record actually states, path -> values."""
    found: dict[str, list[str]] = {}
    for path in _PATHS[fact]:
        values = read_path(record, path)
        if values:
            found[path] = values
    return found


def route_terms(values: list[str]) -> set[str]:
    """One route reading as a set of terms, however it was punctuated."""
    return {
        term.strip().casefold()
        for value in values
        for term in _ROUTE_SPLIT.split(value)
        if term.strip()
    }


def route_readings_agree(readings: dict[str, list[str]]) -> bool:
    """True when every path that states a route states the same route.

    `INTRAVENOUS, SUBCUTANEOUS` and `['INTRAVENOUS', 'SUBCUTANEOUS']` are one
    answer punctuated two ways. `Respiratory (inhalation)` and `Inhalation` are
    one answer at two grains, so a term that contains the other is not a
    disagreement. `Oral` and `Inhalation` are a disagreement.
    """
    sets = [route_terms(values) for values in readings.values() if values]
    if len(sets) < 2:
        return True
    first = sets[0]
    for other in sets[1:]:
        if not _terms_cover(first, other) and not _terms_cover(other, first):
            return False
    return True


def _terms_cover(wide: set[str], narrow: set[str]) -> bool:
    return all(any(term in other or other in term for other in wide) for term in narrow)


def clean_moa_summary(text: str | None) -> str | None:
    """Strip label section numbering/headers from mechanism prose."""
    if not text:
        return None
    cleaned = _MOA_SECTION_HEADER.sub("", str(text).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.lstrip(" :.-")
    return cleaned or None


def format_moa_profile_value(moa_terms: list[str], moa_summary: str | None) -> str | None:
    """Prefer cleaned descriptive MoA prose; fall back to structured MoA class terms."""
    summary = clean_moa_summary(moa_summary)
    if summary:
        return summary
    terms = [term.strip() for term in moa_terms if term and str(term).strip()]
    if terms:
        return "; ".join(terms)
    return None


@dataclass(frozen=True)
class ParsedFDALabel:
    brand_names: list[str] = field(default_factory=list)
    generic_names: list[str] = field(default_factory=list)
    active_ingredients: list[str] = field(default_factory=list)
    application_numbers: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    dosage_forms: list[str] = field(default_factory=list)
    epc_terms: list[str] = field(default_factory=list)
    moa_terms: list[str] = field(default_factory=list)
    moa_summary: str | None = None
    indications_text: str | None = None
    # path -> the values that path stated, for every fact stated more than once.
    route_readings: dict[str, list[str]] = field(default_factory=dict)
    # fact name -> the path its value was read from, for citations.
    paths: dict[str, str] = field(default_factory=dict)

    @property
    def route_conflict(self) -> bool:
        return not route_readings_agree(self.route_readings)

    def path(self, fact: str) -> str | None:
        return self.paths.get(fact)


def parse_label_record(record: dict[str, Any]) -> ParsedFDALabel:
    """Parse one openFDA record by whichever of `_PATHS` it states.

    EPC and MoA stay separate. Route keeps every path that stated one, because
    the two datasets disagree about it and the disagreement is the finding,
    not something to resolve here.
    """

    readings = {fact: _readings(record, fact) for fact in _PATHS}

    def chosen(fact: str) -> tuple[list[str], str | None]:
        for path, values in readings[fact].items():
            return values, path
        return [], None

    values: dict[str, list[str]] = {}
    paths: dict[str, str] = {}
    for fact in _PATHS:
        found, path = chosen(fact)
        values[fact] = found
        if path:
            paths[fact] = path

    moa_summary = clean_moa_summary("\n".join(values["moa_summary"]) or None)
    indications_text = _first_section(record, "indications_and_usage")
    if not moa_summary:
        paths.pop("moa_summary", None)

    return ParsedFDALabel(
        brand_names=values["brand_name"],
        generic_names=values["generic_name"],
        active_ingredients=values["active_ingredients"],
        application_numbers=values["application_number"],
        routes=values["route"],
        dosage_forms=values["dosage_form"],
        epc_terms=values["pharm_class_epc"],
        moa_terms=values["pharm_class_moa"],
        moa_summary=moa_summary,
        indications_text=indications_text,
        route_readings=readings["route"],
        paths=paths,
    )
