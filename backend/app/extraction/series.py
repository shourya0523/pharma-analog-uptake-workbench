"""Stage 3d: one series per product from every observation every document made.

Documents overlap. A quarter is stated in its own release, again as the
prior-year comparative a year later, again inside a year-to-date column, and
sometimes restated at coarser precision. Documents also disagree: a
collaboration line beside a product line, a shipment figure beside a revenue
figure, a stub period beside a full one. This stage turns that pile into one
value per period and geography, or into an explicit verdict that it cannot.

Nothing here knows an issuer. The rules are about evidence:

* Figures that agree within rounding are one figure; the most precise
  statement wins, and among equals the document whose own period it is.
* A line item that is exactly the product outranks a qualified line
  ("Alliance revenue - X"), and a sentence tying the amount to the product's
  revenue outranks one that does not.
* Disagreement that survives those preferences is reported as a review
  verdict, never settled by picking a side.
* A quarter no document states is derived only when the issuer's own totals
  determine it uniquely, preferring a stated year-to-date figure over a sum
  of quarters the issuer rounded separately.
* A quarter split by an ownership change is assembled only from dated parts
  that tile it.
* A unit no document declared is inferred only from the same product's
  declared neighbours, and is labelled as inferred.
"""

from __future__ import annotations

import collections
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from app.extraction.adjudicate import (
    IMPOSSIBLE,
    NEEDS_REVIEW,
    RESOLVED,
    Candidate,
    Verdict,
    adjudicate_reported_value,
    rounding_tolerance,
)
from app.extraction.derive import assemble_split_ownership_quarter
from app.extraction.fingerprint import UNIT_SCALE_TO_MILLIONS
from app.extraction.process import FX_USD_PER_UNIT
from app.extraction.readers import Observation

_QUARTER_RE = re.compile(r"(\d{4})Q([1-4])")
_QUARTERS_IN = {"annual": (1, 2, 3, 4), "nine_month": (1, 2, 3), "six_month": (1, 2)}
_NEGLIGIBLE = 0.05


@dataclass(frozen=True)
class SeriesValue:
    """One resolved figure of the series."""

    product: str
    period: str
    period_type: str
    geography: str | None
    value_usd_millions: float
    value_as_reported: float
    unit_label: str
    currency: str
    route: str                          # read | derived | bridged | propagated
    derivation: str
    status: str                         # resolved | needs_review | impossible
    detail: str
    source_urls: tuple[str, ...]
    source_quote: str
    inputs: tuple[str, ...] = field(default_factory=tuple)
    covers: tuple[str, str] | None = None
    normalization: str = "ok"
    # Other results the same evidence supports within the issuer's rounding:
    # a full year less a stated nine months, or less the three quarters that
    # sum to a different nine months. The primary is stated; these are kept
    # visible rather than reconciled.
    alternates: tuple[float, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "period": self.period,
            "period_type": self.period_type,
            "geography": self.geography,
            "value_usd_millions": self.value_usd_millions,
            "value_as_reported": self.value_as_reported,
            "unit_label": self.unit_label,
            "currency": self.currency,
            "route": self.route,
            "derivation": self.derivation,
            "status": self.status,
            "detail": self.detail,
            "source_urls": list(self.source_urls),
            "source_quote": self.source_quote,
            "inputs": list(self.inputs),
            "covers": list(self.covers) if self.covers else None,
            "normalization": self.normalization,
            "alternates": list(self.alternates),
        }


@dataclass
class Series:
    product: str
    values: list[SeriesValue]
    verdicts: list[SeriesValue]        # periods that did not resolve
    notes: list[str]

    def resolved(self, geography: str | None = None) -> dict[str, SeriesValue]:
        return {
            v.period: v
            for v in self.values
            if v.status == RESOLVED and (geography is None or v.geography == geography)
        }


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Norm:
    observation: Observation
    value_usd: float | None
    unit_label: str
    status: str

    @property
    def step(self) -> float:
        """The size of the last stated digit, in USD millions.

        "$2.9 billion" is stated to a tenth of a billion, so it agrees with
        2,901 million; "121,718" thousand is stated to a thousand.
        """
        decimals = _precision(self.observation.value_as_reported)
        scale = UNIT_SCALE_TO_MILLIONS.get(self.unit_label, 1.0)
        return (10 ** (-decimals)) * scale


