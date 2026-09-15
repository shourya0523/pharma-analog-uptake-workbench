"""The words for a queue reason live where the reason is decided.

The review page once kept its own table of reason prose, a copy of what
`select_validation_tasks` attaches. A reason added on one side and not the
other fell back to showing the bare reason - which is a failure of the same
shape as every hand-maintained mirror: the list and its producer drifting
apart with nothing to say so.
"""

from __future__ import annotations

import ast
import inspect

from app.api import products as products_api
from app.domain.models import NO_FILER_OF_RECORD, REPORTED_WITH_ANOTHER_PRODUCT
from app.validation import sampling
from tests.test_products_api import _items, client  # noqa: F401


def _reasons_attached_by(function) -> set[str]:
    """Every literal reason `add(dp, "...")` records, read from the code."""
    tree = ast.parse(inspect.getsource(function))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "add"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.add(node.args[1].value)
    return found


def test_every_queue_reason_has_its_prose():
    attached = _reasons_attached_by(sampling.select_validation_tasks)
    assert attached, "the sampler attaches no reasons; the parse found nothing"
    assert attached == set(sampling.REASON_HELP), (
        "a reason the sampler attaches has no prose, or prose names a reason "
        "nothing attaches"
    )


def test_the_missing_quarter_reasons_have_theirs_too():
    """The queue decides these two itself, so their prose sits beside them."""
    produced = {products_api._missing_reason(products_api.WHOLE_PRODUCT_PERIOD),
                products_api._missing_reason("2024Q3"),
                products_api._missing_reason("2024Q3", f"[{NO_FILER_OF_RECORD}] nobody filed"),
                products_api._missing_reason(
                    "2024Q3", f"[{REPORTED_WITH_ANOTHER_PRODUCT}] reported as a pair")}
    assert produced == set(products_api.MISSING_REASON_HELP)


def test_the_prose_is_served_with_the_queue(client):  # noqa: F811
    body = client.get("/review/queue").json()
    for item in _items(body):
        assert item["reason"] in body["reason_help"], item["reason"]
        assert body["reason_help"][item["reason"]].strip()
