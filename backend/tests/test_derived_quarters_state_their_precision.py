"""A derived quarter is exact arithmetic on figures that were already rounded.

A derived fourth quarter can disagree with the issuer's own printed one by a
million while both are right about what they were computed from:

    Emravir 2022Q4     200 - (44 + 54 + 43)      = 59, issuer printed 58
    Cordexa 2022Q4   1,872 - (374 + 460 + 500)   = 538, issuer printed 537
    Velantis 2022Q4    127 - (32 + 33 + 32)      = 30, issuer printed 29
    Pyrenil 2022Q4   3,784 - (1,038 + 970 + 911) = 865, issuer printed 866
    NuVessa 2022Q4   2,184 - (571 + 566 + 545)   = 502, issuer printed 501

Every fact behind them carries `decimals="-6"`: the filer rounded to the
nearest million before tagging. Four inputs rounded that way put the residual
up to two million from the truth, and the issuer's own printed fourth quarter
- rounded once, from the unrounded number - is the better figure where it can
be had. It cannot be had from XBRL, which never tags a fourth quarter.

So the arithmetic is not wrong and is not worth "fixing". What was wrong is
that the result looked exactly as precise as a figure the filer stated. These
tests are about the number saying what it is.
"""

from __future__ import annotations

from app.extraction.derive import complete_series
from app.parsing.xbrl import Fact

# One product's 2022 facts, as the filer tagged them.
FY, Q1, Q2, Q3 = 200.0, 44.0, 54.0, 43.0
HALF_A_MILLION = 0.5


def _candidate(period, period_type, value, *, uncertainty=HALF_A_MILLION):
    return {
        "period": period,
        "period_type": period_type,
        "value_reported": value,
        "value_normalized_usd_millions": value,
        "currency": "USD",
        "unit": "millions",
        "rounding_uncertainty_usd_millions": uncertainty,
    }


def _complera(**kwargs):
    rows = [
        _candidate("2022", "annual", FY, **kwargs),
        _candidate("2022Q1", "quarterly", Q1, **kwargs),
        _candidate("2022Q2", "quarterly", Q2, **kwargs),
        _candidate("2022Q3", "quarterly", Q3, **kwargs),
    ]
    return [d for d in complete_series({"Complera": rows}, product="Complera")
            if d["period"] == "2022Q4"]


def test_the_derivation_still_produces_the_arithmetic_answer():
    """59, not 58. The pipeline must not start guessing at the issuer's number."""
    derived = _complera()
    assert len(derived) == 1
    assert derived[0]["value_normalized_usd_millions"] == FY - (Q1 + Q2 + Q3) == 59.0


def test_it_carries_one_rounding_per_input():
    """A year and three quarters, each to the nearest million: two million."""
    derived = _complera()
    assert derived[0]["rounding_uncertainty_usd_millions"] == 2.0


def test_the_quote_says_so_beside_the_number():
    """The quote is what a reader sees next to the value."""
    quote = _complera()[0]["source_quote"]
    assert "yields 2022Q4 59" in quote
    assert "+/- 2 from input rounding" in quote


def test_the_one_million_gap_to_the_issuers_own_figure_is_inside_the_bound():
    """The point of the bound: 58 and 59 are the same figure to this precision."""
    derived = _complera()[0]
    issuer_printed = 58.0
    gap = abs(derived["value_normalized_usd_millions"] - issuer_printed)
    assert gap <= derived["rounding_uncertainty_usd_millions"]


def test_an_input_that_never_said_its_precision_leaves_the_bound_unknown():
    """A bound from only the inputs that declared one understates the error.

    Unknown has to stay unknown, because a number claiming to be within half a
    million when nothing establishes that is worse than one claiming nothing.
    """
    rows = [
        _candidate("2022", "annual", FY),
        _candidate("2022Q1", "quarterly", Q1),
        _candidate("2022Q2", "quarterly", Q2, uncertainty=None),
        _candidate("2022Q3", "quarterly", Q3),
    ]
    derived = [d for d in complete_series({"Complera": rows}, product="Complera")
               if d["period"] == "2022Q4"]
    assert derived[0]["value_normalized_usd_millions"] == 59.0
    assert derived[0]["rounding_uncertainty_usd_millions"] is None
    assert "+/-" not in derived[0]["source_quote"]


