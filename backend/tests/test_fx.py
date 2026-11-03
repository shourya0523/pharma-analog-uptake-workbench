"""H.10 conversion, against a page served from memory."""

from __future__ import annotations

import pytest

import app.fx as fx

PAGE = """<h3>(Rates in Japanese yen per U.S. dollar)</h3>
<tr><th headers="a1">2-JAN-24</th><td>100.0</td></tr>
<tr><th headers="a1">3-JAN-24</th><td>ND</td></tr>
<tr><th headers="a1">1-APR-24</th><td>200.0</td></tr>"""


@pytest.fixture(autouse=True)
def page(monkeypatch):
    fx.daily_quotes.cache_clear()

    class Response:
        text = PAGE

    monkeypatch.setattr(fx.httpx, "get", lambda *a, **k: Response())
    yield
    fx.daily_quotes.cache_clear()


def test_a_per_dollar_quotation_is_inverted_and_nd_days_skipped():
    assert fx.usd_per_unit("JPY", 2024, 1) == pytest.approx(0.01)


def test_a_year_averages_its_days_and_usd_is_one():
    assert fx.usd_per_unit("JPY", 2024) == pytest.approx(1 / 150)  # mean quote 150, inverted once
    assert fx.to_usd(1000, "JPY", 2024, 2) == pytest.approx(5)
    assert fx.usd_per_unit("USD", 2024, 1) == 1.0


def test_an_unknown_currency_or_period_raises():
    with pytest.raises(fx.UnknownCurrency):
        fx.usd_per_unit("XYZ", 2024, 1)
    with pytest.raises(LookupError):
        fx.usd_per_unit("JPY", 2019, 1)
