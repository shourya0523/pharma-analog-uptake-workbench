"""Repeating failure fixes: rollup, partner cash, local peers, harvest, formulation.

Invented names only: Calderon, NuVessa. No gold brands.
"""

from __future__ import annotations

from app.extraction.candidates import extract_revenue_candidates
from app.extraction.extract import read_table
from app.llm.harvest import harvest_amount_loci, harvest_table_loci
from app.parsing.evidence import NON_PRODUCT_REVENUE_RE
from app.parsing.labels import read_label
from app.quality.candidate_filters import filter_revenue_candidates, schedule_local_peer_names

HEADER = [
    ["($ in millions)", "Three Months Ended June 30,", ""],
    ["", "2024", "2023"],
]


def test_royalty_is_residue_not_a_qualifier():
    reading = read_label("Calderon royalties", ["Calderon"])
    assert reading.residue == "royalties"
    assert not reading.whole
    assert "label_not_understood" in reading.flags


def test_product_sales_preferred_over_product_plus_royalty_total():
    rows = HEADER + [
        ["Calderon product sales", "54", "50"],
        ["Calderon royalties", "102", "90"],
        ["Total Calderon", "156", "140"],
    ]
    readout = read_table(rows, product="Calderon", context="(in millions)")
    values = {v.period: v.value_as_reported for v in readout.values
              if "label_not_understood" not in v.flags}
    assert values.get("2024Q2") == 54.0 or any(
        v.value_as_reported == 54.0 for v in readout.values
        if "label_not_understood" not in v.flags
    ), readout.values


def test_geo_total_still_preferred_among_regions():
    rows = HEADER + [
        ["Calderon - U.S.", "40", "35"],
        ["Calderon - International", "14", "15"],
        ["Total Calderon", "54", "50"],
    ]
    readout = read_table(rows, product="Calderon", context="(in millions)")
    published = [v for v in readout.values if "label_not_understood" not in v.flags]
    assert any(v.value_as_reported == 54.0 for v in published), published


def test_lone_total_calderon_sales_still_publishes():
    rows = HEADER + [["Total Calderon sales", "54", "50"]]
    readout = read_table(rows, product="Calderon", context="(in millions)")
    assert any(v.value_as_reported == 54.0 for v in readout.values)


def test_partner_cash_vocab_matches():
    text = "we received $235.0 million in aggregate from Bayer related to Calderon"
    assert NON_PRODUCT_REVENUE_RE.search(text)


def test_partner_cash_dropped_before_store():
    prose = (
        "For the three months ended June 30, 2024, we received $235.0 million "
        "in aggregate from Bayer related to Calderon collaboration."
    )
    cands, _findings, _skipped = extract_revenue_candidates(
        [], product="Calderon", prose=prose, context="(in millions)"
    )
    assert not any(abs(float(c["value_reported"]) - 235.0) < 0.01 for c in cands), cands


def test_ordinary_product_revenue_prose_kept():
    prose = (
        "For the three months ended June 30, 2024, Calderon product revenue "
        "was $54.0 million."
    )
    cands, _findings, _skipped = extract_revenue_candidates(
        [], product="Calderon", prose=prose, context="(in millions)"
    )
    assert any(abs(float(c["value_reported"]) - 54.0) < 0.01 for c in cands), cands


def test_partner_cash_filter_drop_reason():
    cand = {
        "period": "2024Q2",
        "period_type": "quarterly",
        "value_reported": 235.0,
        "revenue_scope": "Product family",
        "source_quote": "received $235.0 million in aggregate from Bayer for Calderon",
    }
    kept, dropped = filter_revenue_candidates([cand], product="Calderon", peer_names=[])
    assert not kept
    assert dropped[0]["_drop_reason"] == "non_product_revenue"


def _period_schedule(rows):
    return [
        ["", "Three Months Ended June 30,"],
        ["", "2024", "2023"],
        *rows,
    ]


def test_bare_net_product_kept_when_peer_on_other_schedule():
    product_sales = _period_schedule(
        [
            ["Net product revenue", "222.4", "100.0"],
            ["Cost of sales", "40.0", "20.0"],
        ]
    )
    other = _period_schedule(
        [
            ["NuVessa", "10.0", "8.0"],
            ["Other", "1.0", "1.0"],
        ]
    )
    cand = {
        "period": "2024Q2",
        "period_type": "quarterly",
        "value_reported": 222.4,
        "revenue_scope": "Product family",
        "source_quote": "Net product revenue | 222.4 | 100.0",
    }
    local = schedule_local_peer_names(
        [product_sales, other], product="Calderon", quote=cand["source_quote"]
    )
    assert local == []
    kept, dropped = filter_revenue_candidates(
        [cand], product="Calderon", peer_names=["nuvessa"], tables=[product_sales, other]
    )
    assert len(kept) == 1, dropped


