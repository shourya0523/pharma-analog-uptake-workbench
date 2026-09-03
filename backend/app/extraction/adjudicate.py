"""Stage 3c: decide when there is no answer, and say which kind of no.

Every other stage in this pipeline either produces a value or produces nothing.
That is the right default and it stays the default: almost every quarter an
issuer publishes is stated once, on one basis, and needs no adjudication at
all. This module exists for the small number that are not, and it draws one
distinction that matters downstream:

* ``impossible`` - the question cannot be answered from these documents. No
  value is correct, and none will become correct by trying harder. A worldwide
  figure asked of an issuer that only reports its own territory is impossible;
  so is a quarter whose two halves overlap, because the overlap has been
  counted twice and no arithmetic removes it.
* ``needs_review`` - more than one defensible answer exists, and choosing
  between them is a judgement a person should make. J&J's 2024 Opsumit year is
  2,184 on the standalone line and 2,225 on the combined OPSUMIT / OPSYNVI line
  it was restated onto. Both are true. Which one belongs in a series depends on
  what the series is for, and a pipeline that silently picks one has answered a
  question nobody asked.

The rule these follow, and the reason the thresholds below are as loose as they
are: a verdict other than ``resolved`` is a claim about the *documents*, never
about the pipeline's confidence. Issuers round each published period
independently, so a stated nine-month figure and the sum of its own quarters
routinely differ by 1 - Merck's 2025 Adempas does. That is not a contradiction
and must not be reported as one. Anything this module flags should be something
a careful analyst reading the same pages would also stop at.

``test_no_real_gold_row_needs_review`` runs every quarter in ``seed/gold``
through here and requires all of them to resolve. If a change to this file
starts flagging real data, that test fails, and it is this file that is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RESOLVED = "resolved"
NEEDS_REVIEW = "needs_review"
IMPOSSIBLE = "impossible"


@dataclass(frozen=True)
class Candidate:
    """One figure a document states, with what it is a figure *of*."""

    value: float
    scope: str
    basis: str = "as_reported"
    source: str = ""


@dataclass(frozen=True)
class Verdict:
    status: str
    code: str
    detail: str
    value: float | None = None
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED


def rounding_tolerance(part_count: int) -> float:
    """How far a total may sit from the sum of its parts and still agree.

    Each published period is rounded on its own, so a total and its parts can
    each be off by half a unit in either direction. With three quarters against
    a stated nine months that is two units of slack, which is what lets Merck's
    229-against-230 Adempas pass as the rounding it is.

    Deliberately generous. A tighter bound would make this module report the
    ordinary arithmetic of rounded financial statements as a defect, and a
    warning that fires on healthy data trains everyone to ignore it.
    """
    return 0.5 * (part_count + 1)


def adjudicate_reported_value(
    requested_scope: str, candidates: list[Candidate]
) -> Verdict:
    """Pick the figure that answers the question, or explain why none does."""
    if not candidates:
        return Verdict(
            IMPOSSIBLE,
            "no_stated_value",
            "No document states a figure for this period.",
        )

    in_scope = [c for c in candidates if c.scope == requested_scope]
    if not in_scope:
        offered = ", ".join(sorted({c.scope for c in candidates}))
        return Verdict(
            IMPOSSIBLE,
            "scope_not_reported",
            f"No source reports {requested_scope}; only {offered}. A figure for "
            f"{requested_scope} would have to be invented, not read.",
            candidates=tuple(candidates),
        )

    bases = {c.basis for c in in_scope}
    if len(bases) > 1:
        listed = "; ".join(f"{c.basis} = {c.value:g}" for c in sorted(in_scope, key=lambda c: c.basis))
        return Verdict(
            NEEDS_REVIEW,
            "restated_basis",
            f"The period is reported on more than one basis ({listed}). Both are "
            "stated by the issuer and they answer different questions; which one "
            "belongs in this series is a judgement, not an extraction.",
            candidates=tuple(in_scope),
        )

    values = {round(c.value, 6) for c in in_scope}
    if len(values) == 1:
        return Verdict(RESOLVED, "single_stated_value", "", in_scope[0].value, tuple(in_scope))

    spread = max(values) - min(values)
    if spread <= rounding_tolerance(1):
        # Same scope, same basis, differing only by each source's own rounding.
        chosen = max(in_scope, key=lambda c: c.value)
        return Verdict(
            RESOLVED,
            "agrees_within_rounding",
            f"Sources differ by {spread:g}, within independent rounding.",
            chosen.value,
            tuple(in_scope),
        )
    listed = "; ".join(f"{c.value:g} ({c.source or 'unattributed'})" for c in in_scope)
    return Verdict(
        NEEDS_REVIEW,
        "sources_disagree",
        f"Sources state materially different figures for the same scope and "
        f"basis: {listed}. Nothing in the documents says which supersedes the "
        "other.",
        candidates=tuple(in_scope),
    )


def adjudicate_total_against_parts(
    total: float, parts: dict[str, float], *, expected_parts: int
) -> Verdict:
    """Check a stated total against the periods that make it up.

    Three outcomes, and the middle one is the reason this exists. Parts that
    overshoot their own total are not a rounding difference at any plausible
    scale - something has been double counted or mislabelled, and no residual
    computed from them is trustworthy.
    """
    if expected_parts < 1:
        return Verdict(IMPOSSIBLE, "no_parts_expected", "A total covering no periods.")

    stated = sum(parts.values())
    missing = expected_parts - len(parts)
    tolerance = rounding_tolerance(expected_parts)

    if missing == 0:
        if abs(stated - total) <= tolerance:
            return Verdict(RESOLVED, "parts_reconcile", "", total)
        if stated > total + tolerance:
            return Verdict(
                IMPOSSIBLE,
                "parts_exceed_total",
                f"The {expected_parts} stated periods sum to {stated:g} against a "
                f"stated total of {total:g}. At least one is not the period it "
                "claims to be, and no residual from them can be trusted.",
            )
        return Verdict(
            IMPOSSIBLE,
            "parts_fall_short_of_total",
            f"All {expected_parts} periods are stated and sum to {stated:g}, "
            f"short of the stated total {total:g} by more than rounding. The "
            "total covers something the parts do not.",
        )

    if missing == 1:
        residual = round(total - stated, 6)
        if residual < -tolerance:
            return Verdict(
                IMPOSSIBLE,
                "residual_is_negative",
                f"The stated periods already exceed the total by {-residual:g}, so "
                "the one that is missing would have to be negative.",
            )
        return Verdict(RESOLVED, "single_residual", "", max(residual, 0.0))

    return Verdict(
        NEEDS_REVIEW,
        "underdetermined_residual",
        f"{missing} of {expected_parts} periods are unstated against one total: "
        "one equation in more than one unknown. Any split is a guess, and the "
        "issuer has not published one.",
    )


def adjudicate_positional_solutions(
    requested_scope: str, solutions: list[tuple[str, int, int]]
) -> Verdict:
    """Decide a flattened exhibit block that admits more than one reading.

    A block like J&J's states US, International and Worldwide as three rows of
    bare numbers with no delimiters. The reader recovers which column is which
    quarter by finding the (scope, offset, direction) that explains every cited
    period. Usually exactly one does.

    Sometimes more than one does, and it is not a near miss - it happens when
    the same number genuinely appears in two rows. Opsumit's 2021Q3 worldwide
    figure is 458, and 458 is also the International nine-month figure in the
    same block; with one period to constrain three unknowns, both rows explain
    it perfectly. Picking the requested scope because it is the one asked for
    would be assuming the answer: the whole point of solving for the row is not
    to trust the label.
    """
    if not solutions:
        return Verdict(
            IMPOSSIBLE,
            "no_alignment_explains_the_periods",
            "No combination of scope row, starting column and direction puts "
            "every cited period on its own value. The block does not contain "
            "this series.",
        )
    scopes = {scope for scope, _, _ in solutions}
    if len(scopes) == 1:
        return Verdict(RESOLVED, "unique_alignment", "", None)
    return Verdict(
        NEEDS_REVIEW,
        "ambiguous_scope",
        f"The block reads equally well from {', '.join(sorted(scopes))}: the "
        "same figure appears in more than one scope row and the cited periods "
        "do not distinguish them. Reading the requested row because it is the "
        "requested row would assume what this check exists to establish.",
    )


def adjudicate_split_ownership_quarter(period: str, components: list[dict]) -> Verdict:
    """Verdict wrapper over ``derive.assemble_split_ownership_quarter``.

    The mechanism lives in ``derive`` because it is arithmetic; the verdict
    lives here because "these two halves cannot be added" is a statement about
    the documents. Parts that overlap have counted the closing day twice and
    parts with a gap have lost days neither issuer reported - in both cases no
    sum is the quarter, which is what ``impossible`` means.
    """
    from app.extraction.derive import assemble_split_ownership_quarter

    assembled = assemble_split_ownership_quarter(period, components)
    if assembled is None:
        return Verdict(
            IMPOSSIBLE,
            "partition_does_not_tile",
            f"The parts offered for {period} do not tile it exactly: they "
            "overlap, leave a gap, or fall outside the quarter. Any sum of them "
            "double counts or drops real days.",
        )
    return Verdict(RESOLVED, "partition_tiles", "", assembled)
