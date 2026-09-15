"""The analog attributes are derived from evidence, not looked up.

`seed/product_attributes.csv` carries curated analog attributes for the
products this repo already tracks, and the answer key's profiles are those same
rows. A derivation that consulted them would be measured against its own input,
so the test that matters here is not that the attributes are right - it is that
a product the curated file describes gets nothing from it.

Names are invented throughout. A test spelling a real brand teaches the next
reader which products the answer key holds, and they start reasoning about
those instead of about the rule.
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.analytics.profile_attributes import (
    DISTINCT_PRODUCT,
    PROVENANCE,
    competitive_intensity,
    derive_analog_profile,
    era_for_year,
    peer_universe_role,
    peers_marketed_at_launch,
    read_classification,
    route_of_administration,
    year_of,
)

REPO = Path(__file__).resolve().parents[2]


def profile(name, *, area="Vessel disease", moa_class="vessel_peptide_pathway",
            route="Oral", year=2010, role=DISTINCT_PRODUCT):
    return {
        "drug_name": name,
        "moa_class": moa_class,
        "route_of_administration": route,
        "first_approval_year": year,
        "indication_area": area,
        "peer_universe_role": role,
    }


def test_an_era_is_computed_rather_than_looked_up():
    """A year in a bucket no product has reached still gets one."""
    assert era_for_year(2010) == era_for_year(2014) == "2010-2014"
    assert era_for_year(2015) == "2015-2019"
    assert era_for_year(1989) == "1985-1989"
    assert era_for_year(2043) == "2040-2044"


def test_a_year_is_read_out_of_whatever_the_source_spelled():
    assert year_of("2009-05-22") == 2009
    assert year_of("approved May 2009") == 2009
    assert year_of("") is None
    assert year_of(None) is None


def test_one_route_resolves_and_several_do_not():
    """A product approved by three routes has not stated one route, and an
    attribute compared for equality cannot be a list."""
    assert route_of_administration(["ORAL"]) == "Oral"
    assert route_of_administration(["RESPIRATORY (INHALATION)"]) == "Respiratory (Inhalation)"
    assert route_of_administration(["ORAL", "ORAL"]) == "Oral"
    assert route_of_administration(["ORAL", "INTRAVENOUS"]) is None
    assert route_of_administration([]) is None


def test_a_formulation_is_recorded_as_its_parents():
    """Nebulized Calderon is Calderon's approval a second time."""
    catalog = ["Calderon", "Calderon XR", "NuVessa"]
    assert peer_universe_role("Nebulized Calderon", catalog) == "formulation_of:Calderon"
    assert peer_universe_role("Calderon", catalog) == DISTINCT_PRODUCT
    assert peer_universe_role("NuVessa", catalog) == DISTINCT_PRODUCT
    # The longest parent is the more specific reading.
    assert peer_universe_role("Calderon XR Depot", catalog) == "formulation_of:Calderon XR"


def test_a_formulation_is_not_a_second_competitor():
    catalog = [
        profile("Calderon", year=2004),
        profile("Nebulized Calderon", year=2008, role="formulation_of:Calderon"),
        profile("NuVessa", year=2006),
    ]
    peers = peers_marketed_at_launch(profile("Latecomer", year=2012), catalog)
    assert sorted(row["drug_name"] for row in peers) == ["Calderon", "NuVessa"]


def test_a_launch_into_an_unknown_indication_is_refused_not_called_open():
    """An empty roster because we know nothing is not an open market."""
    label, count = competitive_intensity(
        {"drug_name": "Calderon", "first_approval_year": 2004, "indication_area": None}, []
    )
    assert label is None and count is None


def test_a_crowded_launch_outranks_an_early_one_in_the_same_cohort():
    catalog = [profile(f"Peer{index}", year=1996 + index) for index in range(8)]
    first = competitive_intensity(profile("Firstcomer", year=1990), catalog)
    latest = competitive_intensity(profile("Latecomer", year=2012), catalog)
    assert first[1] == 0
    assert latest[1] == len(catalog)
    assert ("low", "high") == (first[0], latest[0])


def test_what_the_evidence_did_not_support_is_named():
    derived = derive_analog_profile(drug_name="Calderon", fields={})
    assert derived.attribute_provenance == PROVENANCE
    assert derived.peer_universe_role == DISTINCT_PRODUCT
    assert set(derived.unresolved) >= {
        "moa", "moa_class", "route_of_administration", "first_approval_year",
        "approval_era", "indication_area", "competitive_intensity_at_launch",
    }


def test_the_attributes_come_from_the_fields_that_were_extracted():
    derived = derive_analog_profile(
        drug_name="Calderon",
        fields={
            "moa": "Selective vessel peptide receptor antagonist",
            "moa_class": "vessel_peptide_pathway",
            "indication_area": "Vessel disease",
            "fda_approval_date": "2004-11-02",
        },
        route_terms=["ORAL"],
        catalogue=[profile("NuVessa", year=1999), profile("Othermol", year=2001)],
    )
    assert derived.first_approval_year == 2004
    assert derived.approval_era == "2000-2004"
    assert derived.route_of_administration == "Oral"
    assert derived.marketed_peers_at_launch == 2
    assert derived.competitive_intensity_basis
    assert not derived.unresolved


def test_a_classification_that_breaks_its_own_rule_is_refused():
    """Each check is the prompt's own rule, so a model that broke one has not
    produced the attribute that was asked for."""
    assert read_classification(
        {"moa_class": "vessel_peptide_pathway", "indication_area": "Vessel disease"},
        product="Calderon",
    ) == ("vessel_peptide_pathway", "Vessel disease")
    # Names this product rather than a group it belongs to.
    assert read_classification({"moa_class": "calderon_pathway"}, product="Calderon")[0] is None
    # Not a group key.
    assert read_classification({"moa_class": "Vessel Peptide Pathway"}, product="Calderon")[0] is None
    # The Established Pharmacologic Class term relabelled.
    assert read_classification(
        {"moa_class": "vessel_peptide_antagonist"},
        product="Calderon",
        epc="Vessel Peptide Antagonist",
    )[0] is None
    # Either key may be refused on its own.
    assert read_classification(
        {"moa_class": "inconclusive", "indication_area": "Vessel disease"}, product="Calderon"
    ) == (None, "Vessel disease")


def test_a_curated_product_gets_nothing_from_the_curated_file():
    """The property rule 3 is about, checked on behaviour rather than on imports.

    Every product in `seed/product_attributes.csv` has curated values for these
    attributes, and the answer key's profiles are those same rows. Asked about
    one of them with no extracted evidence, the derivation must still answer
    nothing - otherwise it would score against a file it read.
    """
    curated = list(csv.DictReader((REPO / "seed" / "product_attributes.csv").open(newline="")))
    assert curated, "no curated rows to check against; this test would pass vacuously"
    for row in curated:
        derived = derive_analog_profile(drug_name=row["drug_name"], fields={})
        assert derived.moa is None
        assert derived.moa_class is None
        assert derived.route_of_administration is None
        assert derived.first_approval_year is None
        assert derived.indication_area is None
