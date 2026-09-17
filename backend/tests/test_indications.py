from app.parsing.indications import (
    parse_indications,
    therapeutic_area,
    therapeutic_areas,
)


def test_multiple_indications_remain_separate_with_distinct_lot():
    records = parse_indications(
        "Indicated as first-line therapy for metastatic disease A.\n\n"
        "Indicated for disease B after at least two prior lines of therapy."
    )

    assert len(records) == 2
    assert records[0].approved_lot.value == "1L"
    assert records[1].approved_lot.value == "3L+"
    assert records[0].disease != records[1].disease


def test_indication_record_preserves_setting_population_and_biomarker():
    records = parse_indications(
        "For adults with metastatic EGFR-positive non-small cell lung cancer after one prior regimen."
    )
    record = records[0]
    assert record.setting == "metastatic"
    assert record.population == "adults"
    assert record.biomarker == "EGFR-positive"


TYVASO_LABEL = (
    "1 INDICATIONS AND USAGE Tyvaso is a prostacyclin mimetic indicated for the treatment of: "
    "Pulmonary arterial hypertension (PAH; WHO Group 1) to improve exercise ability. "
    "Studies establishing effectiveness predominately included patients with NYHA Functional Class III. "
    "1.1 Pulmonary Arterial Hypertension Tyvaso is indicated for the treatment of "
    "pulmonary arterial hypertension (PAH; WHO Group 1) to improve exercise ability. "
    "Studies establishing effectiveness predominately included patients with NYHA Functional Class III. "
    "1.2 Pulmonary Hypertension Associated with ILD Tyvaso is indicated for the treatment of "
    "pulmonary hypertension associated with interstitial lung disease (PH-ILD; WHO Group 3) "
    "to improve exercise ability. The study establishing effectiveness predominately included "
    "patients with idiopathic interstitial pneumonia."
)


def test_tyvaso_style_label_splits_pah_and_ph_ild():
    records = parse_indications(TYVASO_LABEL)
    diseases = [r.disease.casefold() for r in records]
    assert len(records) >= 2
    assert any("pulmonary arterial hypertension" in d or "pah" in d for d in diseases)
    assert any("interstitial lung disease" in d or "ph-ild" in d for d in diseases)
    assert all(not d.startswith("1 indications") for d in diseases)
    assert all("studies establishing" not in d for d in diseases)


def test_winrevair_style_single_indication_is_clean():
    records = parse_indications(
        "1 INDICATIONS AND USAGE WINREVAIR is indicated for the treatment of adults with "
        "pulmonary arterial hypertension (PAH, Group 1 pulmonary hypertension) to improve "
        "exercise capacity and World Health Organization (WHO) functional class (FC)."
    )
    assert len(records) == 1
    assert "pulmonary arterial hypertension" in records[0].disease.casefold()
    assert "1 indications" not in records[0].disease.casefold()
    assert records[0].population == "adults"


def test_one_disease_written_several_ways_is_one_area():
    spellings = [
        "Calderon's disease (CD) (WHO Group 1)",
        "Calderon's disease (CD; WHO Group 1)",
        "Calderon's disease (CD, WHO Group I)",
        "Calderon's disease (CD) (World Health Organization [WHO] Group I)",
        "Calderon's disease",
        "Calderon's disease - WHO Group 1",
    ]
    assert {therapeutic_area(spelling) for spelling in spellings} == {"calderon's disease"}


def test_a_qualifier_that_changes_the_market_keeps_its_own_area():
    assert therapeutic_area("Calderon's disease associated with nebulisation (WHO Group 3)") != (
        therapeutic_area("Calderon's disease (WHO Group 1)")
    )
    assert therapeutic_area("NuVessa syndrome") == "nuvessa syndrome"


def test_areas_are_listed_once_each_in_label_order():
    parsed = parse_indications(
        "Indicated for the treatment of Calderon's disease (CD) (WHO Group 1).\n\n"
        "Indicated for the treatment of Calderon's disease (CD; WHO Group 1).\n\n"
        "Indicated for the treatment of NuVessa syndrome."
    )
    assert therapeutic_areas(parsed) == ["calderon's disease", "nuvessa syndrome"]
    assert therapeutic_areas([]) == []
