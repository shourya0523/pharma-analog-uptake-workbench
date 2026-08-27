from app.connectors.llm_search import _normalize_results
from app.llm.aliases import merge_aliases
from app.llm.client import _citations_from_message, _parse_json_content
from app.quality.candidate_filters import filter_revenue_candidates


def test_merge_aliases_includes_llm_names():
    merged = merge_aliases(
        "Opsumit",
        "macitentan",
        llm_aliases=["OPSYNVI"],
        formulations=["Opsumit DPI"],
        parent_companies=["Johnson & Johnson"],
    )
    assert any("OPSYNVI" in a for a in merged)
    assert any("macitentan" in a.lower() for a in merged)


def test_filter_uses_extra_aliases():
    kept, dropped = filter_revenue_candidates(
        [
            {
                "period": "2026Q1",
                "value_reported": 120.0,
                "revenue_scope": "Product family",
                "source_quote": "OPSYNVI net product sales were $120 million",
            }
        ],
        product="Opsumit",
        generic="macitentan",
        extra_aliases=["OPSYNVI"],
    )
    assert len(kept) == 1
    assert not dropped


def test_normalize_openrouter_search_results():
    results = _normalize_results(
        {
            "results": [
                {"url": "https://www.sec.gov/a", "title": "10-K", "snippet": "Opsumit sales"},
                {"url": "https://www.sec.gov/a", "title": "dup"},
            ],
            "_citations": [{"url": "https://investor.jnj.com/x", "title": "IR", "snippet": "macitentan"}],
        }
    )
    assert len(results) == 1
    assert results[0]["url"].endswith("/a")


def test_parse_json_content_fenced():
    assert _parse_json_content('```json\n{"a": 1}\n```')["a"] == 1


def test_citations_from_message():
    cites = _citations_from_message(
        {
            "annotations": [
                {"type": "url_citation", "url_citation": {"url": "https://sec.gov/x", "title": "SEC", "content": "CIK"}}
            ]
        }
    )
    assert cites[0]["url"] == "https://sec.gov/x"
