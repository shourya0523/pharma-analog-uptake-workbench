from app.parsing.fda_label import (
    PROFILE_FIELDS,
    clean_moa_summary,
    format_moa_profile_value,
    openfda_block,
    parse_label_record,
    product_columns,
    profile_fields,
    read_path,
    route_readings_agree,
)

# The shape of a drugsFDA application: an `openfda` block built from the
# marketed NDC listings, and the application's own `products[]` array. The two
# state the route independently and can disagree.
DRUGSFDA = {
    "application_number": "NDA000001",
    "openfda": {"brand_name": ["CALDERON"], "generic_name": ["calderinol"], "route": ["ORAL"]},
    "products": [
        {
            "brand_name": "CALDERON",
            "route": "INHALATION",
            "dosage_form": "SOLUTION",
            "active_ingredients": [{"name": "CALDERINOL", "strength": "0.6MG/ML"}],
        }
    ],
}
# The shape of an SPL label record: prose sections, an `openfda` block, and no
# `products` array at all.
LABEL = {
    "openfda": {"brand_name": ["CALDERON"], "route": ["RESPIRATORY (INHALATION)"]},
    "indications_and_usage": ["CALDERON is indicated for Calderon's disease."],
    "mechanism_of_action": ["12.1 Mechanism of Action Calderinol is a vasodilator."],
}


def test_route_is_read_from_the_application_not_the_listing():
    parsed = parse_label_record(DRUGSFDA)
    assert parsed.routes == ["INHALATION"]
    assert parsed.path("route") == "products[].route"


def test_a_route_stated_twice_and_differently_is_a_conflict_not_a_preference():
    parsed = parse_label_record(DRUGSFDA)
    assert parsed.route_readings == {
        "products[].route": ["INHALATION"],
        "openfda.route": ["ORAL"],
    }
    assert parsed.route_conflict


def test_two_grains_of_one_route_are_not_a_conflict():
    assert route_readings_agree(
        {"products[].route": ["INHALATION"], "openfda.route": ["RESPIRATORY (INHALATION)"]}
    )
    assert route_readings_agree(
        {"products[].route": ["INTRAVENOUS, SUBCUTANEOUS"], "openfda.route": ["INTRAVENOUS", "SUBCUTANEOUS"]}
    )
    assert not route_readings_agree({"products[].route": ["ORAL"], "openfda.route": ["INHALATION"]})


def test_dosage_form_comes_from_the_products_array_and_a_label_record_has_none():
    assert parse_label_record(DRUGSFDA).dosage_forms == ["SOLUTION"]
    assert parse_label_record(DRUGSFDA).path("dosage_form") == "products[].dosage_form"
    assert parse_label_record(LABEL).dosage_forms == []
    assert parse_label_record(LABEL).path("dosage_form") is None


def test_a_record_with_no_openfda_block_still_answers_from_its_products():
    discontinued = {"application_number": "NDA000002", "products": [{"brand_name": "NUVESSA", "route": "INJECTION"}]}
    parsed = parse_label_record(discontinued)
    assert parsed.brand_names == ["NUVESSA"]
    assert parsed.routes == ["INJECTION"]
    assert parsed.application_numbers == ["NDA000002"]


def test_keys_are_enumerated_off_the_record():
    assert openfda_block(DRUGSFDA)["route"] == ["ORAL"]
    assert openfda_block({"openfda": None}) == {}
    assert product_columns(DRUGSFDA)["dosage_form"] == ["SOLUTION"]
    # A column drugsFDA adds is readable without this module naming it.
    assert product_columns({"products": [{"marketing_status": "Prescription"}]}) == {
        "marketing_status": ["Prescription"]
    }
    assert read_path(DRUGSFDA, "products[].active_ingredients[].name") == ["CALDERINOL"]
    assert read_path(DRUGSFDA, "products[].nothing_here") == []


