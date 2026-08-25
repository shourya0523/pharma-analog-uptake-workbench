from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.migrations import upgrade_database
from app.db.models import (
    AnalogFamilyORM,
    CanonicalProductORM,
    ProductFormulationORM,
    ProductIndicationORM,
)
from app.domain.models import (
    CompetitiveIntensity,
    LineOfTherapy,
    PeakEstimateType,
    PharmaAssertion,
)


def test_normalized_product_supports_multiple_formulations_and_indications():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        family = AnalogFamilyORM(id="family", name="Treprostinil")
        product = CanonicalProductORM(id="product", canonical_name="Tyvaso", identity_key="tyvaso")
        db.add_all([family, product])
        db.flush()
        db.add_all(
            [
                ProductFormulationORM(
                    id="f1", product_id=product.id, analog_family_id=family.id,
                    dosage_form="inhalation solution", route_category="inhaled",
                ),
                ProductFormulationORM(
                    id="f2", product_id=product.id, analog_family_id=family.id,
                    dosage_form="dry powder inhaler", route_category="inhaled",
                ),
                ProductIndicationORM(
                    id="i1", product_id=product.id, disease="PAH", approved_lot="all_lines_or_unspecified",
                ),
                ProductIndicationORM(
                    id="i2", product_id=product.id, disease="PH-ILD", approved_lot="all_lines_or_unspecified",
                ),
            ]
        )
        db.commit()
        assert db.query(ProductFormulationORM).count() == 2
        assert db.query(ProductIndicationORM).count() == 2


def test_canonical_identity_key_is_unique():
    engine = create_engine("sqlite://")
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(CanonicalProductORM(id="one", canonical_name="Tyvaso", identity_key="tyvaso"))
        db.commit()
        db.add(CanonicalProductORM(id="two", canonical_name="TYVASO", identity_key="tyvaso"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_domain_enums_and_assertion_provenance_are_typed():
    assertion = PharmaAssertion(
        field_name="approved_lot",
        value=LineOfTherapy.SECOND_LINE_PLUS,
        source_url="https://dailymed.nlm.nih.gov/example",
        source_section="INDICATIONS AND USAGE",
        source_quote="after one prior regimen",
        extraction_method="deterministic_label_rule",
    )
    assert assertion.value == LineOfTherapy.SECOND_LINE_PLUS
    assert PeakEstimateType.CONSENSUS.value == "consensus"
    assert CompetitiveIntensity.HIGH.value == "high"

    with pytest.raises(ValidationError):
        PharmaAssertion(
            field_name="approved_lot",
            value="2L+",
            source_url="",
            extraction_method="deterministic_label_rule",
        )

