"""The scoring contract must describe every attribute the pipeline derives.

`scripts/profile_contract.py` says, per attribute, how a derived value is
compared with an answer key's. An attribute the pipeline produces and the
contract does not name is simply not scored, and nothing else would say so -
the eval would print a clean table with a column missing from it.

The contract lives under scripts/ because it reads answer keys, which is what a
scorer does and what the thing being scored must never do. This test is the
join between the two.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.analytics.profile_attributes import AnalogProfile

CONTRACT = Path(__file__).resolve().parents[2] / "scripts" / "profile_contract.py"

# Attributes that say where a value came from rather than what it is. They are
# not compared with an answer key because they describe the producer: an answer
# key curated by a person and a row derived by the pipeline disagree on them by
# definition, and should.
PROVENANCE_FIELDS = frozenset(
    {"drug_name", "attribute_provenance", "competitive_intensity_basis"}
)


def _contract():
    spec = importlib.util.spec_from_file_location("profile_contract", CONTRACT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_derived_attribute_is_in_the_contract():
    derived = set(AnalogProfile(drug_name="Calderon").as_row()) - PROVENANCE_FIELDS
    missing = sorted(derived - set(_contract().COMPARISON))
    assert not missing, (
        "the pipeline derives these and the contract does not say how to "
        f"compare them, so they are not scored: {missing}"
    )


def test_the_contract_describes_nothing_the_pipeline_does_not_derive():
    """A name the derivation dropped would leave a rule nothing can exercise."""
    derived = set(AnalogProfile(drug_name="Calderon").as_row())
    stale = sorted(set(_contract().COMPARISON) - derived)
    assert not stale, f"the contract compares attributes nothing produces: {stale}"


def test_every_comparison_is_one_the_contract_implements():
    """A rule named but not handled falls through to string equality, which is
    the wrong answer for a year, an era or a grouping key."""
    contract = _contract()
    known = {contract.EXACT, contract.YEAR, contract.ERA, contract.PARTITION, contract.PROSE}
    unknown = sorted(
        f"{name}={how}" for name, how in contract.COMPARISON.items() if how not in known
    )
    assert not unknown, unknown
