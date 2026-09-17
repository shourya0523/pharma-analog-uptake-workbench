"""A slash between two different names is a pair, not one product's spellings.

`product_aliases` split every alias on `/`, which is right for a franchise
name - one product written two ways - and wrong for a co-packaged pair. Split,
the other product's name enters this product's alias set, and there it stops
being another product's name at all: `read_label` drops a marked name it
already holds as this product's own, so a line naming both reads as this
product's own line, `combined_with` comes back empty, and the pair is never
recorded.

Nothing here consults a list of brands. Whether a slash joined one product or
two is decided by whether both sides spell the product that was asked about,
and which other product a line names is decided by the filer's own mark.

Invented names: Calderon, Calderon XR, NuVessa, calderinol.
"""

from __future__ import annotations

from app.llm.aliases import merge_aliases
from app.parsing.evidence import product_aliases
from app.parsing.labels import read_label

MARK = "®"


def test_a_franchise_name_still_splits():
    """Both sides spell the product, so the slash joined two spellings."""
    aliases = product_aliases("Calderon", "calderinol",
                              extra=["Calderon (calderinol)/Calderon XR"])
    assert "Calderon XR" in aliases


def test_a_pair_keeps_the_joined_form_and_does_not_yield_the_other_name():
    aliases = product_aliases("Calderon", None, extra=["Calderon/NuVessa"])
    assert "Calderon/NuVessa" in aliases
    assert "NuVessa" not in aliases


def test_the_plus_form_was_never_the_problem():
    assert product_aliases("Calderon", None, extra=["Calderon + NuVessa"]) == [
        "Calderon", "Calderon + NuVessa"
    ]


def test_the_filers_mark_names_the_other_product_once_the_alias_is_gone():
    """End to end: the aliases a run would hold, against the line as printed."""
    aliases = merge_aliases(
        "Calderon", "calderinol",
        llm_aliases=["Calderon", "calderinol", "Calderon + NuVessa", "Calderon/NuVessa"],
    )
    reading = read_label(f"Total Calderon{MARK} + NuVessa{MARK} sales", aliases)
    assert reading.combined_with == ("NuVessa",)
    assert reading.flags == ("combined_line",)


def test_the_same_line_reads_as_this_products_own_when_the_pair_was_split():
    """The defect, stated as the property that fails: hold the other name as
    our own and the marked name is dropped, so nothing records the pair."""
    reading = read_label(
        f"Total Calderon{MARK} + NuVessa{MARK} sales",
        ["Calderon", "Calderon + NuVessa", "Calderon/NuVessa", "NuVessa"],
    )
    assert reading.combined_with == ()
