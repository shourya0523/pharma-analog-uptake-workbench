from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import LineOfTherapy


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


def parse_indications(text: str) -> list[ParsedIndication]:
    """Split explicit label indication paragraphs and classify only stated LoT evidence."""

    blocks = [
        " ".join(block.split()).strip(" -•\t")
        for block in re.split(r"\n\s*\n|(?:^|\n)\s*[•*-]\s+", text or "")
        if block.strip()
    ]
    records: list[ParsedIndication] = []
    for block in blocks:
        setting = _extract(r"\b(metastatic|adjuvant|neoadjuvant|maintenance|advanced)\b", block)
        population = _extract(r"\b(adults?|pediatric patients?|children|adolescents?)\b", block)
        biomarker = _extract(r"\b[A-Z0-9]+(?:[-/][A-Z0-9]+)*-(?:positive|negative)\b", block)
        disease = re.sub(r"^(is\s+)?indicated\s+(as\s+[^.]+?\s+)?for\s+", "", block, flags=re.IGNORECASE)
        disease = re.split(r"\b(after|following|in adults?|in pediatric)\b", disease, maxsplit=1, flags=re.IGNORECASE)[0]
        disease = disease.strip(" .") or block
        records.append(
            ParsedIndication(
                disease=disease,
                setting=setting.lower() if setting else None,
                population=population.lower() if population else None,
                biomarker=biomarker,
                source_quote=block,
                approved_lot=classify_approved_lot(block),
            )
        )
    return records