def test_bare_net_product_dropped_when_peer_on_same_schedule():
    shared = _period_schedule(
        [
            ["Calderon", "54.0", "50.0"],
            ["NuVessa", "10.0", "8.0"],
            ["Net product revenue", "64.0", "58.0"],
        ]
    )
    cand = {
        "period": "2024Q2",
        "period_type": "quarterly",
        "value_reported": 64.0,
        "revenue_scope": "Product family",
        "source_quote": "Net product revenue | 64.0 | 58.0",
    }
    kept, dropped = filter_revenue_candidates(
        [cand], product="Calderon", peer_names=["nuvessa"], tables=[shared]
    )
    assert not kept
    assert dropped[0]["_drop_reason"] == "product_scope_without_product_in_quote"


def test_harvest_skips_collaboration_paragraph_outside_product_schedule():
    product_sales = _period_schedule(
        [
            ["Calderon product sales", "54", "50"],
            ["Total product sales", "54", "50"],
        ]
    )
    text = (
        "Calderon product sales were $54 million for the quarter. "
        "Separately, collaboration income of $235 million was recognized "
        "under the Bayer agreement unrelated to product units sold."
    )
    table_loci = harvest_table_loci([product_sales], product="Calderon")
    prose_loci = harvest_amount_loci(text, product="Calderon", tables=[product_sales])
    amounts = {loc["amount"] for loc in table_loci + prose_loci}
    assert any("54" in a for a in amounts), amounts
    assert not any("235" in a for a in amounts), amounts


def test_formulation_stamp_not_aggregate():
    reading = read_label("Calderon foam", ["Calderon"])
    assert reading.formulation == "foam"
    assert reading.residue == ""
    assert reading.whole


def test_brand_total_preferred_over_formulation_slice():
    rows = HEADER + [
        ["Calderon cream", "42.3", "30.0"],
        ["Calderon foam", "39.2", "20.0"],
        ["Total Calderon", "81.5", "50.0"],
    ]
    readout = read_table(rows, product="Calderon", context="(in millions)")
    published = [
        v for v in readout.values
        if "label_not_understood" not in v.flags and not v.formulation
    ]
    assert any(v.value_as_reported == 81.5 for v in published), readout.values


def test_cream_alone_publishes_as_formulation():
    rows = HEADER + [["Calderon cream", "42.3", "30.0"]]
    readout = read_table(rows, product="Calderon", context="(in millions)")
    assert any(v.value_as_reported == 42.3 and v.formulation == "cream" for v in readout.values)


def test_formulation_alias_does_not_erase_formulation_stamp():
    """``Calderon foam`` as a match alias made the foam slice look like the brand."""
    from app.llm.aliases import merge_aliases
    from app.parsing.labels import read_label

    match = merge_aliases(
        "Calderon",
        formulations=["Calderon foam", "Calderon cream"],
    )
    reading = read_label("Calderon foam", match)
    assert reading.matched == "Calderon"
    assert reading.formulation == "foam"


def test_parent_alias_does_not_claim_corporate_loss_as_product():
    from app.llm.aliases import merge_aliases
    from app.quality.candidate_filters import quote_mentions_product

    match = merge_aliases(
        "Calderon",
        parent_companies=["Acme Pharma, Inc.", "Acme"],
    )
    quote = "Comprehensive loss attributable to common stockholders of Acme"
    assert not quote_mentions_product(quote, "Calderon", extra_aliases=match)


def test_product_sales_and_total_revenue_do_not_conflict():
    """Two tables, one quarter: the line keeps, the Total rollup does not veto it."""
    from app.extraction.candidates import extract_revenue_candidates

    product_sales = HEADER + [["Calderon product sales", "54", "50"]]
    total_rev = [
        ["($ in millions)", "Three Months Ended June 30,", ""],
        ["", "2024", "2023"],
        ["Total Calderon revenue", "156", "140"],
    ]
    cands, findings, _ = extract_revenue_candidates(
        [product_sales, total_rev], product="Calderon", context="(in millions)",
    )
    assert not any(f.code == "conflicting_values" for f in findings), findings
    q2 = [c for c in cands if c["period"] == "2024Q2"]
    assert any(abs(float(c["value_reported"]) - 54.0) < 0.01 for c in q2), q2
    assert not any(abs(float(c["value_reported"]) - 156.0) < 0.01 for c in q2), q2