def _year_of(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


def _to_usd_millions(value: float, unit_label: str, currency: str, period: str) -> tuple[float | None, str]:
    scale = UNIT_SCALE_TO_MILLIONS.get(unit_label)
    if scale is None:
        return None, "unknown_unit"
    in_millions = value * scale
    if currency == "USD":
        return round(in_millions, 6), "ok"
    year = _year_of(period)
    rate = FX_USD_PER_UNIT.get(currency, {}).get(year) if year else None
    if rate is None:
        return None, f"no_fx_rate_for_{currency}_{year}"
    return round(in_millions * rate, 6), "ok"


def _quarter_index(period: str) -> int | None:
    match = _QUARTER_RE.fullmatch(period or "")
    if not match:
        return None
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def normalize_observations(observations: list[Observation]) -> list[_Norm]:
    """USD millions for every observation, inferring undeclared units from neighbours."""
    declared: list[_Norm] = []
    undeclared: list[Observation] = []
    for obs in observations:
        if obs.unit_declared:
            unit_label = obs.unit_label
            if unit_label in {"millions", "billions"} and abs(obs.value_as_reported) >= 1_000_000:
                # "$45,121,000" printed in full inside a document whose
                # context says millions: no product revenue is a trillion
                # dollars, so the cell carries its own magnitude.
                unit_label = "units"
            value, status = _to_usd_millions(obs.value_as_reported, unit_label, obs.currency, obs.period)
            declared.append(_Norm(obs, value, unit_label, status))
        else:
            undeclared.append(obs)

    # Neighbours a scale can be checked against: declared quarterly values by
    # quarter index and geography.
    anchors: dict[tuple[str | None, int], list[float]] = defaultdict(list)
    for norm in declared:
        index = _quarter_index(norm.observation.period)
        if norm.value_usd is not None and index is not None and norm.observation.period_type == "quarterly":
            anchors[(norm.observation.geography, index)].append(norm.value_usd)

    out = list(declared)
    # Nearest declared neighbour first; an inferred value then anchors its
    # own neighbours, so a long run of undeclared documents resolves from
    # one declared quarter at either end.
    pending = sorted(undeclared, key=lambda o: o.period)
    progress = True
    while pending and progress:
        progress = False
        remaining: list[Observation] = []
        for obs in pending:
            index = _quarter_index(obs.period)
            candidates: list[float] = []
            if index is not None:
                for offset in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6, -7, 7, -8, 8):
                    candidates.extend(anchors.get((obs.geography, index + offset), []))
                    if candidates:
                        break
            chosen: str | None = None
            if candidates:
                reference = sum(candidates) / len(candidates)
                fits = []
                for label, scale in UNIT_SCALE_TO_MILLIONS.items():
                    scaled = obs.value_as_reported * scale
                    if reference > 0 and 0.2 <= scaled / reference <= 5.0:
                        fits.append(label)
                if len(fits) == 1:
                    chosen = fits[0]
            if chosen is None:
                remaining.append(obs)
                continue
            value, status = _to_usd_millions(obs.value_as_reported, chosen, obs.currency, obs.period)
            out.append(_Norm(obs, value, chosen, "unit_inferred_from_series" if status == "ok" else status))
            if value is not None and index is not None and obs.period_type == "quarterly":
                anchors[(obs.geography, index)].append(value)
            progress = True
        pending = remaining
    # A grid has one unit: an observation still undeclared adopts the unit
    # its own grid's other rows were inferred to have.
    inferred_by_grid: dict[tuple[str, int], collections.Counter] = defaultdict(collections.Counter)
    for norm in out:
        if norm.status == "unit_inferred_from_series" and norm.observation.table_index >= 0:
            inferred_by_grid[(norm.observation.source_url, norm.observation.table_index)][norm.unit_label] += 1
    still: list[Observation] = []
    for obs in pending:
        counts = inferred_by_grid.get((obs.source_url, obs.table_index))
        if counts:
            unit = counts.most_common(1)[0][0]
            value, status = _to_usd_millions(obs.value_as_reported, unit, obs.currency, obs.period)
            out.append(_Norm(obs, value, unit, "unit_inferred_from_grid" if status == "ok" else status))
            continue
        still.append(obs)
    for obs in still:
        out.append(_Norm(obs, None, obs.unit_label, "unit_not_declared"))
    return out


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