def test_every_path_a_parse_cites_is_a_key_the_record_states():
    for record in (DRUGSFDA, LABEL):
        parsed = parse_label_record(record)
        for fact, path in parsed.paths.items():
            assert read_path(record, path), f"{fact} cites {path}, which the record does not state"



def test_combination_label_retains_all_ingredients_and_mechanisms():
    parsed = parse_label_record(
        {
            "openfda": {
                "substance_name": ["BICTEGRAVIR", "EMTRICITABINE", "TENOFOVIR ALAFENAMIDE"],
                "pharm_class_moa": [
                    "HIV Integrase Inhibitors [MoA]",
                    "Nucleoside Analog Reverse Transcriptase Inhibitors [MoA]",
                ],
                "application_number": ["NDA210251"],
                "route": ["ORAL"],
            },
            "dosage_and_administration": ["Tablets for oral administration."],
            "indications_and_usage": ["BIKTARVY is indicated for treatment of HIV-1 infection."],
        }
    )

    assert parsed.active_ingredients == [
        "BICTEGRAVIR",
        "EMTRICITABINE",
        "TENOFOVIR ALAFENAMIDE",
    ]
    assert len(parsed.moa_terms) == 2
    assert parsed.application_numbers == ["NDA210251"]
    assert parsed.indications_text.startswith("BIKTARVY")


def test_label_parser_preserves_all_routes_and_source_terms():
    parsed = parse_label_record({"openfda": {"route": ["INTRAVENOUS", "SUBCUTANEOUS"]}})
    assert parsed.routes == ["INTRAVENOUS", "SUBCUTANEOUS"]


def test_moa_summary_strips_section_header():
    cleaned = clean_moa_summary(
        "12.1 Mechanism of Action Treprostinil is a prostacyclin analogue. "
        "The major pharmacologic actions of treprostinil are direct vasodilation."
    )
    assert cleaned is not None
    assert not cleaned.startswith("12.1")
    assert "Mechanism of Action" not in cleaned
    assert cleaned.startswith("Treprostinil is a prostacyclin analogue")


def test_format_moa_prefers_cleaned_summary_over_class_terms():
    value = format_moa_profile_value(
        ["Prostacyclin Agonists [MoA]"],
        "12.1 Mechanism of Action Treprostinil is a prostacyclin analogue.",
    )
    assert value == "Treprostinil is a prostacyclin analogue."


def test_format_moa_falls_back_to_structured_terms():
    assert (
        format_moa_profile_value(["Endothelin Receptor Antagonists [MoA]"], None)
        == "Endothelin Receptor Antagonists [MoA]"
    )


def test_every_profile_field_names_the_path_it_was_read_from():
    record = dict(DRUGSFDA, sponsor_name="ACME THERAPEUTICS")
    fields = profile_fields(record, parse_label_record(record))
    assert fields["roa"].path == "products[].route"
    assert fields["dosage_form"].path == "products[].dosage_form"
    assert fields["brand_name"].path == "openfda.brand_name"
    assert fields["manufacturer"].path == "sponsor_name"
    for name, sourced in fields.items():
        if sourced.value is None:
            continue
        assert read_path(record, sourced.path), f"{name} cites {sourced.path}"


def test_a_field_the_record_does_not_state_keeps_its_key_and_loses_its_path():
    fields = profile_fields({}, parse_label_record({}))
    assert set(fields) == set(PROFILE_FIELDS)
    assert all(sourced.value is None and sourced.path == "" for sourced in fields.values())


def test_the_rival_route_reading_travels_with_the_field():
    fields = profile_fields(DRUGSFDA, parse_label_record(DRUGSFDA))
    assert fields["roa"].rival == {"readings": {"openfda.route": ["ORAL"]}}
    agreeing = {"openfda": {"route": ["INHALATION"]}, "products": [{"route": "INHALATION"}]}
    assert profile_fields(agreeing, parse_label_record(agreeing))["roa"].rival is None
