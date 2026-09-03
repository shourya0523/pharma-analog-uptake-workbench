"""Tests for the orchestrator's unit-scaling fallback on LLM candidates.

The LLM extraction path returns a free-text ``unit`` field per
revenue_extractor.yaml (e.g. "millions", "thousands", "units") and no
value_normalized_usd_millions, unlike the deterministic table path which
always fills that field itself (see app/extraction/candidates.py). The
orchestrator used to fall back to an inline if/elif/else that only handled
"billion" and "thousand" substrings and silently assumed millions for
anything else - including "units", which is off by a factor of one million
(e.g. a Remodulin-style "205000 units" figure, 0.205 USD millions, would
have been persisted as 205000 USD millions).
"""

from __future__ import annotations

import pytest

from app.pipeline.orchestrator import scale_to_millions


def test_units_scale_down_by_one_million():
    assert scale_to_millions(205000, "units") == pytest.approx(0.205)


def test_thousands_scale_down_by_one_thousand():
    assert scale_to_millions(121718, "thousands") == pytest.approx(121.718)


def test_billions_scale_up_by_one_thousand():
    assert scale_to_millions(1.2, "billions") == pytest.approx(1200.0)


def test_millions_pass_through_unchanged():
    assert scale_to_millions(101.8, "millions") == pytest.approx(101.8)


def test_missing_unit_defaults_to_millions():
    assert scale_to_millions(101.8, None) == pytest.approx(101.8)
    assert scale_to_millions(101.8, "") == pytest.approx(101.8)


def test_unrecognized_unit_defaults_to_millions():
    assert scale_to_millions(101.8, "widgets") == pytest.approx(101.8)


def test_case_and_plural_insensitive():
    assert scale_to_millions(205000, "Units") == pytest.approx(0.205)
    assert scale_to_millions(1.2, "Billion") == pytest.approx(1200.0)
    assert scale_to_millions(121718, "Thousand") == pytest.approx(121.718)
