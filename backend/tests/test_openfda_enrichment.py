"""OpenFDA enrichment must describe the requested product, not its molecule siblings.

A drugsFDA search on brand OR generic name returns every application sharing the
molecule. For Tyvaso the live API returns REMODULIN first, so taking results[0] gave
a Tyvaso record REMODULIN's brand name, intravenous route and 2002 approval date.
The fixtures below are trimmed from that live response.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.connectors import openfda as openfda_module
from app.connectors.openfda import BRAND_SEARCH_PATHS, OpenFDAConnector, search_queries


async def _noop(*_args, **_kwargs):
    return None

from app.connectors.openfda_fields import (
    brand_matched_results,
    earliest_approval_date,
    earliest_approved_match,
    molecule_names,
    names_the_molecule,
    openfda_brand_names,
    parse_openfda_date,
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
    matches = brand_matched_results(LIVE_RESULTS, product="Tyvaso", generic="treprostinil")
    assert [(r["application_number"], b) for r, b in matches] == [("NDA022387", "TYVASO")]


def test_generic_name_never_selects_a_competitor_or_anda():
    # The molecule is shared, so it must not drive selection
    matches = brand_matched_results(LIVE_RESULTS, product="Orenitram", generic="treprostinil")
    assert [r["application_number"] for r, _ in matches] == ["NDA203496"]

    assert (
        brand_matched_results([REMODULIN, GENERIC_ANDA], product="Tyvaso", generic="treprostinil")
        == []
    )


def test_a_more_specific_product_does_not_take_the_general_one_s_application():
    """`Nebulized Calderon` is its own product; `Calderon` is not its brand."""
    assert (
        brand_matched_results(LIVE_RESULTS, product="Nebulized Tyvaso", generic="treprostinil")
        == []
    )
    # ... and not even when the alias expander offers the parent brand.
    assert (
        brand_matched_results(
            LIVE_RESULTS,
            product="Nebulized Tyvaso",
            generic="treprostinil",
            aliases=["Tyvaso"],
        )
        == []
    )


def test_a_fuller_sku_name_is_still_the_same_product():
    """`Calderon` against a registry brand `Calderon Extended-Release`."""
    sku = {
        "application_number": "NDA000003",
        "openfda": {"brand_name": ["CALDERON EXTENDED-RELEASE"], "generic_name": ["calderinol"]},
    }
    assert brand_matched_results([sku], product="Calderon", generic="calderinol") == [
        (sku, "CALDERON EXTENDED-RELEASE")
    ]
    # Unless a stored alias says that fuller name is a sibling brand.
    assert (
        brand_matched_results(
            [sku], product="Calderon", generic="calderinol", aliases=["Calderon Extended-Release"]
        )[0][1]
        == "CALDERON EXTENDED-RELEASE"
    )


def test_a_molecule_variant_spelling_cannot_select_an_application():
    """The exclusion compares against the molecule the record declares.

    A candidate equal to the upload's spelling was the only one refused, so
    `calderinol phosphate` against a record declaring `calderinol` survived and
    selected whichever sibling was marketed under the molecule name.
    """
    sibling = {
        "application_number": "NDA000004",
        "openfda": {
            "brand_name": ["CALDERINOL", "NUVESSA"],
            "generic_name": ["CALDERINOL"],
            "substance_name": ["CALDERINOL PHOSPHATE"],
        },
    }
    assert names_the_molecule("calderinol phosphate", molecule_names(sibling, "calderinol"))
    assert (
        brand_matched_results(
            [sibling], product="Calderon", generic="calderinol", aliases=["calderinol phosphate"]
        )
        == []
    )


def test_no_match_is_reported_rather_than_guessed():
    assert brand_matched_results(LIVE_RESULTS, product="Winrevair", generic="sotatercept") == []
    assert brand_matched_results([], product="Tyvaso") == []


def test_approval_date_is_scoped_to_the_applications_that_matched():
    matched = brand_matched_results(LIVE_RESULTS, product="Tyvaso", generic="treprostinil")
    scoped, field = earliest_approval_date([r for r, _ in matched])
    assert scoped == "2009-07-30"
    assert "submissions" in field
    # Across every result the earliest date belongs to a sibling's application.
    unscoped, _ = earliest_approval_date(LIVE_RESULTS)
    assert unscoped == "2002-05-21"
    assert scoped != unscoped


def test_a_brand_with_two_applications_is_dated_from_the_earlier_one():
    """The later one is a line extension, not the product's approval."""
    line_extension = {
        "application_number": "NDA000006",
        "openfda": {"brand_name": ["CALDERON"], "generic_name": ["calderinol"], "route": ["INTRAVENOUS"]},
        "submissions": [
            {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20210729"}
        ],
    }
    original = {
        "application_number": "NDA000007",
        "openfda": {"brand_name": ["CALDERON"], "generic_name": ["calderinol"], "route": ["ORAL"]},
        "submissions": [
            {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20151221"}
        ],
    }
    for order in ([line_extension, original], [original, line_extension]):
        matched = brand_matched_results(order, product="Calderon", generic="calderinol")
        assert len(matched) == 2
        assert earliest_approval_date([r for r, _ in matched])[0] == "2015-12-21"
        # ... and the fields are read from that same application, not from
        # whichever one the registry happened to return first.
        selected, brand = earliest_approved_match(matched)
        assert selected["application_number"] == "NDA000007"
        assert selected["openfda"]["route"] == ["ORAL"]
        assert brand == "CALDERON"


def test_an_undated_application_still_answers():
    undated = {"application_number": "NDA000008", "openfda": {"brand_name": ["NUVESSA"]}}
    assert earliest_approved_match([(undated, "NUVESSA")])[0] is undated
    assert earliest_approved_match([]) == (None, None)


def test_brand_names_are_listed_for_diagnostics():
    assert openfda_brand_names({}) == []


def test_parse_openfda_date_handles_compact_and_iso():
    assert parse_openfda_date("20090730") == "2009-07-30"
    assert parse_openfda_date("2009-07-30") == "2009-07-30"
    assert parse_openfda_date("") is None
    assert parse_openfda_date("garbage") is None


def test_every_brand_query_precedes_the_molecule():
    """A combined brand-OR-generic search can exclude the product entirely."""
    scopes = [scope for scope, _ in search_queries("Opsumit", "macitentan")]
    assert [scope.split(":")[0] for scope in scopes] == ["brand"] * len(BRAND_SEARCH_PATHS) + [
        "generic"
    ]
    for scope, query in search_queries("Opsumit", "macitentan"):
        if not scope.startswith("brand"):
            continue
        assert "generic_name" not in query, "the molecule must not widen a brand query"


def test_a_brand_is_asked_for_on_every_path_that_states_one():
    """A discontinued application carries its brand only in `products[]`."""
    queries = {scope: query for scope, query in search_queries("Calderon")}
    assert queries == {
        f"brand:{path}": f'{path}:"Calderon"' for path in BRAND_SEARCH_PATHS
    }
    assert "products.brand_name" in BRAND_SEARCH_PATHS


def test_search_queries_tolerate_missing_inputs():
    assert search_queries("", "treprostinil") == [("generic", 'openfda.generic_name:"treprostinil"')]
    assert search_queries("", None) == []


def test_brands_are_read_from_both_paths_a_record_may_state_them_on():
    assert openfda_brand_names(TYVASO) == ["TYVASO"]
    discontinued = {"application_number": "NDA000002", "products": [{"brand_name": "NUVESSA"}]}
    assert openfda_brand_names(discontinued) == ["NUVESSA"]
    both = {"openfda": {"brand_name": ["CALDERON"]}, "products": [{"brand_name": "CALDERON XR"}]}
    assert openfda_brand_names(both) == ["CALDERON", "CALDERON XR"]


def test_missing_value_placeholders_are_recognised():
    for placeholder in ("Not specified", "not specified.", "N/A", "unknown", "None", "", "  "):
        assert is_missing_value(placeholder), placeholder
    assert is_missing_value(None)
    for real in ("Inhalation", "2009-07-30", "Prostacyclin Vasodilator [EPC]", "0"):
        assert not is_missing_value(real), real


@pytest.mark.asyncio
async def test_a_brand_is_asked_on_every_path_and_the_answers_are_unioned(tmp_path):
    """One path can hold an application the other does not.

    The molecule query stays a fallback: its results are the whole molecule's,
    so adding them to a brand answer would widen it.
    """
    pages = {
        'openfda.brand_name:"Calderon"': [{"application_number": "NDA000009"}],
        'products.brand_name:"Calderon"': [
            {"application_number": "NDA000009"},
            {"application_number": "NDA000010"},
        ],
        'openfda.generic_name:"calderinol"': [{"application_number": "ANDA000011"}],
    }
    asked = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url):
            search = url.split("search=", 1)[1].split("&", 1)[0]
            asked.append(search)
            results = pages.get(search, [])
            return SimpleNamespace(
                status_code=200 if results else 404,
                json=lambda: {"results": results},
                raise_for_status=lambda: None,
            )

    connector = OpenFDAConnector(file_store=SimpleNamespace(put=_noop))
    with patch.object(openfda_module.httpx, "AsyncClient", lambda **_: Client()):
        sources = await connector.retrieve(
            run_id="r", job_id="j", brand="Calderon", generic="calderinol"
        )

    numbers = [r["application_number"] for r in sources[0].metadata["results"]]
    assert numbers == ["NDA000009", "NDA000010"]
    assert 'openfda.generic_name:"calderinol"' not in asked, "the molecule widened a brand answer"
    assert sources[0].notes is None
