from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import (
    AnalogFamilyORM,
    CanonicalProductORM,
    DrugJobORM,
    DrugProfileFieldORM,
    EvidenceAssertionORM,
    MoAComponentORM,
    ProductFormulationORM,
)
from app.domain.models import new_id
from app.identity.resolver import resolve_product_identity
from app.quality.checks import moa_epc_contamination_issue


@dataclass(frozen=True)
class BackfillResult:
    job_id: str
    product_id: str
    created_product: bool


def _selected_fields(rows: list[DrugProfileFieldORM]) -> dict[str, DrugProfileFieldORM]:
    rank = {"confirmed": 0, "auto_pass": 1, "needs_review": 2, "pending": 3}
    selected: dict[str, DrugProfileFieldORM] = {}
    for row in sorted(rows, key=lambda item: (rank.get(item.validation_status, 4), item.id)):
        selected.setdefault(row.field, row)
    return selected


def backfill_job(db: Session, job_id: str) -> BackfillResult:
    job = db.get(DrugJobORM, job_id)
    if not job:
        raise ValueError("job not found")
    fields = _selected_fields(db.query(DrugProfileFieldORM).filter_by(job_id=job.id).all())
    generic = fields.get("generic_name").value if fields.get("generic_name") else job.generic_name
    ingredients = [item.strip() for item in (generic or "").split(";") if item.strip()]
    dosage_form = fields.get("dosage_form").value if fields.get("dosage_form") else None
    roa = fields.get("roa").value if fields.get("roa") else None
    identity = resolve_product_identity(
        brand_name=job.drug_name,
        active_ingredients=ingredients,
        dosage_form=dosage_form,
        route_terms=(roa or "").split(";"),
    )
    product = db.query(CanonicalProductORM).filter_by(identity_key=identity.identity_key).first()
    created = product is None
    if not product:
        product = CanonicalProductORM(
            id=new_id(),
            canonical_name=identity.canonical_name,
            identity_key=identity.identity_key,
            active_moieties_json=identity.active_ingredients,
            current_commercial_owner=job.manufacturer,
            manufacturer=job.manufacturer,
        )
        db.add(product)
        db.flush()

    family = db.query(AnalogFamilyORM).filter_by(active_moiety_key=identity.analog_family_key).first()
    if not family:
        family = AnalogFamilyORM(
            id=new_id(),
            name=identity.analog_family_key.title(),
            active_moiety_key=identity.analog_family_key,
        )
        db.add(family)
        db.flush()
    if dosage_form and not db.query(ProductFormulationORM).filter_by(product_id=product.id).first():
        db.add(
            ProductFormulationORM(
                id=new_id(),
                product_id=product.id,
                analog_family_id=family.id,
                dosage_form=dosage_form,
                route_source_term=roa,
                route_category=(roa or "").lower() or None,
            )
        )

    epc_terms = [fields["pharmacologic_class"].value] if fields.get("pharmacologic_class") else []
    moa_field = fields.get("moa")
    if (
        moa_field
        and moa_field.value
        and not moa_epc_contamination_issue(moa_field.value, epc_terms)
        and not db.query(MoAComponentORM)
        .filter_by(product_id=product.id, moa_term=moa_field.value)
        .first()
    ):
        db.add(
            MoAComponentORM(
                id=new_id(),
                product_id=product.id,
                moa_term=moa_field.value,
                fda_epc_terms_json=epc_terms,
            )
        )

    for field_name, row in fields.items():
        citation = row.citation_json or {}
        existing = (
            db.query(EvidenceAssertionORM)
            .filter_by(
                entity_type="product",
                entity_id=product.id,
                field_name=field_name,
                source_id=citation.get("source_id"),
            )
            .first()
        )
        if not existing and citation.get("source_url"):
            db.add(
                EvidenceAssertionORM(
                    id=new_id(),
                    entity_type="product",
                    entity_id=product.id,
                    field_name=field_name,
                    value_json={"value": row.value},
                    source_id=citation.get("source_id"),
                    source_url=citation["source_url"],
                    source_section=citation.get("source_quote"),
                    source_quote=citation.get("source_quote"),
                    confidence=float(citation.get("confidence") or 0),
                    validation_status=row.validation_status,
                    extraction_method=citation.get("extraction_method") or "legacy_backfill",
                    selected=True,
                )
            )
    db.commit()
    return BackfillResult(job.id, product.id, created)

