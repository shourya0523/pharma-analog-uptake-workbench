from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import LineOfTherapy

_TOP_SECTION = re.compile(r"^\d+\s+INDICATIONS AND USAGE\b[:\s]*", re.IGNORECASE)
_SUBSECTION_SPLIT = re.compile(r"(?=\b\d+\.\d+\s+[A-Za-z])")
_SUBSECTION_TITLE = re.compile(r"^\d+\.\d+\s+([^.]{2,100}?)\s+(?=[A-Z][a-z]|[A-Z]{2,})")
_INDICATED_FOR = re.compile(
    r"indicated(?:\s+for\s+the\s+treatment\s+of|\s+for\s+treatment\s+of|\s+for|\s+to\s+treat)\s*:?\s*",
    re.IGNORECASE,
)
_DISEASE_CUT = re.compile(
    r"\s+to\s+improve\b|"
    r"\s+Studies?\s+establishing\b|"
    r"\s+The\s+(?:study|effects?|controlled)\b|"
    r"\s+Effectiveness\s+was\b|"
    r"\s+While\s+there\s+are\b|"
    r"\s*\(\s*\d+\.\d+\s*\)",
    re.IGNORECASE,
)
_BRAND_BOILERPLATE = re.compile(
    r"^[A-Z0-9 ®™]{2,40}\s+(?:is|are)\s+(?:a|an)\s+[^.]{0,120}?(?:indicated\s+for(?:\s+the\s+treatment\s+of)?\s*:?\s*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LoTClassification:
    value: LineOfTherapy
    source_quote: str
    extraction_method: str = "deterministic_label_rule"
    requires_review: bool = False


@dataclass(frozen=True)
class ParsedIndication:
    disease: str
    setting: str | None
    population: str | None
    biomarker: str | None
    source_quote: str
    approved_lot: LoTClassification


def classify_approved_lot(text: str) -> LoTClassification:
    quote = " ".join((text or "").split())
    if not quote:
        return LoTClassification(LineOfTherapy.UNRESOLVED, quote)
    lowered = quote.lower()
    first_line = bool(re.search(r"\b(first[\s-]line|initial therapy|treatment-naive)\b", lowered))
    third_plus = bool(
        re.search(r"\b(after|following).{0,30}(at least\s+)?(two|2)\s+prior\s+(lines?|regimens?)\b", lowered)
    )
    second_plus = bool(
        re.search(
            r"\b(after|following).{0,30}(one|1)\s+prior(?:\s+\w+){0,2}\s+(line|regimen|therapy)\b",
            lowered,
        )
    )
    if first_line and (second_plus or third_plus):
        return LoTClassification(LineOfTherapy.UNRESOLVED, quote, requires_review=True)
    if third_plus:
        return LoTClassification(LineOfTherapy.THIRD_LINE_PLUS, quote)
    if second_plus:
        return LoTClassification(LineOfTherapy.SECOND_LINE_PLUS, quote)
    if first_line:
        return LoTClassification(LineOfTherapy.FIRST_LINE, quote)
    if re.search(r"\b(previously treated|prior treatment|refractory|relapsed)\b", lowered):
        return LoTClassification(LineOfTherapy.SUBSEQUENT_UNSPECIFIED, quote)
    return LoTClassification(LineOfTherapy.ALL_LINES_OR_UNSPECIFIED, quote)


def _extract(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0) if match else None


def _normalize_block(text: str) -> str:
    return " ".join((text or "").split()).strip(" -•\t")


def _disease_from_block(block: str) -> str:
    """Pull a short disease/indication label out of an FDA indication paragraph."""
    text = _normalize_block(block)
    text = _SUBSECTION_TITLE.sub("", text, count=1).strip()
    text = _BRAND_BOILERPLATE.sub("", text).strip()
    if _INDICATED_FOR.search(text):
        text = _INDICATED_FOR.split(text, maxsplit=1)[-1].strip()
    text = _DISEASE_CUT.split(text, maxsplit=1)[0]
    text = re.split(
        r"\b(after|following|in adults?|in pediatric)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = text.strip(" .;:/-")
    # Drop leftover "treatment of" crumbs
    text = re.sub(r"^(?:the\s+)?treatment\s+of\s+", "", text, flags=re.IGNORECASE).strip(" .;:")
    return text or _normalize_block(block)


def _split_indication_blocks(text: str) -> list[str]:
    cleaned = _normalize_block(text)
    cleaned = _TOP_SECTION.sub("", cleaned).strip()
    if not cleaned:
        return []

    numbered = [part.strip() for part in _SUBSECTION_SPLIT.split(cleaned) if part.strip()]
    # Prefer numbered 1.1 / 1.2 bodies when present (skip the preamble list if any).
    subsection_bodies = [part for part in numbered if re.match(r"^\d+\.\d+\b", part)]
    if len(subsection_bodies) >= 2:
        return subsection_bodies
    if len(subsection_bodies) == 1 and len(numbered) == 1:
        return subsection_bodies

    # Fall back to blank-line / bullet splits used by cleaner fixtures.
    blocks = [
        _normalize_block(block)
        for block in re.split(r"\n\s*\n|(?:^|\n)\s*[•*-]\s+", text or "")
        if block.strip()
    ]
    return blocks or ([cleaned] if cleaned else [])


def parse_indications(text: str) -> list[ParsedIndication]:
    """Split explicit label indication paragraphs and classify only stated LoT evidence."""

    records: list[ParsedIndication] = []
    seen: set[str] = set()
    for block in _split_indication_blocks(text):
        disease = _disease_from_block(block)
        # Skip preamble crumbs that are still section titles without a disease.
        if re.fullmatch(r"\d+(?:\.\d+)*\s+[A-Za-z][A-Za-z\s/-]{2,60}", disease):
            continue
        key = disease.casefold()
        if key in seen:
            continue
        seen.add(key)
        setting = _extract(r"\b(metastatic|adjuvant|neoadjuvant|maintenance|advanced)\b", block)
        population = _extract(r"\b(adults?|pediatric patients?|children|adolescents?)\b", block)
        biomarker = _extract(r"\b[A-Z0-9]+(?:[-/][A-Z0-9]+)*-(?:positive|negative)\b", block)
        records.append(
            ParsedIndication(
                disease=disease,
                setting=setting.lower() if setting else None,
                population=population.lower() if population else None,
                biomarker=biomarker,
                source_quote=_normalize_block(block),
                approved_lot=classify_approved_lot(block),
            )
        )
    return records
