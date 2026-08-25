from app.parsing.fda_label import parse_label_record
from app.quality.checks import moa_epc_contamination_issue


def test_epc_and_moa_are_parsed_separately():
    parsed = parse_label_record(
        {
            "openfda": {
                "brand_name": ["Example"],
                "pharm_class_epc": ["Kinase Inhibitor [EPC]"],
                "pharm_class_moa": ["Kinase Inhibitors [MoA]"],
                "route": ["ORAL"],
            },
            "mechanism_of_action": ["Example inhibits kinase signaling."],
        }
    )

    assert parsed.epc_terms == ["Kinase Inhibitor [EPC]"]
    assert parsed.moa_terms == ["Kinase Inhibitors [MoA]"]
    assert parsed.moa_summary == "Example inhibits kinase signaling."


def test_epc_only_label_leaves_moa_unresolved_and_flags_copy():
    parsed = parse_label_record({"openfda": {"pharm_class_epc": ["Endothelin Receptor Antagonist [EPC]"]}})

    assert parsed.moa_terms == []
    assert parsed.moa_summary is None
    issue = moa_epc_contamination_issue(
        moa="Endothelin Receptor Antagonist [EPC]",
        epc_terms=parsed.epc_terms,
    )
    assert issue is not None
    assert issue.severity == "high"

