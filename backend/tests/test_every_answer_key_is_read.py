"""Every answer key under seed/ is one `answer_keys.py` can actually read.

`tests/answer_keys.py` discovers the keys by globbing rather than naming them,
so a set added later cannot be forgotten. Discovery is only half of it: a file
can be found and still yield nothing, because it spells its columns
differently from every key that came before. The result is the same as not
finding it - a held-out set drawn from its issuers passes every guard while
reusing an issuer that is already spent.

So this asserts the other half. A key file that carries rows must give up at
least one name, and the assertion message says which key names are understood,
because adding the new spelling to `ISSUER_KEYS` or `PRODUCT_KEYS` is the fix.
"""

from __future__ import annotations

from tests.answer_keys import (
    ISSUER_KEYS,
    PRODUCT_KEYS,
    SEED,
    answer_key_paths,
    cases_in,
    issuers_in,
    products_in,
)


def test_the_glob_reaches_a_key_directly_under_seed():
    """A set need not sit in a directory of its own.

    The glob this replaced was `seed/*/*.json`, one level deep, and a key
    written straight into `seed/` was invisible to every guard built on it.
    """
    found = {p.name for p in answer_key_paths() if p.parent == SEED}
    assert found, "nothing directly under seed/ is discovered; the glob is too deep"


def test_every_key_with_rows_yields_a_name():
    silent = []
    for path in answer_key_paths():
        rows = cases_in(path)
        if not rows:
            # A summary or a manifest, not a key. Nothing to be spent.
            continue
        if all("case_id" in row for row in rows):
            # An answer about a constructed case rather than about a company:
            # the row is identified by what it tests, and the figures in it
            # were taken from whichever key already spends that issuer. There
            # is no name column to miss, so an absence here is not a hole.
            continue
        if not (issuers_in(path) or products_in(path)):
            silent.append(str(path.relative_to(SEED)))
    assert not silent, (
        f"these answer keys carry rows and name nothing: {silent}. "
        f"Issuers are read from {ISSUER_KEYS} and products from {PRODUCT_KEYS}; "
        f"a key that spells either differently is invisible to every rule-4 guard."
    )
