from app.parsing.indications import parse_indications


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

