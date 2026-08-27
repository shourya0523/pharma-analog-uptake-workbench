from app.parsing.fda_label import (
    clean_moa_summary,
    format_moa_profile_value,
    parse_label_record,
)


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
