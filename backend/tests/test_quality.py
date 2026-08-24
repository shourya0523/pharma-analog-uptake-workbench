from app.quality.checks import apply_auto_pass_gate, quote_contains_value, run_quality_checks


def test_quote_contains_value():
    assert quote_contains_value("Net sales of Opsumit were $250 million", 250)
    assert not quote_contains_value("Net sales increased year over year", 250)


def test_missing_citation_blocks_auto_pass():
    dps = [
        {
            "id": "1",
            "period": "2020Q1",
            "value_reported": 10,
            "source_url": "",
            "source_quote": "",
            "period_type": "quarterly",
            "revenue_scope": "U.S.",
            "confidence_score": 0.9,
            "validation_status": "auto_pass",
        }
    ]
    issues = run_quality_checks(dps)
    assert any(i.issue_type == "missing_source_url" for i in issues)
    status = apply_auto_pass_gate(dps[0], issues)
    assert status == "needs_review"