def _precision(value: float) -> int:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return len(text.split(".")[1]) if "." in text else 0


def _agree(a: float, b: float, step: float | None = None) -> bool:
    """Two statements of one figure that differ only by rounding."""
    if step is None:
        coarse = min(_precision(a), _precision(b))
        step = 10 ** (-coarse)
    return abs(a - b) <= 0.5 * step + 1e-9 or abs(a - b) <= 1e-6


def _rank(norm: _Norm) -> tuple:
    obs = norm.observation
    return (
        obs.specificity,
        0 if norm.status == "ok" else 1,
        0 if "current_period_column" in obs.notes else 1,
        norm.step,
        0 if obs.method == "grid" else 1,
    )


def _best_of_agreeing(group: list[_Norm]) -> _Norm:
    return sorted(group, key=_rank)[0]


def reconcile_period(norms: list[_Norm], product: str) -> SeriesValue | None:
    """One value for one (period, geography) bucket, or a verdict."""
    usable = [n for n in norms if n.value_usd is not None]
    if not usable:
        return None
    first = usable[0].observation
    # Cluster values that agree within rounding.
    clusters: list[list[_Norm]] = []
    for norm in sorted(usable, key=lambda n: n.step):
        for cluster in clusters:
            if _agree(cluster[0].value_usd, norm.value_usd, max(cluster[0].step, norm.step)):
                cluster.append(norm)
                break
        else:
            clusters.append([norm])

    def make(norm: _Norm, status: str, detail: str, code: str, cluster: list[_Norm]) -> SeriesValue:
        obs = norm.observation
        return SeriesValue(
            product=product,
            period=obs.period,
            period_type=obs.period_type,
            geography=obs.geography,
            value_usd_millions=norm.value_usd,
            value_as_reported=obs.value_as_reported,
            unit_label=norm.unit_label,
            currency=obs.currency,
            route="read",
            derivation=code,
            status=status,
            detail=detail,
            source_urls=tuple(sorted({n.observation.source_url for n in cluster})),
            source_quote=obs.source_quote,
            covers=obs.covers,
            normalization=norm.status,
        )

    if len(clusters) == 1:
        best = _best_of_agreeing(clusters[0])
        return make(best, RESOLVED, "", "direct_reported", clusters[0])

    # Prefer the most specific evidence: exact product lines over qualified
    # ones, revenue-tied sentences over weaker ties.
    best_specificity = min(n.observation.specificity for n in usable)
    specific = [c for c in clusters if any(n.observation.specificity == best_specificity for n in c)]
    if len(specific) == 1:
        best = _best_of_agreeing([n for n in specific[0] if n.observation.specificity == best_specificity])
        return make(best, RESOLVED, "less specific line items set aside", "direct_reported", specific[0])

    # Among equally specific statements, the document's own period column
    # outranks a restated comparative.
    current = [c for c in specific if any("current_period_column" in n.observation.notes for n in c)]
    if len(current) == 1:
        best = _best_of_agreeing(current[0])
        return make(best, RESOLVED, "own-period statement outranks restatements", "direct_reported", current[0])

    candidates = [
        Candidate(value=c[0].value_usd, scope=first.geography or "unspecified", basis="as_reported",
                  source=c[0].observation.source_url)
        for c in specific
    ]
    verdict: Verdict = adjudicate_reported_value(first.geography or "unspecified", candidates)
    best = _best_of_agreeing(specific[0])
    if verdict.resolved:
        chosen = next(c for c in specific if _agree(c[0].value_usd, verdict.value))
        return make(_best_of_agreeing(chosen), RESOLVED, verdict.detail, verdict.code, chosen)
    return make(best, verdict.status, verdict.detail, verdict.code, specific[0])


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------