def test_exactly_tagged_inputs_derive_an_exact_quarter():
    derived = _complera(uncertainty=0.0)
    assert derived[0]["rounding_uncertainty_usd_millions"] == 0.0
    assert "+/-" not in derived[0]["source_quote"]


class TestTheFactStatesItsOwnPrecision:
    def test_decimals_minus_six_is_the_nearest_million(self):
        assert Fact("us-gaap:Revenues", 200e6, decimals="-6").rounding_unit == 1e6

    def test_inf_is_exact(self):
        assert Fact("us-gaap:Revenues", 1.0, decimals="INF").rounding_unit == 0.0

    def test_saying_nothing_is_not_saying_exact(self):
        assert Fact("us-gaap:Revenues", 1.0).rounding_unit is None
        assert Fact("us-gaap:Revenues", 1.0, decimals="  ").rounding_unit is None
        assert Fact("us-gaap:Revenues", 1.0, decimals="lots").rounding_unit is None

    def test_the_reader_halves_it_and_scales_to_millions(self):
        from app.extraction.tagged import rounding_uncertainty

        assert rounding_uncertainty(Fact("us-gaap:Revenues", 200e6, decimals="-6")) == 0.5
        assert rounding_uncertainty(Fact("us-gaap:Revenues", 200e6, decimals="-3")) == 0.0005
        assert rounding_uncertainty(Fact("us-gaap:Revenues", 200e6)) is None


class TestTheBulkExtractsSpellItDifferently:
    """NUM calls the column `dcml`, not `decimals`, and writes INF as 32767.

    Reading it under its XBRL name returns nothing, which is indistinguishable
    from a filer that declared no precision - so every bulk-read fact would
    have had an unknown bound and no test of the instance path would notice.
    The column names below are the real ones, from the 2009q3 and 2025q2
    extracts: adsh, tag, version, ddate, qtrs, uom, dimh, iprx, value,
    footnote, footlen, dimn, coreg, durp, datp, dcml.
    """

    HEADER = ("adsh\ttag\tversion\tddate\tqtrs\tuom\tdimh\tiprx\tvalue\t"
              "footnote\tfootlen\tdimn\tcoreg\tdurp\tdatp\tdcml\n")

    def _root(self, tmp_path, dcml):
        (tmp_path / "sub.tsv").write_text(
            "adsh\tcik\tname\tform\tperiod\tfiled\n"
            "0000882095-22-000001\t882095\tGILEAD SCIENCES, INC.\t10-K\t20221231\t20230101\n"
        )
        (tmp_path / "dim.tsv").write_text(
            "dimhash\tsegments\n" "d1\tProductOrService=Complera;\n"
        )
        (tmp_path / "num.tsv").write_text(
            self.HEADER
            + f"0000882095-22-000001\tRevenues\tus-gaap/2022\t20221231\t4\tUSD\td1\t0\t"
              f"200000000\t\t0\t1\t\t0\t0\t{dcml}\n"
        )
        return tmp_path

    def _only_fact(self, root):
        from app.parsing.notes_datasets import (
            iter_facts,
            load_dimensions,
            load_submissions,
        )

        subs = load_submissions(root, ciks={882095}, forms={"10-K"})
        facts = list(iter_facts(root, submissions=subs,
                                dimensions=load_dimensions(root)))
        assert len(facts) == 1
        item = facts[0]
        return item[-1] if isinstance(item, tuple) else item

    def test_dcml_reaches_the_fact(self, tmp_path):
        fact = self._only_fact(self._root(tmp_path, "-6"))
        assert fact.decimals == "-6"
        assert fact.rounding_unit == 1e6

    def test_the_sentinel_means_exact(self, tmp_path):
        fact = self._only_fact(self._root(tmp_path, "32767"))
        assert fact.decimals == "INF"
        assert fact.rounding_unit == 0.0

    def test_an_empty_column_stays_unknown(self, tmp_path):
        assert self._only_fact(self._root(tmp_path, "")).rounding_unit is None
