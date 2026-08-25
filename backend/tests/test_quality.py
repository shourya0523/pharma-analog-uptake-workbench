from app.connectors.openfda_fields import earliest_approval_date, parse_openfda_date
from app.llm.client import apply_judge_hard_vetoes, re_ytd_language
from app.llm.grounding import enforce_verbatim_on_candidates, quote_is_verbatim
from app.parsing.evidence import build_revenue_llm_text, product_aliases, select_product_evidence_text
from app.quality.candidate_filters import filter_revenue_candidates
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


def test_product_aliases_split_franchise():
    aliases = product_aliases("OPSUMIT (macitentan)/OPSYNVI", "macitentan")
    assert "OPSUMIT" in aliases or any(a.upper() == "OPSUMIT" for a in aliases)
    assert any("macitentan" in a.lower() for a in aliases)


def test_select_product_evidence_prefers_money_windows():
    filler = "x" * 60000
    needle = "Total Tyvaso net product sales decreased four percent to $452.6 million for the three months ended June 30, 2026."
    text = filler + needle + " Total revenues $783.3 million were reported."
    selected, meta = select_product_evidence_text(text, product="Tyvaso", max_chars=20000)
    assert meta["had_product_money_hits"] is True
    assert "452.6" in selected
    assert "Tyvaso net product sales" in selected


def test_filter_drops_company_total_and_other_brand_and_xbrl():
    candidates = [
        {
            "period": "2026Q2",
            "value_reported": 783.3,
            "revenue_scope": "Product family",
            "source_quote": "Total revenues $783.3",
        },
        {
            "period": "2025",
            "value_reported": 526.8,
            "revenue_scope": "U.S.",
            "source_quote": "We recognized $526.8 million in Remodulin net product sales",
        },
        {
            "period": "2026Q1",
            "value_reported": 0.0,
            "revenue_scope": "Product family",
            "source_quote": "jnj:PulmonaryHypertensionMember",
        },
        {
            "period": "2026Q2",
            "value_reported": 452.6,
            "revenue_scope": "Product family",
            "source_quote": "Total Tyvaso net product sales decreased to $452.6 million",
        },
    ]
    kept, dropped = filter_revenue_candidates(candidates, product="Tyvaso")
    assert len(kept) == 1
    assert kept[0]["value_reported"] == 452.6
    reasons = {d["_drop_reason"].split(":")[0] for d in dropped}
    assert "company_total_not_product" in reasons
    assert "other_brand" in reasons
    assert "xbrl_taxonomy_noise" in reasons


def test_filter_keeps_adcirca_product_line():
    kept, dropped = filter_revenue_candidates(
        [
            {
                "period": "2025",
                "value_reported": 30.0,
                "revenue_scope": "U.S.",
                "source_quote": "We recognized $30.0 million, $23.8 million, and $28.9 million in Adcirca net product sales",
            }
        ],
        product="Adcirca",
    )
    assert len(kept) == 1
    assert not dropped


def test_openfda_earliest_approval():
    results = [
        {
            "submissions": [
                {"submission_type": "SUPPL", "submission_status": "AP", "submission_status_date": "20250402"},
                {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20130524"},
            ]
        }
    ]
    date, field = earliest_approval_date(results)
    assert date == "2013-05-24"
    assert "ORIG" in (field or "")
    assert parse_openfda_date("20130524") == "2013-05-24"


def test_build_revenue_llm_text_includes_tables():
    from app.domain.models import ParsedDocument, ParsingStatus

    doc = ParsedDocument(
        source_id="s1",
        text_blocks=["Intro text mentioning Tyvaso elsewhere without money. " * 50],
        tables=[[["Product", "2025"], ["Tyvaso DPI", "$100 million"], ["Remodulin", "$50 million"]]],
        parsing_status=ParsingStatus.SUCCESS,
    )
    doc.text_blocks.append("Tyvaso net product sales were $452.6 million in the quarter.")
    text, meta = build_revenue_llm_text(doc, product="Tyvaso")
    assert meta["had_product_money_hits"]
    assert "452.6" in text
    assert "TABLES" in text or "Tyvaso DPI" in text


def test_verbatim_quote_normalized_whitespace():
    source = "Total Tyvaso   net product sales\nto $452.6 million"
    assert quote_is_verbatim("Total Tyvaso net product sales to $452.6 million", source)
    assert not quote_is_verbatim("Made up quote about Tyvaso $999", source)


def test_enforce_verbatim_drops_paraphrase():
    spans = [{"span_id": "s1", "span_text": "Tyvaso DPI net product sales were $326.6 million"}]
    cands = [
        {"source_quote": "Tyvaso DPI net product sales were $326.6 million", "value_reported": 326.6},
        {"source_quote": "Tyvaso sold about three hundred million", "value_reported": 300},
    ]
    kept, dropped = enforce_verbatim_on_candidates(cands, source_text=spans[0]["span_text"], spans=spans)
    assert len(kept) == 1
    assert dropped[0]["_drop_reason"] == "quote_not_verbatim"


def test_judge_hard_veto_company_total():
    out = apply_judge_hard_vetoes(
        product="Tyvaso",
        candidate={"period_type": "quarterly", "revenue_scope": "Product family"},
        quote="Total revenues $783.3",
        judgment={"support_classification": "supported", "validation_status": "auto_pass", "issues": []},
    )
    assert out["support_classification"] == "misclassified"
    assert out["validation_status"] == "needs_review"


def test_judge_hard_veto_ytd_as_quarterly():
    assert re_ytd_language("for the six months ended June 30, 2026")
    out = apply_judge_hard_vetoes(
        product="Adcirca",
        candidate={"period_type": "quarterly", "revenue_scope": "U.S."},
        quote="Adcirca net product sales for the six months ended June 30, 2026 were $9.6 million",
        judgment={"support_classification": "supported", "validation_status": "auto_pass", "issues": []},
    )
    assert out["support_classification"] == "misclassified"


def test_ytd_period_type_blocks_auto_pass():
    status = apply_auto_pass_gate(
        {
            "id": "1",
            "source_url": "https://example.com",
            "source_quote": "Adcirca 9.6",
            "period_type": "ytd",
            "revenue_scope": "Product family",
            "confidence_score": 0.95,
            "validation_status": "auto_pass",
        },
        [],
    )
    assert status == "needs_review"


def test_parse_html_xbrl_no_xml_as_html_warning():
    import warnings

    from bs4 import XMLParsedAsHTMLWarning

    from app.domain.models import RetrievedSource, RetrievalStatus, SourceType
    from app.parsing.documents import DocumentParser

    markup = (
        "<?xml version='1.0' encoding='ASCII'?>"
        "<html xmlns='http://www.w3.org/1999/xhtml'>"
        "<body><p>Opsumit net sales $250 million</p>"
        "<table><tr><td>Opsumit</td><td>250</td></tr></table>"
        "</body></html>"
    )
    src = RetrievedSource(
        source_type=SourceType.SEC_FILING,
        url="https://www.sec.gov/Archives/example.htm",
        title="10-K",
        retrieval_status=RetrievalStatus.SUCCESS,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", XMLParsedAsHTMLWarning)
        doc = DocumentParser.__new__(DocumentParser)._parse_html(src, markup)
    assert not any(issubclass(w.category, XMLParsedAsHTMLWarning) for w in caught)
    assert doc.parsing_status.value == "success"
    assert any("250" in b for b in doc.text_blocks)
    assert doc.tables and doc.tables[0][0] == ["Opsumit", "250"]
