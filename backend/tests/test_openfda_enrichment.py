"""OpenFDA enrichment must describe the requested product, not its molecule siblings.

A drugsFDA search on brand OR generic name returns every application sharing the
molecule. For Tyvaso the live API returns REMODULIN first, so taking results[0] gave
a Tyvaso record REMODULIN's brand name, intravenous route and 2002 approval date.
The fixtures below are trimmed from that live response.
"""

from app.connectors.openfda import search_queries
from app.connectors.openfda_fields import (
    earliest_approval_date,
    openfda_brand_names,
    parse_openfda_date,
    select_openfda_result,
)
from app.quality.profile import is_missing_value

REMODULIN = {
    "application_number": "NDA021272",
    "sponsor_name": "UNITED THERAP",
    "openfda": {
        "brand_name": ["REMODULIN", "STERILE DILUENT FOR REMODULIN"],
        "generic_name": ["TREPROSTINIL", "WATER"],
        "route": ["INTRAVENOUS", "SUBCUTANEOUS"],
    },
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20020521"}
    ],
}
TYVASO = {
    "application_number": "NDA022387",
    "sponsor_name": "UNITED THERAP",
    "openfda": {
        "brand_name": ["TYVASO"],
        "generic_name": ["TREPROSTINIL"],
        "route": ["ORAL"],
    },
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20090730"}
    ],
}
GENERIC_ANDA = {
    "application_number": "ANDA211574",
    "sponsor_name": "ALEMBIC GLOBAL",
    "openfda": {
        "brand_name": ["TREPROSTINIL"],
        "generic_name": ["TREPROSTINIL"],
        "route": ["INTRAVENOUS", "SUBCUTANEOUS"],
    },
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20210211"}
    ],
}
ORENITRAM = {
    "application_number": "NDA203496",
    "sponsor_name": "UNITED THERAP",
    "openfda": {"brand_name": ["ORENITRAM"], "generic_name": ["TREPROSTINIL"], "route": ["ORAL"]},
    "submissions": [
        {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20131220"}
    ],
}
# Live result order for search brand_name:"Tyvaso" OR generic_name:"treprostinil"
LIVE_RESULTS = [REMODULIN, TYVASO, GENERIC_ANDA, ORENITRAM]


def test_selects_the_requested_brand_not_the_first_result():
    result, brand = select_openfda_result(LIVE_RESULTS, product="Tyvaso", generic="treprostinil")
    assert result["application_number"] == "NDA022387"
    assert brand == "TYVASO"


def test_generic_name_never_selects_a_competitor_or_anda():
    # The molecule is shared, so it must not drive selection
    result, _ = select_openfda_result(LIVE_RESULTS, product="Orenitram", generic="treprostinil")
    assert result["application_number"] == "NDA203496"

    result, brand = select_openfda_result(
        [REMODULIN, GENERIC_ANDA], product="Tyvaso", generic="treprostinil"
    )
    assert result is None and brand is None


def test_formulation_variant_matches_its_parent_brand():
    result, brand = select_openfda_result(
        LIVE_RESULTS, product="Nebulized Tyvaso", generic="treprostinil"
    )
    assert result["application_number"] == "NDA022387"
    assert brand == "TYVASO"


def test_no_match_is_reported_rather_than_guessed():
    result, brand = select_openfda_result(LIVE_RESULTS, product="Winrevair", generic="sotatercept")
    assert result is None and brand is None
    assert select_openfda_result([], product="Tyvaso") == (None, None)


def test_approval_date_is_scoped_to_the_selected_application():
    selected, _ = select_openfda_result(LIVE_RESULTS, product="Tyvaso", generic="treprostinil")
    scoped, field = earliest_approval_date([selected])
    assert scoped == "2009-07-30"
    assert "submissions" in field
    # Across every result the earliest date is Remodulin's, which is the old bug
    unscoped, _ = earliest_approval_date(LIVE_RESULTS)
    assert unscoped == "2002-05-21"
    assert scoped != unscoped


def test_brand_names_are_listed_for_diagnostics():
    assert openfda_brand_names(TYVASO) == ["TYVASO"]
    assert openfda_brand_names({}) == []


def test_parse_openfda_date_handles_compact_and_iso():
    assert parse_openfda_date("20090730") == "2009-07-30"
    assert parse_openfda_date("2009-07-30") == "2009-07-30"
    assert parse_openfda_date("") is None
    assert parse_openfda_date("garbage") is None


def test_brand_is_queried_before_the_molecule():
    """A combined brand-OR-generic search can exclude the product entirely."""
    scopes = [scope for scope, _ in search_queries("Opsumit", "macitentan")]
    assert scopes == ["brand", "generic"]

    brand_query = search_queries("Opsumit", "macitentan")[0][1]
    assert brand_query == 'openfda.brand_name:"Opsumit"'
    assert "generic_name" not in brand_query, "the molecule must not widen the brand query"


def test_search_queries_tolerate_missing_inputs():
    assert search_queries("Tyvaso") == [("brand", 'openfda.brand_name:"Tyvaso"')]
    assert search_queries("", "treprostinil") == [("generic", 'openfda.generic_name:"treprostinil"')]
    assert search_queries("", None) == []


def test_missing_value_placeholders_are_recognised():
    for placeholder in ("Not specified", "not specified.", "N/A", "unknown", "None", "", "  "):
        assert is_missing_value(placeholder), placeholder
    assert is_missing_value(None)
    for real in ("Inhalation", "2009-07-30", "Prostacyclin Vasodilator [EPC]", "0"):
        assert not is_missing_value(real), real


# ---------------------------------------------------------------------------
# Which application a product's attributes are read from. Both cases below are
# defects the profile holdout surfaced: one product's attributes taken from a
# sibling formulation's application, and its first approval taken from a later
# one of its own.
# ---------------------------------------------------------------------------

def _application(number, brand, approved):
    return {
        "application_number": number,
        "openfda": {"brand_name": [brand]},
        "submissions": [
            {"submission_type": "ORIG", "submission_status": "AP",
             "submission_status_date": approved}
        ],
    }


def test_a_sibling_brand_in_the_alias_list_does_not_outrank_the_product():
    """The job asks about Calderon; the alias list holds Calderon XR.

    Both are exact brand matches against the candidate list, so whichever the
    search returned first used to win - and with it the approval date, the route
    and the label sections of a formulation the job was not asking about.
    """
    from app.connectors.openfda_fields import select_openfda_result

    results = [_application("NDA200002", "CALDERON XR", "20200101"),
               _application("NDA200001", "CALDERON", "20100101")]
    selected, brand = select_openfda_result(
        results, product="Calderon", aliases=["Calderon XR"]
    )
    assert selected["application_number"] == "NDA200001"
    assert brand == "CALDERON"

    # Asked about the sibling itself, the sibling is the right answer.
    selected, _ = select_openfda_result(results, product="Calderon XR")
    assert selected["application_number"] == "NDA200002"


def test_first_approval_comes_from_the_earliest_of_a_products_applications():
    """A capsule and a tablet of one brand are two applications, years apart.

    The product's first approval is the earlier of them. Read from whichever
    application the search returned first, a product reformulated later reports
    the reformulation's date as its first approval.
    """
    from app.connectors.openfda_fields import (
        earliest_approval_date,
        select_openfda_applications,
    )

    results = [_application("NDA300002", "CALDERON", "20220805"),
               _application("NDA300001", "CALDERON", "20171031"),
               _application("NDA300003", "NUVESSA", "20010101")]
    applications = select_openfda_applications(results, product="Calderon")

    assert {a["application_number"] for a in applications} == {"NDA300001", "NDA300002"}
    # NuVessa shares neither the brand nor the product, so its older date is not
    # this product's first approval.
    assert earliest_approval_date(applications)[0] == "2017-10-31"
