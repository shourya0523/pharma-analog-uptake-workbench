"""The pipeline held-out set must not touch an issuer any answer key uses.

`seed/holdout_pipeline/cases.jsonl` scores changes to the retrieval pipeline.
Its number means something only while none of its issuers, filers or products
is held by an answer key a change could have been tuned against. So this
checks that property, and that the set cannot be passed by a pipeline that
always answers, or always refuses - not the score.

The answer keys are discovered, not named: `seed/gold/`, `seed/cases/`, the
analog profiles and consensus estimates (answer-key side until shown
otherwise), and any other `seed/holdout*` set that appears beside this one.
"""

from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "seed"
HOLDOUT = SEED / "holdout_pipeline"
CASES = HOLDOUT / "cases.jsonl"

MIN_PER_ANSWER = 8
EDGAR_CIK = re.compile(r"sec\.gov/(?:Archives/edgar/data|cgi-bin/browse-edgar\?[^\s\"']*CIK=)/?0*(\d+)", re.I)


def _cases() -> list[dict]:
    return [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]


def _answer_key_files() -> list[Path]:
    """Every file on the answer-key side of CLAUDE.md rule 3, found by walking.

    Profiles and consensus estimates are matched by the stems the repository
    layout gives them; a new answer-key directory under seed/ named holdout*
    is picked up without editing this.
    """
    roots = [SEED / "gold", SEED / "cases"]
    roots += [p for p in SEED.glob("holdout*") if p.resolve() != HOLDOUT.resolve()]
    roots += [p for p in SEED.glob("*.csv") if p.stem.startswith(("analog_profile", "consensus"))]
    files: list[Path] = []
    for root in roots:
        files += [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    return files


def _records(path: Path):
    """Yield every JSON object or CSV row a file holds; nothing for other text."""
    text = path.read_text(errors="replace")
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)
    elif path.suffix == ".json":
        yield json.loads(text)
    elif path.suffix == ".csv":
        yield from csv.DictReader(text.splitlines())


def _walk(obj, key=None):
    """Yield (key, string) for every scalar under obj, keyed by its nearest dict key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)
    elif obj is not None:
        yield key, str(obj)


@lru_cache(maxsize=1)
def _answer_keys() -> tuple[str, set[int], set[str], set[str]]:
    """Return (all text, CIKs, issuer-name first words, product names) the answer keys hold.

    CIKs come from every EDGAR URL in any file and from any field named `cik`.
    Issuer names come from any field whose name says it holds a company, and
    product names from `drug_name`, so a field added to gold is read without
    this changing.
    """
    texts, ciks, issuer_words, products = [], set(), set(), set()
    for path in _answer_key_files():
        text = path.read_text(errors="replace")
        texts.append(text)
        ciks |= {int(c) for c in EDGAR_CIK.findall(text)}
        try:
            records = list(_records(path))
        except (json.JSONDecodeError, csv.Error, UnicodeDecodeError):
            continue
        for record in records:
            for key, value in _walk(record):
                k = (key or "").lower()
                if k == "cik" and value.strip().isdigit():
                    ciks.add(int(value))
                elif re.search(r"manufacturer|issuer|company|filer|marketer", k):
                    words = re.findall(r"[a-z0-9][a-z0-9\-]*", value.lower())
                    if words:
                        issuer_words.add(words[0])
                elif k == "drug_name":
                    products.add(value.strip().lower())
    return "\n".join(texts).lower(), ciks, issuer_words, products


def _core_name(issuer: str) -> str:
    """The issuer's name without its legal-form suffix.

    `Calderon Therapeutics, Inc.` -> `calderon therapeutics`; `NuVessa A/S` ->
    `nuvessa`. Trailing tokens of four characters or fewer are legal forms
    (Inc, Corp, Ltd, AB, A/S, plc, SE) in every name this set uses; a name made
    only of short tokens keeps its first one.
    """
    tokens = re.findall(r"[a-z0-9][a-z0-9\-/]*", issuer.lower())
    while len(tokens) > 1 and len(tokens[-1].strip("./")) <= 4:
        tokens.pop()
    return " ".join(tokens)


def test_the_answer_keys_were_found():
    """Every check below is vacuous if the walk finds no answer key."""
    text, ciks, issuer_words, products = _answer_keys()
    assert text and ciks and issuer_words and products


def test_no_case_comes_from_an_answer_key_issuer():
    """No case's CIK, issuer name or issuer first word is held by an answer key."""
    text, ciks, issuer_words, _ = _answer_keys()
    for case in _cases():
        who = f"{case['product']} {case['period']} ({case['issuer']})"
        assert int(case["cik"]) not in ciks, f"{who}: CIK {case['cik']} is in an answer key"
        core = _core_name(case["issuer"])
        named = core in text and re.search(rf"\b{re.escape(core)}\b", text)
        assert not named, f"{who}: '{core}' appears in an answer key"
        first = core.split()[0]
        assert first not in issuer_words, f"{who}: an answer key names an issuer starting '{first}'"


def test_no_case_is_an_answer_key_product():
    """A product gold holds would be scored by gold already, whoever the issuer."""
    _, _, _, products = _answer_keys()
    for case in _cases():
        assert case["product"].lower() not in products, case["product"]


def test_both_answers_are_represented():
    """A set that only refuses is passed by a pipeline that always refuses, and
    one that only answers by a pipeline that returns any number it finds."""
    cases = _cases()
    values = [c for c in cases if c["expect"] == "value"]
    refusals = [c for c in cases if c["expect"] == "not_reported"]
    assert {c["expect"] for c in cases} == {"value", "not_reported"}
    assert len(values) >= MIN_PER_ANSWER, len(values)
    assert len(refusals) >= MIN_PER_ANSWER, len(refusals)
    assert len({c["reason"] for c in refusals}) >= 3, "refusals all fail for the same reason"


def test_every_case_carries_its_own_evidence():
    """Each label cites the primary document it was read from and quotes it."""
    for case in _cases():
        who = f"{case['product']} {case['period']}"
        assert re.fullmatch(r"Q[1-4] \d{4}", case["period"]), who
        assert case["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/"), who
        assert int(case["cik"]) == int(EDGAR_CIK.search(case["source_url"]).group(1)), who
        assert case["source_quote"].strip(), who
        if case["expect"] == "value":
            assert isinstance(case["value"], (int, float)) and case["value"] > 0, who
            assert case["unit"] and case["currency"] and case["scope"] and case["kind"], who
        else:
            assert case["value"] is None and case["reason"], who
