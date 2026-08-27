from app.identity.resolver import resolve_product_identity


def test_related_tyvaso_formulations_share_family_but_not_identity():
    solution = resolve_product_identity(
        brand_name="Tyvaso",
        active_ingredients=["treprostinil"],
        dosage_form="inhalation solution",
        route_terms=["RESPIRATORY (INHALATION)"],
    )
    dpi = resolve_product_identity(
        brand_name="Tyvaso DPI",
        active_ingredients=["treprostinil"],
        dosage_form="powder",
        route_terms=["RESPIRATORY (INHALATION)"],
        delivery_device="dry powder inhaler",
    )

    assert solution.analog_family_key == dpi.analog_family_key == "treprostinil"
    assert solution.identity_key != dpi.identity_key


def test_identity_is_stable_across_case_and_whitespace():
    one = resolve_product_identity(
        brand_name="  Example  ",
        active_ingredients=["Ingredient A"],
        dosage_form="Tablet",
        route_terms=["ORAL"],
    )
    two = resolve_product_identity(
        brand_name="example",
        active_ingredients=["ingredient a"],
        dosage_form="tablet",
        route_terms=["oral"],
    )
    assert one.identity_key == two.identity_key

