from app.db.models import EvidenceAssertionORM
from app.identity.resolver import select_assertion


def _assertion(assertion_id: str, status: str, method: str, confidence: float) -> EvidenceAssertionORM:
    return EvidenceAssertionORM(
        id=assertion_id,
        entity_type="product",
        entity_id="p1",
        field_name="moa",
        value_json={"value": assertion_id},
        source_url="https://example.test/source",
        extraction_method=method,
        validation_status=status,
        confidence=confidence,
    )


def test_confirmed_reviewer_assertion_wins_automated_backfill():
    confirmed = _assertion("reviewed", "confirmed", "reviewer", 0.8)
    deterministic = _assertion("new", "auto_pass", "structured_fda", 1.0)

    assert select_assertion([deterministic, confirmed]).id == "reviewed"


def test_deterministic_extraction_wins_unreviewed_llm_interpretation():
    deterministic = _assertion("structured", "needs_review", "structured_fda", 0.8)
    llm = _assertion("llm", "needs_review", "bounded_llm", 0.95)

    assert select_assertion([llm, deterministic]).id == "structured"