def _split(period: str) -> tuple[int, int] | None:
    match = _QUARTER_RE.fullmatch(period or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def derive_residual_quarters(
    values: dict[tuple[str, str], SeriesValue],
    *,
    product: str,
    commercial_start: str | None = None,
) -> list[SeriesValue]:
    """Quarters the issuer left implicit against a total it stated.

    A stated year-to-date figure that covers every other quarter is preferred
    to summing those quarters, because the issuer rounded each period on its
    own and its stated figure is the one its own arithmetic used.
    """
    start = _split(commercial_start or "")
    quarters: dict[int, dict[int, SeriesValue]] = defaultdict(dict)
    totals: dict[tuple[int, str], SeriesValue] = {}
    for (period, period_type), value in values.items():
        if value.status != RESOLVED or value.covers:
            continue
        year = _year_of(period)
        if year is None:
            continue
        if period_type == "quarterly":
            parsed = _split(period)
            if parsed:
                quarters[year][parsed[1]] = value
        elif period_type in _QUARTERS_IN:
            totals[(year, period_type)] = value

    derived: list[SeriesValue] = []
    for (year, period_type), total in sorted(totals.items(), key=lambda kv: (kv[0][0], -len(_QUARTERS_IN[kv[0][1]]))):
        members = _QUARTERS_IN[period_type]
        if start is not None:
            if year < start[0]:
                continue
            if year == start[0]:
                members = tuple(q for q in members if q >= start[1])
                if not members:
                    continue
        have = quarters.get(year, {})
        # A stated sub-total (six or nine months) accounts for the quarters
        # inside it whether or not they are stated individually, so a full
        # year less a stated nine months determines the fourth quarter even
        # when the first two are not known. The largest such sub-total is
        # used; the issuer's own figure beats a sum of separately rounded
        # quarters.
        subtotal: SeriesValue | None = None
        covered: tuple[int, ...] = ()
        for sub_type, sub_members in _QUARTERS_IN.items():
            if len(sub_members) >= len(members):
                continue
            candidate = totals.get((year, sub_type))
            if candidate is None or len(sub_members) <= len(covered):
                continue
            outside = [q for q in members if q not in sub_members and q not in have]
            if len(outside) == 1:
                subtotal, covered = candidate, sub_members
        if subtotal is not None:
            missing = [q for q in members if q not in have and q not in covered]
        else:
            missing = [q for q in members if q not in have]
        if len(missing) != 1:
            continue
        target = missing[0]
        residual = total.value_usd_millions
        inputs = [total.period + ":" + period_type]
        alternates: list[float] = []
        if subtotal is not None:
            residual -= subtotal.value_usd_millions
            inputs.append(f"{year}:{[k for k, v in _QUARTERS_IN.items() if v == covered][0]}")
            rest = [q for q in members if q != target and q not in covered]
            # The same total less the quarters the sub-total is made of, when
            # they are all stated: the issuer rounded each on its own, so the
            # two can differ by a unit, and both are the issuer's arithmetic.
            if all(q in have for q in covered):
                by_parts = total.value_usd_millions - sum(have[q].value_usd_millions for q in covered)
                for q in rest:
                    by_parts -= have[q].value_usd_millions
                primary = residual - sum(have[q].value_usd_millions for q in rest)
                if abs(by_parts - primary) <= rounding_tolerance(len(covered)):
                    alternates.append(round(by_parts, 6))
        else:
            rest = [q for q in members if q != target]
        for q in rest:
            residual -= have[q].value_usd_millions
            inputs.append(have[q].period)
        if residual < -_NEGLIGIBLE:
            continue
        residual = round(max(residual, 0.0), 6)
        alternates = [a for a in alternates if abs(a - residual) > 1e-9]
        period = f"{year}Q{target}"
        derived.append(
            SeriesValue(
                product=product,
                period=period,
                period_type="quarterly",
                geography=total.geography,
                value_usd_millions=residual,
                value_as_reported=residual,
                unit_label="millions",
                currency="USD",
                route="derived",
                derivation="stated_total_less_stated_parts",
                status=RESOLVED,
                detail=f"{total.period} {period_type} {total.value_usd_millions:g} less {', '.join(inputs[1:])} yields {period} {residual:g}",
                source_urls=tuple(sorted(set(total.source_urls) | {u for q in rest for u in have[q].source_urls} | (set(subtotal.source_urls) if subtotal else set()))),
                source_quote=total.source_quote,
                inputs=tuple(inputs),
                alternates=tuple(alternates),
            )
        )
        quarters[year][target] = derived[-1]
    return derived


def derive_partial_remainders(
    values: dict[tuple[str, str], SeriesValue], observations_by_period: dict[tuple[str, str], list[_Norm]], *, product: str
) -> list[SeriesValue]:
    """A dated year-to-date figure less the full quarters inside it.

    An acquirer's first nine-month figure starts on the closing date. Taking
    off the full quarters it contains leaves the stub the acquirer sold in
    the closing quarter, dated from the close to that quarter's end.
    """
    derived: list[SeriesValue] = []
    for (period, period_type), norms in observations_by_period.items():
        if period_type not in _QUARTERS_IN:
            continue
        for norm in norms:
            obs = norm.observation
            if not obs.covers or norm.value_usd is None:
                continue
            start, end = obs.covers
            year = _year_of(period)
            if year is None or not start.startswith(str(year)):
                continue
            start_quarter = (int(start[5:7]) - 1) // 3 + 1
            members = _QUARTERS_IN[period_type]
            later = [q for q in members if q > start_quarter]
            if not all((f"{year}Q{q}", "quarterly") in values for q in later):
                continue
            remainder = norm.value_usd - sum(values[(f"{year}Q{q}", "quarterly")].value_usd_millions for q in later)
            if remainder < -_NEGLIGIBLE:
                continue
            from calendar import monthrange

            end_month = start_quarter * 3
            covers = (start, f"{year}-{end_month:02d}-{monthrange(year, end_month)[1]:02d}")
            derived.append(
                SeriesValue(
                    product=product,
                    period=f"{year}Q{start_quarter}",
                    period_type="quarterly",
                    geography=obs.geography,
                    value_usd_millions=round(max(remainder, 0.0), 6),
                    value_as_reported=round(max(remainder, 0.0), 6),
                    unit_label="millions",
                    currency="USD",
                    route="derived",
                    derivation="dated_total_less_full_quarters",
                    status=RESOLVED,
                    detail=f"{period} {period_type} from {start} ({norm.value_usd:g}) less {', '.join(f'{year}Q{q}' for q in later)}",
                    source_urls=(obs.source_url,),
                    source_quote=obs.source_quote,
                    inputs=(f"{period}:{period_type}",) + tuple(f"{year}Q{q}" for q in later),
                    covers=covers,
                )
            )
    return derived


# Geographies that together are the whole world. A product whose total is
# not printed but whose regional lines are is still stated worldwide.
_PARTITIONS = (
    frozenset({"United States", "International"}),
    frozenset({"United States", "Europe", "International"}),
    frozenset({"United States", "Europe", "Japan", "International"}),
)


def sum_geography_partitions(series_by_geo: dict[str | None, dict[tuple[str, str], SeriesValue]], *, product: str) -> list[SeriesValue]:
    """Worldwide from regional lines that partition the world, when no total is stated."""
    out: list[SeriesValue] = []
    worldwide = series_by_geo.get("Worldwide", {})
    periods: set[tuple[str, str]] = set()
    for geography, series in series_by_geo.items():
        if geography not in {"United States", "Europe", "Japan", "International"}:
            continue
        periods |= set(series)
    for key in sorted(periods):
        if key in worldwide:
            continue
        parts = {
            geography: series[key]
            for geography, series in series_by_geo.items()
            if geography in {"United States", "Europe", "Japan", "International"} and key in series
        }
        if frozenset(parts) not in _PARTITIONS:
            continue
        if any(v.status != RESOLVED or v.covers for v in parts.values()):
            continue
        total = round(sum(v.value_usd_millions for v in parts.values()), 6)
        first = next(iter(parts.values()))
        out.append(
            SeriesValue(
                product=product,
                period=key[0],
                period_type=key[1],
                geography="Worldwide",
                value_usd_millions=total,
                value_as_reported=total,
                unit_label="millions",
                currency="USD",
                route="derived",
                derivation="sum_of_geography_partition",
                status=RESOLVED,
                detail=" + ".join(f"{g} {v.value_usd_millions:g}" for g, v in sorted(parts.items())),
                source_urls=tuple(sorted({u for v in parts.values() for u in v.source_urls})),
                source_quote="; ".join(v.source_quote for v in parts.values()),
                inputs=tuple(f"{g}:{key[0]}" for g in sorted(parts)),
            )
        )
    return out


def assemble_bridges(partials: list[SeriesValue], *, product: str) -> list[SeriesValue]:
    """Dated parts that tile one quarter become that quarter."""
    by_period: dict[tuple[str, str | None], list[SeriesValue]] = defaultdict(list)
    for value in partials:
        if value.covers and value.period_type == "quarterly":
            by_period[(value.period, value.geography)].append(value)
    out: list[SeriesValue] = []
    for (period, geography), parts in by_period.items():
        # Distinct coverage spans only; the same span stated twice is one part.
        spans: dict[tuple[str, str], SeriesValue] = {}
        for part in parts:
            spans.setdefault(part.covers, part)
        components = [
            {"covers": f"{p.covers[0]}/{p.covers[1]}", "value": p.value_usd_millions, "source_url": p.source_urls[0] if p.source_urls else ""}
            for p in spans.values()
        ]
        if len(components) < 2:
            continue
        assembled = assemble_split_ownership_quarter(period, components)
        if assembled is None:
            continue
        out.append(
            SeriesValue(
                product=product,
                period=period,
                period_type="quarterly",
                geography=geography,
                value_usd_millions=assembled,
                value_as_reported=assembled,
                unit_label="millions",
                currency="USD",
                route="bridged",
                derivation="dated_parts_tile_the_quarter",
                status=RESOLVED,
                detail=" + ".join(f"{c['covers']}={c['value']:g}" for c in components),
                source_urls=tuple(sorted({u for p in spans.values() for u in p.source_urls})),
                source_quote="; ".join(p.source_quote for p in spans.values()),
                inputs=tuple(c["covers"] for c in components),
            )
        )
    return out


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _geography_key(obs: Observation) -> str | None:
    return obs.geography


def assemble_series(
    observations: list[Observation],
    *,
    product: str,
    commercial_start: str | None = None,
) -> Series:
    """Reconcile, then derive, then bridge - each step over the last's output."""
    # A line that names the product with a qualifier ("Alliance revenue -
    # X", "X delivery pumps and supplies", "Nebulized X" when X is the
    # family) is a different line item. It is evidence about the document's
    # structure, never a value of this product's series.
    observations = [o for o in observations if o.line_item == "exact"]
    # A generic "product sales" line attributed to this product because the
    # filing names no other is provisional: it stands only for periods the
    # product's own stated and derived figures leave empty, because when the
    # two exist and differ, the generic line was not this product's alone.
    provisional = [o for o in observations if "generic_product_line" in o.notes]
    observations = [o for o in observations if "generic_product_line" not in o.notes]
    norms = normalize_observations(observations)
    notes: list[str] = []

    buckets: dict[tuple[str, str, str | None], list[_Norm]] = defaultdict(list)
    partial_norms: dict[tuple[str, str], list[_Norm]] = defaultdict(list)
    for norm in norms:
        obs = norm.observation
        if obs.covers:
            partial_norms[(obs.period, obs.period_type)].append(norm)
            continue
        buckets[(obs.period, obs.period_type, _geography_key(obs))].append(norm)

    values: list[SeriesValue] = []
    verdicts: list[SeriesValue] = []
    for key in sorted(buckets, key=lambda k: (k[0], k[1], k[2] or "")):
        resolved = reconcile_period(buckets[key], product)
        if resolved is None:
            continue
        (values if resolved.status == RESOLVED else verdicts).append(resolved)

    # Partial-period statements resolve among themselves the same way, and
    # keep their coverage so they never masquerade as full quarters.
    partial_values: list[SeriesValue] = []
    partial_buckets: dict[tuple[str, str, str | None, tuple[str, str]], list[_Norm]] = defaultdict(list)
    for (period, period_type), group in partial_norms.items():
        for norm in group:
            obs = norm.observation
            partial_buckets[(period, period_type, obs.geography, obs.covers)].append(norm)
    for key in sorted(partial_buckets, key=lambda k: (k[0], k[1], k[2] or "", k[3])):
        resolved = reconcile_period(partial_buckets[key], product)
        if resolved is not None and resolved.status == RESOLVED:
            partial_values.append(resolved)

    by_geo: dict[str | None, dict[tuple[str, str], SeriesValue]] = defaultdict(dict)
    for value in values:
        by_geo[value.geography][(value.period, value.period_type)] = value

    # Stubs an acquirer's dated year-to-date figure implies.
    for geography, series in by_geo.items():
        partial_by_period: dict[tuple[str, str], list[_Norm]] = defaultdict(list)
        for (period, period_type), group in partial_norms.items():
            partial_by_period[(period, period_type)].extend(n for n in group if n.observation.geography == geography)
        partial_values.extend(derive_partial_remainders(series, partial_by_period, product=product))

    # Quarters tiled by dated parts.
    bridged = assemble_bridges(partial_values, product=product)
    for value in bridged:
        if (value.period, value.period_type) not in by_geo[value.geography]:
            by_geo[value.geography][(value.period, value.period_type)] = value
            values.append(value)

    # Regional lines that partition the world state the worldwide figure.
    for value in sum_geography_partitions(by_geo, product=product):
        by_geo["Worldwide"][(value.period, value.period_type)] = value
        values.append(value)

    # Residuals against stated totals, per geography, until nothing more
    # follows: a quarter derived from a nine-month total can be the one that
    # lets the full year determine another.
    for geography, series in by_geo.items():
        while True:
            added = 0
            for value in derive_residual_quarters(series, product=product, commercial_start=commercial_start):
                if (value.period, value.period_type) not in series:
                    series[(value.period, value.period_type)] = value
                    values.append(value)
                    added += 1
            if not added:
                break

    if provisional:
        have = {(v.period, v.period_type, v.geography) for v in values}
        provisional_norms = normalize_observations(provisional + observations)
        buckets_p: dict[tuple[str, str, str | None], list[_Norm]] = defaultdict(list)
        for norm in provisional_norms:
            obs = norm.observation
            if "generic_product_line" in obs.notes and (obs.period, obs.period_type, obs.geography) not in have:
                buckets_p[(obs.period, obs.period_type, obs.geography)].append(norm)
        for key in sorted(buckets_p, key=lambda k: (k[0], k[1], k[2] or "")):
            resolved = reconcile_period(buckets_p[key], product)
            if resolved is not None and resolved.status == RESOLVED:
                values.append(replace(resolved, derivation="generic_product_line_of_sole_product"))
                notes.append(f"{resolved.period}: generic product line taken as {product}'s")

    values.sort(key=lambda v: (v.geography or "", v.period_type, v.period))
    return Series(product=product, values=values, verdicts=verdicts, notes=notes)


def propagate_family(
    parent: Series,
    *,
    product: str,
    sibling_periods: Iterable[str],
) -> list[SeriesValue]:
    """A family line before its formulation split is the one formulation on sale."""
    siblings = sorted(set(sibling_periods))
    if not siblings:
        return []
    split_at = siblings[0]
    out: list[SeriesValue] = []
    for value in parent.values:
        if value.status != RESOLVED or value.period_type != "quarterly" or value.period >= split_at:
            continue
        out.append(
            replace(
                value,
                product=product,
                route="propagated",
                derivation="family_total_before_formulation_split",
                detail=f"{parent.product} total attributed to {product}: sole formulation on sale before {split_at}",
                inputs=(f"{parent.product}:{value.period}",),
            )
        )
    return out
