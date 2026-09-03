"""Build gold only from independently researched issuer disclosures.

This module intentionally imports no application or pipeline code. Source
manifests are human-researched indexes of SEC and issuer IR documents. The
builder downloads those documents, parses their reported tables, and writes
the benchmark in ``seed/gold``.

Usage:
    cd backend
    uv run python ../scripts/build_independent_gold.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pdfplumber
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "seed"
GOLD_DIR = SEED_DIR / "gold"
SOURCE_DIR = GOLD_DIR / "source_manifests"
CACHE_DIR = Path("/tmp/independent-gold-research")
AS_OF_QUARTER = "2026Q2"
PROVENANCE = "independent_issuer_research"
USER_AGENT = "Pharma analog gold dataset research contact@example.com"

PRODUCT_METADATA = {
    "Tyvaso": {
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_tyvaso_total_reported",
        "commercial_start_quarter": "2009Q3",
        "revenue_scope": "Product family",
        "geography": "Worldwide",
        "formulation": "inhalation",
        "route_of_administration": "inhalation",
    },
    "Nebulized Tyvaso": {
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_tyvaso_nebulized_reported",
        "commercial_start_quarter": "2009Q3",
        "revenue_scope": "Formulation-specific",
        "geography": "Worldwide",
        "formulation": "inhalation solution",
        "route_of_administration": "inhalation",
    },
    "Tyvaso DPI": {
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_tyvaso_dpi_reported",
        "commercial_start_quarter": "2022Q2",
        "revenue_scope": "Formulation-specific",
        "geography": "Worldwide",
        "formulation": "inhalation powder",
        "route_of_administration": "inhalation",
    },
    "Remodulin": {
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_remodulin_reported",
        # 2002Q1 is $205 thousand of pre-approval supply - Remodulin was not
        # approved until 21 May 2002 - but United Therapeutics reports it as
        # Remodulin revenue, and the series mirrors the issuer. Leaving it out
        # is what made the 2002Q4 derivation disagree with gold for so long.
        "commercial_start_quarter": "2002Q1",
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "injection",
        "route_of_administration": "parenteral",
    },
    "Orenitram": {
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_orenitram_reported",
        "commercial_start_quarter": "2014Q2",
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "extended-release tablet",
        "route_of_administration": "oral",
    },
    "Adcirca": {
        "generic_name": "tadalafil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_adcirca_us_reported",
        "commercial_start_quarter": "2009Q3",
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "Uptravi": {
        "generic_name": "selexipag",
        "manufacturer": "Actelion/J&J",
        "benchmark_identity": "actelion_jnj_uptravi_worldwide_reported",
        "commercial_start_quarter": "2016Q1",
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "Yutrepia": {
        "generic_name": "treprostinil",
        "manufacturer": "Liquidia",
        "benchmark_identity": "liquidia_yutrepia_us_reported",
        "commercial_start_quarter": "2025Q2",
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "inhalation powder",
        "route_of_administration": "inhalation",
    },
    "Adempas": {
        "generic_name": "riociguat",
        "manufacturer": "Merck",
        "benchmark_identity": "merck_adempas_merck_territories_reported",
        # Adempas launched in 2013Q4, but this series deliberately does not
        # start there, for two reasons that are worth keeping separate.
        #
        # First, basis: until 2020Q1 Merck reported a single blended "Adempas"
        # figure that mixed its own territory sales with its profit share from
        # Bayer's territories. That blend is not a product-sales series, and it
        # is why Adempas was excluded from this catalog. From 2020Q1 Merck
        # splits the two, and the "Adempas" line is territory product sales.
        #
        # Second, provenance: Merck's 2020-2023 filings are not reachable here
        # as filings, only as redistributed copies, so those quarters cannot be
        # cited to the document that reports them. The series therefore starts
        # at 2024Q1, the first quarter each figure carries a citation to its own
        # filing. That boundary is about what can be evidenced, not about the
        # product, which is why this is not a launch-to-date uptake series and
        # never earns a peak.
        "commercial_start_quarter": "2024Q1",
        "launch_quarter": "2013Q4",
        "series_start_reason": (
            "Merck reported a blended territory-sales-plus-profit-share figure "
            "for Adempas until 2020Q1 and split them from that quarter on; "
            "2024Q1 is the earliest split quarter citable to its own filing "
            "here. Scope and format benchmark, not a launch-to-date series."
        ),
        "peak_eligible": False,
        "revenue_scope": "Merck marketing territories",
        "geography": "International",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "Opsumit": {
        "generic_name": "macitentan",
        "manufacturer": "Johnson & Johnson",
        # Deliberately not the annual entry's identity. ANNUAL_METADATA carries
        # "actelion_jnj_opsumit_worldwide_partial", which splices Actelion's CHF
        # years onto J&J's USD years to give context for a product older than
        # either series; this is the single-issuer, single-currency quarterly
        # series J&J actually reports, and the two must not be compared.
        "benchmark_identity": "jnj_opsumit_worldwide_reported",
        #
        # Both ends of this series are bounded, and for different reasons.
        #
        # Start: Opsumit launched in 2013Q4 under Actelion, which reported it in
        # CHF and only in part (see the excluded-products note for which
        # quarters are stated and which are not). J&J acquired Actelion on
        # 16 June 2017, so its first disclosure covers 16-30 June only - a
        # 45-million stub that its 2Q2018 exhibit states outright as the 2017
        # comparative. A 15-day stub is not a quarter, so the series starts at
        # 2017Q3, the first full quarter J&J owned and reported the product.
        #
        # End: from 2025Q1 J&J reports a combined "OPSUMIT / OPSYNVI" line and
        # restates FY2024 from 2,184 to 2,225 to match. Those later quarters are
        # a different product identity, and splitting the combined line back
        # apart would invent values, so the series stops at 2024Q4.
        "commercial_start_quarter": "2016Q1",
        "launch_quarter": "2013Q4",
        "series_start_reason": (
            "Opsumit launched in 2013Q4 under Actelion, whose own disclosures "
            "were in CHF and covered only scattered quarters. J&J republished "
            "Actelion's history in US dollars when it closed the acquisition, "
            "but that schedule reaches back only to 2016Q1, which is where this "
            "series starts. 2013Q4-2015Q4 has no US-dollar quarterly source, so "
            "uptake measured from here is not launch-to-date."
        ),
        "series_end_quarter": "2024Q4",
        "series_end_basis": "issuer_stopped_reporting",
        "series_end_reason": (
            "From 2025Q1 J&J reports a combined OPSUMIT / OPSYNVI line and "
            "restates FY2024 from 2,184 to 2,225 on that basis. Opsumit alone "
            "is no longer separately reported, and allocating the combined "
            "line would invent values."
        ),
        # The 2015-2017 middle of the product's life is not citable at all, so
        # 2024's 2,184 is the highest observed value on a still-rising curve
        # rather than a lifetime peak. Same shape as Adempas.
        "peak_eligible": False,
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "AmBisome": {
        "generic_name": "amphotericin B liposome for injection",
        "manufacturer": "Gilead",
        "benchmark_identity": "gilead_ambisome_worldwide_reported",
        "therapeutic_area": "Invasive fungal infection",
        "commercial_start_quarter": "2016Q1",
        "launch_quarter": "1997Q3",
        "series_start_reason": (
            "AmBisome has been on sale since 1997 and Gilead reports it "
            "throughout; 2016Q1 is the earliest quarter sourced here. Nearly "
            "twenty years after launch is a maturity plateau, not uptake - which "
            "is what makes it useful: it is the flattest series in the catalog."
        ),
        "series_end_quarter": "2019Q4",
        "series_end_basis": "sourcing_boundary",
        "series_end_reason": (
            "Gilead still reports AmBisome separately after this (FY2020 436, "
            "FY2021 540). The series stops where sourcing stopped."
        ),
        "peak_eligible": False,
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "liposomal injection",
        "route_of_administration": "intravenous",
    },
    "Harvoni": {
        "generic_name": "ledipasvir/sofosbuvir",
        "manufacturer": "Gilead",
        "benchmark_identity": "gilead_harvoni_worldwide_reported",
        "therapeutic_area": "Chronic hepatitis C",
        # The most violent curve in this dataset by a wide margin: 3,017 in one
        # quarter down to 232 twelve quarters later, because hepatitis C is
        # curative and the treatable population was being exhausted. Nothing in
        # the PAH catalog behaves remotely like this, which is the point of
        # having it.
        "commercial_start_quarter": "2016Q1",
        "launch_quarter": "2014Q4",
        "series_start_reason": (
            "Harvoni launched in 2014Q4 and had already passed its peak before "
            "this window opens; 2016Q1 is the earliest quarter sourced here. The "
            "series is the decline, not the rise."
        ),
        "series_end_quarter": "2018Q4",
        "series_end_basis": "issuer_stopped_reporting",
        "series_end_reason": (
            "From 2019Q1 Gilead renames the line Ledipasvir/Sofosbuvir and folds "
            "in the authorized generic sold by its own subsidiary Asegua. That "
            "line is a different quantity from Harvoni-the-brand, and the two "
            "cannot be separated from the published figures."
        ),
        "peak_eligible": False,
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "Ranexa": {
        "generic_name": "ranolazine",
        "manufacturer": "Gilead",
        "benchmark_identity": "gilead_ranexa_us_reported",
        "therapeutic_area": "Chronic angina",
        "commercial_start_quarter": "2016Q1",
        "launch_quarter": "2006Q1",
        "series_start_reason": (
            "Ranexa launched in 2006Q1; 2016Q1 is the earliest quarter sourced "
            "here. Ten years after launch, and two before the cliff."
        ),
        "series_end_quarter": "2019Q4",
        "series_end_basis": "sourcing_boundary",
        "series_end_reason": (
            "Gilead still reports Ranexa separately after this (FY2020 9, FY2021 "
            "10). The series stops where sourcing stopped, one year after "
            "generic entry took it from 177 a quarter to 11."
        ),
        "peak_eligible": False,
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "extended-release tablet",
        "route_of_administration": "oral",
    },
    "Letairis": {
        "generic_name": "ambrisentan",
        "manufacturer": "Gilead",
        # As with Opsumit and Tracleer, the annual entry keeps its own identity
        # and its own peak: it spans the product's whole life and knows where
        # the maximum is, while this is the four-year window sourced quarter by
        # quarter here.
        "benchmark_identity": "gilead_letairis_us_reported",
        "commercial_start_quarter": "2016Q1",
        "launch_quarter": "2007Q2",
        "series_start_reason": (
            "Letairis launched in 2007Q2. Gilead has reported it separately in "
            "its product sales summary throughout, but presented the figures in "
            "thousands before 2015 and in millions after; 2016Q1 is the earliest "
            "quarter sourced here on the current basis. Nine years after launch "
            "is not an uptake curve."
        ),
        "series_end_quarter": "2019Q4",
        # The first end in this catalog that is NOT the issuer changing what it
        # reports, and the difference matters enough to record in the data
        # rather than only in prose - see series_end_basis.
        "series_end_basis": "sourcing_boundary",
        "series_end_reason": (
            "Gilead still reports Letairis separately after this: FY2020 is 314 "
            "and FY2021 is 206. The series stops at 2019Q4 because that is what "
            "has been sourced quarter by quarter here, not because the "
            "disclosure changed. Anyone extending it will find the documents."
        ),
        # The window opens nine years after launch and contains the 2018 peak
        # only by luck; the annual series is the peak authority for this product.
        "peak_eligible": False,
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "Tracleer": {
        "generic_name": "bosentan",
        "manufacturer": "Actelion/J&J",
        # Distinct from the annual entry's identity, for the same reason as
        # Opsumit: the annual rows are Actelion's CHF history and carry the
        # product's real 2011 peak, while this is the US-dollar quarterly
        # window J&J's schedules cover.
        "benchmark_identity": "actelion_jnj_tracleer_worldwide_reported",
        "commercial_start_quarter": "2016Q1",
        "launch_quarter": "2001Q4",
        "series_start_reason": (
            "Tracleer launched in 2001Q4 and Actelion reported it in CHF. The "
            "US-dollar quarterly history J&J republished on acquisition reaches "
            "back only to 2016Q1, which is where this series starts - fifteen "
            "years after launch and four years after the product peaked. It is "
            "a decline-phase window, not an uptake curve."
        ),
        "series_end_quarter": "2019Q4",
        "series_end_basis": "issuer_stopped_reporting",
        "series_end_reason": (
            "From 2020Q1 J&J folds Tracleer into Other PAH and restates prior "
            "periods on that basis: the 1Q2020 schedule states that Other PAH "
            "is inclusive of TRACLEER, which was previously disclosed "
            "separately. The combined line cannot be split back apart."
        ),
        # The product peaked around 2011, long before this window opens. Its
        # peak lives on the annual CHF series; taking a maximum from a
        # declining tail would report less than a quarter of the truth.
        "peak_eligible": False,
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
    "Winrevair": {
        "generic_name": "sotatercept-csrk",
        "manufacturer": "Merck",
        "benchmark_identity": "merck_winrevair_worldwide_reported",
        # Approved March 26, 2024 (5 days before quarter end); Merck's own
        # prior-year comparison schedule discloses no separate Q1 2024
        # figure, only Q2-Q4 + FY (see merck_winrevair_quarterly.csv), so the
        # benchmarked series starts at the first quarter with real disclosed
        # data rather than inventing a Q1 value.
        "commercial_start_quarter": "2024Q2",
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "injection",
        "route_of_administration": "subcutaneous",
    },
}

ANNUAL_METADATA = {
    "Letairis": {
        "generic_name": "ambrisentan",
        "manufacturer": "Gilead",
        "benchmark_identity": "gilead_letairis_us_reported",
    },
    "Revatio": {
        "generic_name": "sildenafil",
        "manufacturer": "Pfizer",
        "benchmark_identity": "pfizer_revatio_worldwide_reported",
    },
    "Flolan": {
        "generic_name": "epoprostenol",
        "manufacturer": "GSK",
        "benchmark_identity": "gsk_flolan_worldwide_partial",
    },
    "Tracleer": {
        "generic_name": "bosentan",
        "manufacturer": "Actelion/J&J",
        "benchmark_identity": "actelion_tracleer_worldwide_reported_chf",
    },
    # Adempas is a quarterly benchmark product; these annual rows exist only
    # to carry the full-year totals its unstated fourth quarters derive from.
    "Adempas": {
        "generic_name": "riociguat",
        "manufacturer": "Merck",
        "benchmark_identity": "merck_adempas_merck_territories_annual",
    },
    # Opsumit, Veletri and Ventavis stay excluded from the quarterly benchmark
    # (no contiguous launch-to-end series is citable), but each carries annual
    # context rows - the same role Flolan already has.
    #
    # Opsumit's annual series deliberately spans two issuers and two currencies:
    # Actelion reported it in CHF from the 2013 launch, and J&J in USD from the
    # 16 June 2017 acquisition, which is why 2018 is J&J's first full year. The
    # 2015-2017 middle is not citable from here, so this is context and never a
    # peak benchmark - 2024 is a highest-observed value on a rising curve.
    "Opsumit": {
        "generic_name": "macitentan",
        "manufacturer": "Actelion/J&J",
        "benchmark_identity": "actelion_jnj_opsumit_worldwide_partial",
    },
    # Both of these already have complete quarterly series. Their annual rows
    # exist for one reason: an issuer that states a full year but leaves one
    # quarter implicit makes that quarter derivable, and without the annual
    # total in gold the derivation has nothing to work from. That is why they
    # carry series_role "derivation_input" rather than peak_benchmark or
    # partial_context - they are neither a benchmark nor a fragment.
    "Remodulin": {
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "benchmark_identity": "uthr_remodulin_reported",
    },
    "Winrevair": {
        "generic_name": "sotatercept-csrk",
        "manufacturer": "Merck",
        "benchmark_identity": "merck_winrevair_worldwide_reported",
    },
    "Veletri": {
        "generic_name": "epoprostenol",
        "manufacturer": "Actelion",
        "benchmark_identity": "actelion_veletri_worldwide_partial_chf",
    },
    "Ventavis": {
        "generic_name": "iloprost",
        "manufacturer": "Actelion",
        "benchmark_identity": "actelion_ventavis_us_partial_chf",
    },
}

# Annual-average exchange rates for the non-USD annual manifests (Tracleer in
# CHF, Flolan in GBP). Actelion and GSK never disclosed these figures in USD,
# so no citable USD quote exists to reuse; these rates convert the reported
# figure into a comparable value_normalized_usd_millions without altering the
# as-reported value_reported/currency fields, which still match source_quote.
#
# USD per 1 CHF, annual average of the New York noon buying rate for cable
# transfers, certified for customs purposes by the Federal Reserve Bank of
# New York. Sourced directly from UBS Group AG's own "Selected Financial
# Data" SEC filings (Form 20-F equivalents), which disclose this exact table
# every year specifically so USD readers can convert CHF figures - the same
# rate a Swiss issuer's own US filings would use. Cross-checked across seven
# overlapping UBS annual disclosures (Q4 2003 through Q4 2016 filings), all
# internally consistent.
FX_RATE_USD_PER_CHF: dict[int, float] = {
    2001: 0.5910, 2002: 0.6453, 2003: 0.7493, 2004: 0.8059, 2005: 0.8039,
    2006: 0.8034, 2007: 0.8381, 2008: 0.9298, 2009: 0.9260, 2010: 0.9670,
    2011: 1.1398, 2012: 1.0724, 2013: 1.0826, 2014: 1.0893, 2015: 1.0368,
    2016: 1.0128,
}

# USD per 1 GBP, annual average of daily noon buying rates. Source: Federal
# Reserve H.10/G.5A "Foreign Exchange Rates" annual releases. Covers Flolan's
# 2010-2013 reported span.
FX_RATE_USD_PER_GBP: dict[int, float] = {
    2010: 1.5458, 2011: 1.6043, 2012: 1.5853, 2013: 1.5642,
}

FX_RATE_SOURCE = "federal_reserve_ny_noon_buying_rate_annual_average"


def usd_normalized(value: float, currency: str, year: int) -> tuple[float | None, float | None]:
    """Return (value_normalized_usd_millions, fx_rate_to_usd) for a reported value.

    fx_rate_to_usd is None (and the row is already-USD) when currency == "USD".
    Returns (None, None) if no rate is available for the given currency/year,
    so a missing rate fails loud (via the caller) rather than silently
    reporting a false USD figure. Both rate tables are USD per 1 unit of the
    foreign currency, so converting is always value * rate.
    """
    if currency == "USD":
        return round(value, 6), None
    if currency == "CHF" and year in FX_RATE_USD_PER_CHF:
        rate = FX_RATE_USD_PER_CHF[year]
        return round(value * rate, 6), rate
    if currency == "GBP" and year in FX_RATE_USD_PER_GBP:
        rate = FX_RATE_USD_PER_GBP[year]
        return round(value * rate, 6), rate
    return None, None


def slug(*parts: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(str(part).lower() for part in parts)).strip("-")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(text + ("\n" if rows else ""))


def quarter_range(start: str, end: str) -> list[str]:
    year, quarter = int(start[:4]), int(start[-1])
    end_year, end_quarter = int(end[:4]), int(end[-1])
    out: list[str] = []
    while (year, quarter) <= (end_year, end_quarter):
        out.append(f"{year}Q{quarter}")
        quarter += 1
        if quarter == 5:
            year += 1
            quarter = 1
    return out


def normalize_label(value: str) -> str:
    value = re.sub(r"\(\d+\)", "", value)
    value = value.replace("®", "").replace("™", "")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def quote_contains_number(quote: str, value: float) -> bool:
    normalized = quote.replace(",", "")
    forms = {
        str(value),
        f"{value:g}",
        f"{value:.1f}",
        f"{value:.3f}",
    }
    return any(re.search(rf"(?<!\d){re.escape(form)}(?!\d)", normalized) for form in forms)


def first_amount(cells: list[str]) -> float | None:
    for cell in cells:
        match = re.fullmatch(r"\s*\$?\s*\(?\s*(\d[\d,]*(?:\.\d+)?)\s*\)?\s*", cell)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


class ResearchClient:
    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = cache_dir
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=60,
        )

    def close(self) -> None:
        self.client.close()

    def fetch(self, url: str) -> bytes:
        suffix = Path(url.split("?", 1)[0]).suffix or ".bin"
        path = self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"
        if path.is_file() and path.stat().st_size:
            return path.read_bytes()
        response = self.client.get(url)
        if response.status_code in {403, 429, 503}:
            time.sleep(1.5)
            response = self.client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)
        time.sleep(0.12)
        return response.content


def table_rows(html: bytes) -> list[tuple[str, list[str], str]]:
    soup = BeautifulSoup(html.decode("utf-8", errors="ignore"), "lxml")
    out: list[tuple[str, list[str], str]] = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all(["th", "td"])]
        if not cells:
            continue
        label = normalize_label(cells[0])
        out.append((label, cells, " | ".join(cell for cell in cells if cell)))
    return out


def find_direct_rows(html: bytes) -> dict[str, tuple[float, str]]:
    labels = {
        "tyvaso": "Tyvaso",
        "total tyvaso": "Total Tyvaso",
        "tyvaso dpi": "Tyvaso DPI",
        "nebulized tyvaso": "Nebulized Tyvaso",
        "remodulin": "Remodulin",
        "orenitram": "Orenitram",
        "adcirca": "Adcirca",
    }
    found: dict[str, tuple[float, str]] = {}
    for label, cells, quote in table_rows(html):
        product = labels.get(label)
        if not product or product in found:
            continue
        amount = first_amount(cells[1:])
        if amount is not None:
            found[product] = (amount, quote)
    return found


def revenue_row(
    *,
    drug_name: str,
    period: str,
    value: float,
    source_url: str,
    source_quote: str,
    source_type: str,
    derivation: str = "direct_reported",
    precision: str = "as_reported",
    source_value: float | None = None,
    source_unit: str = "millions",
    sources: list[dict[str, str]] | None = None,
    bridge_components: list[dict[str, Any]] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    meta = PRODUCT_METADATA[drug_name]
    year, quarter = int(period[:4]), int(period[-1])
    return {
        "gold_id": slug(meta["benchmark_identity"], period),
        "drug_name": drug_name,
        "generic_name": meta["generic_name"],
        "manufacturer": meta["manufacturer"],
        "benchmark_identity": meta["benchmark_identity"],
        "period": period,
        "fiscal_year": year,
        "fiscal_quarter": quarter,
        "calendar_year": year,
        "calendar_quarter": quarter,
        "value_reported": round(float(value), 6),
        "value_normalized_usd_millions": round(float(value), 6),
        "currency": "USD",
        "unit": "millions",
        "metric": "revenue",
        "period_type": "quarterly",
        "period_basis": "calendar",
        "revenue_scope": meta["revenue_scope"],
        "geography": meta["geography"],
        "formulation": meta["formulation"],
        "route_of_administration": meta["route_of_administration"],
        "source_type": source_type,
        "source_url": source_url,
        "source_quote": source_quote,
        "source_value_reported": source_value if source_value is not None else value,
        "source_unit": source_unit,
        "sources": sources or [{"source_url": source_url, "source_quote": source_quote}],
        # Only a quarter split by an ownership change carries these: the dated
        # parts that tile it, so a reader (and the pipeline) can check the sum
        # instead of trusting it.
        **({"bridge_components": bridge_components} if bridge_components else {}),
        "derivation": derivation,
        "precision": precision,
        "extraction_method": PROVENANCE,
        "confidence_score": 1.0 if precision != "approximate" else 0.9,
        "validation_status": "confirmed",
        "gold_notes": notes,
    }


def parse_uthr_formulation_history(html: bytes, source_url: str) -> list[dict[str, Any]]:
    periods = ["2022Q1", "2022Q2", "2022Q3", "2022Q4", "2023Q1", "2023Q2", "2023Q3"]
    rows: list[dict[str, Any]] = []
    for label, cells, quote in table_rows(html):
        if len(cells) < 20 or label not in {"tyvaso dpi", "nebulized tyvaso"}:
            continue
        values = [amount for cell in cells[1:] if (amount := first_amount([cell])) is not None]
        drug_name = "Tyvaso DPI" if label == "tyvaso dpi" else "Nebulized Tyvaso"
        value_periods = periods[1:] if drug_name == "Tyvaso DPI" else periods
        for period, value in zip(value_periods, values, strict=True):
            rows.append(
                revenue_row(
                    drug_name=drug_name,
                    period=period,
                    value=value,
                    source_url=source_url,
                    source_quote=quote,
                    source_type="sec_filing",
                    derivation="direct_retrospective_table",
                    notes="Issuer retrospective formulation table published in 2023Q3.",
                )
            )
    return rows


def build_uthr(client: ResearchClient) -> list[dict[str, Any]]:
    manifest = read_csv(SOURCE_DIR / "uthr_quarterly_exhibits.csv")
    rows: list[dict[str, Any]] = []
    legacy_tyvaso: dict[str, dict[str, Any]] = {}
    retrospective_html: bytes | None = None
    retrospective_url = ""
    for source in manifest:
        period, url = source["period"], source["source_url"]
        html = client.fetch(url)
        direct = find_direct_rows(html)
        if period == "2023Q3":
            retrospective_html, retrospective_url = html, url

        tyvaso_label = "Total Tyvaso" if "Total Tyvaso" in direct else "Tyvaso"
        expected = ["Remodulin", "Adcirca", tyvaso_label]
        if period >= "2014Q2":
            expected.append("Orenitram")
        missing = [name for name in expected if name not in direct]
        if missing:
            raise ValueError(f"{period} missing UTHR rows: {missing}")

        for source_label, drug_name in (
            (tyvaso_label, "Tyvaso"),
            ("Remodulin", "Remodulin"),
            ("Adcirca", "Adcirca"),
            ("Orenitram", "Orenitram"),
            ("Tyvaso DPI", "Tyvaso DPI"),
            ("Nebulized Tyvaso", "Nebulized Tyvaso"),
        ):
            if source_label not in direct:
                continue
            raw_value, quote = direct[source_label]
            # UTHR's exhibit tables switch from whole-dollar-thousands (e.g.
            # "121,718") to one-decimal millions (e.g. "102.2") partway
            # through 2016, not cleanly at the 2017Q1 boundary a date cutoff
            # would assume. Infer the unit from the raw magnitude instead: no
            # thousands-formatted quarterly figure in this series is ever
            # below ~1,500 (a $1.5M+ quarter), and no millions-formatted one
            # is ever above ~500 (no single UTHR product line has cleared
            # $500M in a quarter), so 1,000 cleanly separates every observed
            # value with wide margin on both sides.
            source_unit = "thousands" if raw_value >= 1000 else "millions"
            value = raw_value / 1000 if source_unit == "thousands" else raw_value
            row = revenue_row(
                drug_name=drug_name,
                period=period,
                value=value,
                source_url=url,
                source_quote=quote,
                source_type="sec_filing",
                source_value=raw_value,
                source_unit=source_unit,
                notes=f"Issuer row label: {source_label}.",
            )
            rows.append(row)
            if drug_name == "Tyvaso" and period < "2022Q2":
                legacy_tyvaso[period] = row

    if retrospective_html is None:
        raise ValueError("Missing UTHR 2023Q3 retrospective formulation source")
    rows.extend(parse_uthr_formulation_history(retrospective_html, retrospective_url))
    for period, parent in legacy_tyvaso.items():
        rows.append(
            revenue_row(
                drug_name="Nebulized Tyvaso",
                period=period,
                value=parent["value_reported"],
                source_url=parent["source_url"],
                source_quote=parent["source_quote"],
                source_type="sec_filing",
                source_value=parent["source_value_reported"],
                source_unit=parent["source_unit"],
                derivation="identity_normalization_pre_dpi",
                notes="Before DPI launch, the issuer's Tyvaso row necessarily represented nebulized Tyvaso.",
            )
        )

    return deduplicate(rows + build_remodulin_early())


def build_remodulin_early() -> list[dict[str, Any]]:
    """Remodulin's 2002-2008 history, which comes from a manifest, not the wire.

    Split out of ``build_uthr`` so that editing the manifest is enough to
    rebuild these rows. Left inside it, a corrected figure needed a full
    network rebuild to take effect, which is how a stale value survives an
    edit that looks like it landed.
    """
    return [
        revenue_row(
            drug_name="Remodulin",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            source_type="sec_filing",
            derivation=source["derivation"],
            precision=source["precision"],
            # 2002Q1 is stated as "$205,000" - the currency base unit, not millions - so
            # the row carries the as-reported value and its unit; every other
            # row here is quoted in millions and leaves both blank.
            source_value=(
                float(source["source_value_reported"])
                if source.get("source_value_reported")
                else None
            ),
            source_unit=source.get("source_unit") or "millions",
            notes="Early issuer history researched independently from filed reports.",
        )
        for source in read_csv(SOURCE_DIR / "uthr_remodulin_early.csv")
    ]


def pdf_text(raw: bytes) -> str:
    with pdfplumber.open(BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def uptravi_ww(text: str) -> tuple[float, str]:
    match = re.search(r"UPTRAVI[^\n]*\nUS[^\n]*\nIntl[^\n]*\nWW\s+([\d,]+)[^\n]*", text, re.IGNORECASE)
    if not match:
        raise ValueError("UPTRAVI WW row not found")
    return float(match.group(1).replace(",", "")), re.sub(r"\s+", " ", match.group(0))


def uptravi_bridge_components(pre_close: float, post_close: float) -> list[dict[str, Any]]:
    """The registered parts, checked against what this run actually read."""
    registered = ACQUISITION_BRIDGES[("Uptravi", "2017Q2")]["bridge_components"]
    read = [pre_close, post_close]
    for component, value in zip(registered, read):
        if abs(component["value"] - value) > 1e-6:
            raise ValueError(
                f"Uptravi 2017Q2 bridge component for {component['covers']} reads "
                f"{value:g} but is registered as {component['value']:g}; one of the "
                "two documents changed and the bridge must be re-checked."
            )
    return [dict(component) for component in registered]


def build_uptravi(client: ResearchClient) -> list[dict[str, Any]]:
    historical_url = (
        "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q2/"
        "Actelion_Historical_Sales_Schedule.pdf"
    )
    historical_text = pdf_text(client.fetch(historical_url))
    match = re.search(
        r"UPTRAVI\s*\nUS[^\n]*\nIntl[^\n]*\nWW\s+([\d,]+)\s+([\d,]+)\s+"
        r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)",
        historical_text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Historical Uptravi WW row not found")
    values = [float(value.replace(",", "")) for value in match.groups()]
    quote = re.sub(r"\s+", " ", match.group(0))
    mapping = {
        "2016Q1": values[5],
        "2016Q2": values[4],
        "2016Q3": values[3],
        "2016Q4": values[2],
        "2017Q1": values[1],
    }
    rows = [
        revenue_row(
            drug_name="Uptravi",
            period=period,
            value=value,
            source_url=historical_url,
            source_quote=quote,
            source_type="company_ir",
            derivation="direct_jnj_retrospective_table",
            notes="J&J converted pre-acquisition Actelion sales to USD.",
        )
        for period, value in mapping.items()
    ]

    manifest = read_csv(SOURCE_DIR / "jnj_uptravi_quarterly.csv")
    q3_source = manifest[0]
    q3_text = pdf_text(client.fetch(q3_source["source_url"]))
    q3_value, q3_quote = uptravi_ww(q3_text)
    ytd_match = re.search(
        r"UPTRAVI[^\n]*\nUS[^\n]*\nIntl[^\n]*\nWW\s+[\d,]+\s+-\s+\*\s+\*\s+-\s+([\d,]+)",
        q3_text,
        re.IGNORECASE,
    )
    if not ytd_match:
        raise ValueError("2017Q3 Uptravi YTD row not found")
    post_close_stub = float(ytd_match.group(1).replace(",", "")) - q3_value
    pre_close_q2 = values[0]
    bridge_value = pre_close_q2 + post_close_stub
    bridge_quote = (
        f"{quote}; {q3_quote}; acquisition bridge: {pre_close_q2:g} pre-close + "
        f"{post_close_stub:g} post-close = {bridge_value:g} USD million."
    )
    rows.append(
        revenue_row(
            drug_name="Uptravi",
            period="2017Q2",
            value=bridge_value,
            source_url=historical_url,
            source_quote=bridge_quote,
            source_type="company_ir",
            derivation="acquisition_bridge_sum",
            sources=[
                {"source_url": historical_url, "source_quote": quote},
                {"source_url": q3_source["source_url"], "source_quote": q3_quote},
            ],
            bridge_components=uptravi_bridge_components(pre_close_q2, post_close_stub),
            notes=(
                "Combines Actelion sales through June 15 with J&J's June 16-July 2 "
                "fiscal stub. The parts are contiguous and do not overlap, but J&J's "
                "fiscal Q2 2017 ended July 2, so the assembled figure covers two days "
                "more than calendar Q2 and cannot be made exact."
            ),
        )
    )

    for source in manifest:
        text = pdf_text(client.fetch(source["source_url"]))
        value, source_quote = uptravi_ww(text)
        rows.append(
            revenue_row(
                drug_name="Uptravi",
                period=source["period"],
                value=value,
                source_url=source["source_url"],
                source_quote=source_quote,
                source_type="company_ir",
                notes="J&J worldwide supplementary product sales.",
            )
        )
    return deduplicate(rows)


def build_yutrepia() -> list[dict[str, Any]]:
    return [
        revenue_row(
            drug_name="Yutrepia",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            source_type="sec_filing",
            derivation=source["derivation"],
            source_value=float(source["source_value_reported"]),
            source_unit=source["source_unit"],
            notes="Liquidia product sales exclude separately reported service revenue.",
        )
        for source in read_csv(SOURCE_DIR / "yutrepia_quarterly.csv")
    ]


def build_winrevair() -> list[dict[str, Any]]:
    return [
        revenue_row(
            drug_name="Winrevair",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            # Quarters Merck states outright in a 10-Q cite the filing; the rest
            # still come from its IR schedule, so the type follows the URL
            # rather than assuming every row shares one provenance.
            source_type="sec_filing" if "sec.gov" in source["source_url"] else "company_ir",
            derivation=source["derivation"],
            notes="Merck worldwide product sales; alliance revenue is not used.",
        )
        for source in read_csv(SOURCE_DIR / "merck_winrevair_quarterly.csv")
    ]


def build_adempas() -> list[dict[str, Any]]:
    """Adempas as Merck reports it in its own marketing territories.

    Not a worldwide series and not comparable to one: Bayer commercialises
    Adempas in the Americas and Merck records only its share of those profits,
    as alliance revenue, on a separate line. The line read here is product
    sales in Merck's territories, which is a real reported quantity with a real
    scope - and the scope is the point, since nothing else in this catalog
    exercises a territory-split product.
    """
    return [
        revenue_row(
            drug_name="Adempas",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            source_type="sec_filing" if "sec.gov" in source["source_url"] else "company_ir",
            derivation=source["derivation"],
            notes=(
                "Merck-territory product sales only; alliance revenue from "
                "Bayer's territories is a separate line and is not included."
            ),
        )
        for source in read_csv(SOURCE_DIR / "merck_adempas_quarterly.csv")
    ]


# 2017Q2 is the one Opsumit quarter no single issuer reports. Actelion's last
# schedule stops at the 16 June 2017 closing date and J&J's first one starts
# there, so the quarter exists only as 216 + 45. The parts are dated so the
# composition can be checked rather than trusted - see
# ``assemble_split_ownership_quarter``.
ACQUISITION_BRIDGES = {
    ("Opsumit", "2017Q2"): {
    "bridge_components": [
        {
            "covers": "2017-04-01/2017-06-15",
            "value": 216.0,
            "issuer": "Actelion",
            "source_url": (
                "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q2/"
                "Actelion_Historical_Sales_Schedule.pdf"
            ),
        },
        {
            "covers": "2017-06-16/2017-07-02",
            "value": 45.0,
            "issuer": "Johnson & Johnson",
            "source_url": (
                "https://s203.q4cdn.com/636242992/files/doc_financials/2018/q2/"
                "Sales_of_Key_Products_Franchises_2Q2018.pdf"
            ),
        },
    ],
    "sources": [
        {
            "source_url": (
                "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q2/"
                "Actelion_Historical_Sales_Schedule.pdf"
            ),
            "source_quote": (
                "OPSUMIT US 130 144 143 137 130 121 531 Intl 86 100 92 86 77 58 "
                "313 WW 216 244 235 223 207 179 844 (Q2 column is through 6/15)"
            ),
        },
        {
            "source_url": (
                "https://s203.q4cdn.com/636242992/files/doc_financials/2018/q2/"
                "Sales_of_Key_Products_Franchises_2Q2018.pdf"
            ),
            "source_quote": (
                "OPSUMIT US 180 24 * * - 329 24 * * - Intl 131 21 253 21 "
                "WW 311 45 * * * 582 45 * * *"
            ),
        },
    ],
    },
    # Uptravi's 2017Q2 is the same quarter and the same two documents. It is
    # registered here rather than left to build_uptravi alone so that the row
    # can be rebuilt from the manifest without refetching the PDFs; the network
    # path still recomputes the values and asserts they agree.
    ("Tracleer", "2017Q2"): {
        "bridge_components": [
            {
                "covers": "2017-04-01/2017-06-15",
                "value": 198.0,
                "issuer": "Actelion",
                "source_url": (
                    "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q2/"
                    "Actelion_Historical_Sales_Schedule.pdf"
                ),
            },
            {
                "covers": "2017-06-16/2017-07-02",
                "value": 26.0,
                "issuer": "Johnson & Johnson",
                "source_url": (
                    "https://s203.q4cdn.com/636242992/files/doc_financials/2018/q2/"
                    "Sales_of_Key_Products_Franchises_2Q2018.pdf"
                ),
            },
        ],
    },
    ("Uptravi", "2017Q2"): {
        "bridge_components": [
            {
                "covers": "2017-04-01/2017-06-15",
                "value": 110.0,
                "issuer": "Actelion",
                "source_url": (
                    "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q2/"
                    "Actelion_Historical_Sales_Schedule.pdf"
                ),
            },
            {
                "covers": "2017-06-16/2017-07-02",
                "value": 9.0,
                "issuer": "Johnson & Johnson",
                "source_url": (
                    "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q3/"
                    "Sales_of_Key_Products_Franchises_3Q2017.pdf"
                ),
            },
        ],
    },
}


def apply_acquisition_bridges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the dated parts to every bridged quarter, wherever it came from.

    A bridge row built on a previous run and reused here has the value but not
    the parts, and without the parts nothing can check the value. Filling them
    in from the registry keeps the reuse path and the network path producing
    the same row.
    """
    for row in rows:
        bridge = ACQUISITION_BRIDGES.get((row["drug_name"], row["period"]))
        if bridge and "bridge_components" not in row:
            row["bridge_components"] = bridge["bridge_components"]
    return rows


def build_opsumit() -> list[dict[str, Any]]:
    """J&J-era Opsumit worldwide, 2017Q3 through 2024Q4.

    Every quarter is the OPSUMIT "WW" row of the Sales of Key Products /
    Franchises schedule J&J publishes with each earnings release, taken from
    that quarter's own schedule wherever the document could be read - the same
    rule Yutrepia is held to, because a later filing carries the quarter in its
    prior-year column and reading the wrong column of a two-year table is the
    exact failure this dataset exists to catch. Two quarters (2020Q2, 2020Q3)
    are the prior-year column of the 2021 schedule instead, marked as such in
    the manifest; the row there is labelled WW under a "2021 2020" header, so
    there is no column to misread.

    The check that matters is arithmetic, not provenance: J&J states a
    full-year worldwide total in each Q4 schedule, and all seven full years in
    this series sum to it exactly (2018 1,215; 2019 1,327; 2020 1,639; 2021
    1,819; 2022 1,783; 2023 1,973; 2024 2,184). ``test_gold_dataset`` pins that
    reconciliation so a mis-keyed quarter cannot pass silently.

    Worldwide is read as reported and never summed from US + Intl. J&J rounds
    each line independently, so in 2019Q1, 2021Q2 and 2024Q1 the two parts
    differ from the stated worldwide figure by 1.
    """
    return [
        revenue_row(
            drug_name="Opsumit",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            # The schedule is an IR document. It is also filed as 8-K Exhibit
            # 99.2, but this cites the copy actually read, not the twin.
            source_type="company_ir",
            derivation=source["derivation"],
            # The US and International figures of the same block travel with the
            # row rather than inside the quote: the quote has to stay a clean
            # table row so the extraction eval tests column alignment on it,
            # but the corroborating lines are what make a mis-keyed quarter
            # obvious to a reader.
            notes=(
                "Worldwide net trade sales as reported; not summed from the US "
                "and International lines, which round independently. "
                + source["context"]
            ),
            **(
                ACQUISITION_BRIDGES[("Opsumit", source["period"])]
                if source["derivation"] == "acquisition_bridge_sum"
                else {}
            ),
        )
        for source in read_csv(SOURCE_DIR / "jnj_opsumit_quarterly.csv")
    ]


def build_tracleer() -> list[dict[str, Any]]:
    """Tracleer worldwide, 2016Q1 through 2019Q4, across two issuers.

    Sixteen quarters in one currency from two companies: Actelion's own history
    as J&J republished it in US dollars, then J&J's quarterly schedules. It
    matters to this dataset for a reason the numbers do not show - it is the
    only series here observed entirely in decline, and the only one whose peak
    lives in a different series, in a different currency. A benchmark made only
    of rising curves would never catch a reader that assumes the last value is
    the biggest.
    """
    return [
        revenue_row(
            drug_name="Tracleer",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            source_type="company_ir",
            derivation=source["derivation"],
            notes=(
                "Worldwide net trade sales as reported; not summed from the US "
                "and International lines, which round independently. "
                + source["context"]
            ),
            **(
                ACQUISITION_BRIDGES[("Tracleer", source["period"])]
                if source["derivation"] == "acquisition_bridge_sum"
                else {}
            ),
        )
        for source in read_csv(SOURCE_DIR / "jnj_tracleer_quarterly.csv")
    ]


def build_letairis() -> list[dict[str, Any]]:
    """Letairis US, 2016Q1 through 2019Q4 - and the reason it is here at all.

    Letairis was excluded from this catalog on the finding that Gilead reports
    it only inside an aggregate. That is true of the narrative section of every
    release, which folds it into a sentence about "Other product sales". It is
    not true of the PRODUCT SALES SUMMARY table in the same document, which
    states the line on its own. The exclusion had read the prose and stopped.

    Gilead is the sixth issuer in this dataset and the only one outside the
    J&J / United Therapeutics / Merck group, which is the point: a benchmark
    drawn from one company's disclosure habits mostly measures how well the
    pipeline reads that company.
    """
    return [
        revenue_row(
            drug_name="Letairis",
            period=source["period"],
            value=float(source["value_reported"]),
            source_url=source["source_url"],
            source_quote=source["source_quote"],
            source_type="company_ir",
            derivation=source["derivation"],
            notes=source["context"],
        )
        for source in read_csv(SOURCE_DIR / "gilead_letairis_quarterly.csv")
    ]


# Gilead reports these on the same schedule as Letairis, in the same table, so
# one builder serves all of them. They are here for a reason unrelated to
# pulmonary hypertension: a benchmark drawn from one therapy area measures how
# well the pipeline reads that area's disclosure habits. Hepatitis C collapses,
# an antifungal plateaus for two decades, an angina drug falls off a patent
# cliff in two quarters - none of which any PAH series does.
GILEAD_COMPARATORS = {
    "Ranexa": "gilead_ranexa_quarterly.csv",
    "AmBisome": "gilead_ambisome_quarterly.csv",
    "Harvoni": "gilead_harvoni_quarterly.csv",
}


def build_gilead_comparators() -> list[dict[str, Any]]:
    """Gilead products outside pulmonary hypertension, 2016-2019."""
    rows: list[dict[str, Any]] = []
    for drug_name, manifest in GILEAD_COMPARATORS.items():
        rows.extend(
            revenue_row(
                drug_name=drug_name,
                period=source["period"],
                value=float(source["value_reported"]),
                source_url=source["source_url"],
                source_quote=source["source_quote"],
                source_type="company_ir",
                derivation=source["derivation"],
                notes=source["context"],
            )
            for source in read_csv(SOURCE_DIR / manifest)
        )
    return rows


def build_annual_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in read_csv(SOURCE_DIR / "annual_product_sales.csv"):
        meta = ANNUAL_METADATA[source["drug_name"]]
        value = float(source["value_reported"])
        source_unit = "thousands" if "thousands" in source["source_quote"].lower() else source["unit"]
        source_value = value * 1000 if source_unit == "thousands" else value
        normalized_usd, fx_rate = usd_normalized(value, source["currency"], int(source["period"]))
        if normalized_usd is None:
            raise ValueError(
                f"No FX rate for {source['drug_name']} {source['period']} ({source['currency']}); "
                "add one to FX_RATE_CHF_PER_USD/FX_RATE_USD_PER_GBP before building gold."
            )
        rows.append(
            {
                "gold_id": slug(meta["benchmark_identity"], source["period"]),
                "drug_name": source["drug_name"],
                "generic_name": meta["generic_name"],
                "manufacturer": meta["manufacturer"],
                "benchmark_identity": meta["benchmark_identity"],
                "period": source["period"],
                "value_reported": value,
                "currency": source["currency"],
                "unit": source["unit"],
                "value_normalized_usd_millions": normalized_usd,
                "fx_rate_to_usd": fx_rate,
                "fx_rate_source": FX_RATE_SOURCE if fx_rate is not None else None,
                "metric": "revenue",
                "period_type": "annual",
                "period_basis": "calendar",
                "revenue_scope": source["revenue_scope"],
                "geography": source["geography"],
                "source_type": "sec_filing" if "sec.gov" in source["source_url"] else "company_ir",
                "source_url": source["source_url"],
                "source_quote": source["source_quote"],
                "source_value_reported": source_value,
                "source_unit": source_unit,
                "derivation": source["derivation"],
                "series_role": source["series_role"],
                "extraction_method": PROVENANCE,
                "confidence_score": 1.0,
                "validation_status": "confirmed",
            }
        )
    return rows


def deduplicate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    rank = {
        "direct_reported": 4,
        "direct_retrospective_table": 3,
        "direct_jnj_retrospective_table": 3,
        "identity_normalization_pre_dpi": 2,
    }
    for row in rows:
        key = (row["drug_name"], row["period"])
        previous = best.get(key)
        if previous is None or rank.get(row["derivation"], 1) > rank.get(previous["derivation"], 1):
            best[key] = row
    return sorted(best.values(), key=lambda row: (row["drug_name"], row["period"]))


def full_annual_totals(rows: list[dict[str, Any]], drug_name: str) -> list[dict[str, Any]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["drug_name"] == drug_name:
            by_year[row["calendar_year"]].append(row)
    totals: list[dict[str, Any]] = []
    for year, year_rows in sorted(by_year.items()):
        if {row["calendar_quarter"] for row in year_rows} != {1, 2, 3, 4}:
            continue
        totals.append(
            {
                "period": str(year),
                "value_reported": round(sum(row["value_reported"] for row in year_rows), 6),
                "currency": "USD",
                "unit": "millions",
                "input_ids": [row["gold_id"] for row in sorted(year_rows, key=lambda row: row["period"])],
            }
        )
    return totals


def observed_peak(drug_name: str, annual: list[dict[str, Any]], scope: str, geography: str) -> dict[str, Any]:
    maximum = max(annual, key=lambda row: row["value_reported"])
    later = [row for row in annual if row["period"] > maximum["period"]]
    observed = len(later) >= 2 and all(row["value_reported"] < maximum["value_reported"] for row in later)
    return {
        "gold_id": slug(drug_name, "peak"),
        "drug_name": drug_name,
        "peak_status": "observed" if observed else "not_yet_observed",
        "peak_year": int(maximum["period"]) if observed else None,
        "peak_value": maximum["value_reported"] if observed else None,
        "currency": maximum["currency"] if observed else None,
        "unit": maximum["unit"] if observed else None,
        "revenue_scope": scope,
        "geography": geography,
        "highest_observed_year": int(maximum["period"]),
        "highest_observed_value": maximum["value_reported"],
        "annual_observations": len(annual),
        "post_peak_years": len(later) if observed else 0,
        "selection_method": "independent_max_with_two_later_lower_years",
        "input_ids": maximum["input_ids"],
        "benchmark_eligible": True,
        "numeric_peak_available": observed,
    }


def build_peaks(quarterly: list[dict[str, Any]], annual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peaks: list[dict[str, Any]] = []
    for drug_name, meta in PRODUCT_METADATA.items():
        if not meta.get("peak_eligible", True):
            # A series that starts after launch cannot say where the peak is:
            # its maximum is only the highest value inside the window it
            # happens to cover. Adempas is the case - see its metadata.
            continue
        totals = full_annual_totals(quarterly, drug_name)
        if not totals:
            observations = [row for row in quarterly if row["drug_name"] == drug_name]
            highest = max(observations, key=lambda row: row["value_reported"])
            peaks.append(
                {
                    "gold_id": slug(drug_name, "peak"),
                    "drug_name": drug_name,
                    "peak_status": "not_yet_observed",
                    "peak_year": None,
                    "peak_value": None,
                    "currency": None,
                    "unit": None,
                    "revenue_scope": meta["revenue_scope"],
                    "geography": meta["geography"],
                    "highest_observed_period": highest["period"],
                    "highest_observed_value": highest["value_reported"],
                    "annual_observations": 0,
                    "post_peak_years": 0,
                    "selection_method": "insufficient_complete_years_product_still_growing",
                    "input_ids": [highest["gold_id"]],
                    "benchmark_eligible": True,
                    "numeric_peak_available": False,
                }
            )
            continue
        peaks.append(observed_peak(drug_name, totals, meta["revenue_scope"], meta["geography"]))

    for drug_name in ("Letairis", "Revatio", "Tracleer"):
        # Peak selection compares value_normalized_usd_millions, not the raw
        # as-reported currency: Tracleer is CHF-denominated, and a strong-franc
        # year can outrank a nominally larger CHF year once converted (e.g.
        # 2011's franc surge). Comparing raw CHF/GBP/USD figures side by side
        # would silently pick the wrong peak year and isn't comparable to the
        # USD peaks reported for every other product in this file.
        series = [
            {
                "period": row["period"],
                "value_reported": row["value_normalized_usd_millions"],
                "currency": "USD",
                "unit": row["unit"],
                "input_ids": [row["gold_id"]],
            }
            for row in annual
            if row["drug_name"] == drug_name and row["series_role"] == "peak_benchmark"
        ]
        exemplar = next(row for row in annual if row["drug_name"] == drug_name)
        peaks.append(observed_peak(drug_name, series, exemplar["revenue_scope"], exemplar["geography"]))
    return sorted(peaks, key=lambda row: row["drug_name"])


def build_exclusions() -> list[dict[str, Any]]:
    return [
        {
            "gold_id": slug(source["drug_name"], "excluded"),
            "drug_name": source["drug_name"],
            "benchmark_status": "excluded",
            "reason_code": source["reason_code"],
            "source_url": source["source_url"],
            "source_quote": source["source_quote"],
            "details": source["details"],
            "extraction_method": PROVENANCE,
        }
        for source in read_csv(SOURCE_DIR / "excluded_products.csv")
    ]


def series_end_quarter(meta: dict[str, Any]) -> str:
    """The last quarter a product's series is expected to cover.

    Defaults to the dataset's as-of quarter. A product whose issuer stopped
    reporting it separately ends earlier, at a quarter named in its metadata
    together with the reason - see ``series_end_reason``.
    """
    return meta.get("series_end_quarter") or AS_OF_QUARTER


def coverage_rows(quarterly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coverage for every quarterly series, against its own expected span.

    A series is complete when it covers commercial start through its end
    quarter, which is normally the as-of quarter but is earlier for a product
    whose reporting basis changed. Requiring every series to run to the as-of
    quarter is what forced products into total exclusion over a late change:
    Opsumit is reportable from 2013 to 2024Q4 and was dropped entirely because
    J&J merged it into a combined line in 2025.
    """
    rows: list[dict[str, Any]] = []
    by_drug: dict[str, set[str]] = defaultdict(set)
    for row in quarterly:
        by_drug[row["drug_name"]].add(row["period"])
    for drug_name, meta in PRODUCT_METADATA.items():
        end_quarter = series_end_quarter(meta)
        expected = quarter_range(meta["commercial_start_quarter"], end_quarter)
        observed = by_drug[drug_name]
        missing = [period for period in expected if period not in observed]
        # Values after a bounded series ends are not part of its span, and
        # would silently extend a series past the point its basis changed.
        beyond = sorted(period for period in observed if period > end_quarter)
        row = {
            "drug_name": drug_name,
            "benchmark_identity": meta["benchmark_identity"],
            "commercial_start_quarter": meta["commercial_start_quarter"],
            "series_end_quarter": end_quarter,
            "as_of_quarter": AS_OF_QUARTER,
            "expected_quarters": len(expected),
            "observed_quarters": len(observed & set(expected)),
            "coverage_pct": round(100 * len(observed & set(expected)) / len(expected), 1),
            "missing_quarters": missing,
            "quarters_beyond_series_end": beyond,
            "benchmark_eligible": not missing and not beyond,
        }
        if end_quarter != AS_OF_QUARTER:
            reason = meta.get("series_end_reason")
            if not reason:
                raise ValueError(
                    f"{drug_name} ends at {end_quarter} before the as-of quarter "
                    "but states no series_end_reason; a short series must say why."
                )
            row["series_end_reason"] = reason
            # Why it ends, in a field a consumer can branch on. An end because
            # the issuer stopped publishing the line is a fact about the world;
            # an end because sourcing stopped is a fact about this dataset, and
            # only the second one is closable by more work.
            row["series_end_basis"] = meta.get(
                "series_end_basis", "issuer_stopped_reporting"
            )

        # The same rule at the other end. A series that starts after the
        # product went on sale is measuring part of its life, and a reader has
        # to be told which part and why - otherwise the start looks like the
        # launch and every rate computed from it is wrong.
        launch = meta.get("launch_quarter")
        if launch and launch != meta["commercial_start_quarter"]:
            start_reason = meta.get("series_start_reason")
            if not start_reason:
                raise ValueError(
                    f"{drug_name} starts at {meta['commercial_start_quarter']} but "
                    f"launched at {launch} and states no series_start_reason; a "
                    "series that begins after launch must say why."
                )
            row["launch_quarter"] = launch
            row["series_start_reason"] = start_reason
        rows.append(row)
    return rows


def catalog_coverage(
    coverage: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Completeness across the whole seed catalog, not just what is included.

    Coverage measured only over included products always reads 100% and hides
    the real question, which is how much of the catalog the dataset speaks to
    at all. Reporting the excluded products alongside keeps that visible.
    """
    quarterly_products = sorted(row["drug_name"] for row in coverage)
    excluded = sorted(row["drug_name"] for row in exclusions)
    # An excluded product may still appear in ANNUAL_METADATA to supply context
    # rows - Flolan does - so it is not an annual-only benchmark. Counting it as
    # both would overstate the catalog.
    annual_only = sorted(
        ANNUAL_METADATA.keys() - {row["drug_name"] for row in coverage} - set(excluded)
    )
    # Comparators from other therapy areas are in the dataset but not in the
    # catalog, and mixing them in would flatter the coverage percentage: three
    # products added from outside would read as three more of the twenty
    # covered. The catalog is the seed file, and the percentage is measured
    # against it.
    seed = seed_catalog()
    comparators = sorted(
        (set(quarterly_products) | set(annual_only)) - set(excluded) - seed
    )
    in_catalog = [drug for drug in quarterly_products if drug in seed]
    total = len(seed)
    return {
        "catalog_products": total,
        "quarterly_series_products": quarterly_products,
        "comparator_products": comparators,
        "annual_only_products": annual_only,
        "excluded_products": excluded,
        "quarterly_series_pct": round(100 * len(in_catalog) / total, 1),
        "quarterly_observations": sum(row["observed_quarters"] for row in coverage),
        "bounded_series": sorted(
            row["drug_name"] for row in coverage if "series_end_reason" in row
        ),
    }


def gold_completeness(
    coverage: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
    annual: list[dict[str, Any]],
) -> dict[str, Any]:
    """Whether the dataset is complete *as a dataset*, stated explicitly.

    This is not the same question as how much of it the pipeline can read back,
    and conflating the two is easy: ``scripts/eval_completeness.py`` reports a
    delivery rate against this dataset, and a shortfall there is a gap in the
    pipeline, not a hole in the oracle.

    A gold dataset is complete when every product in the seed catalog is
    accounted for - either a quarterly series covering its whole commercial
    span, an annual benchmark, or an exclusion carrying evidence for why no
    series exists - and when no included series is missing a quarter. The
    builder already refuses to emit an incomplete series; this records the
    result so the claim is checkable rather than implied.
    """
    quarterly = {row["drug_name"] for row in coverage}
    excluded = {row["drug_name"] for row in exclusions}
    annual_only = ANNUAL_METADATA.keys() - quarterly - excluded
    accounted = quarterly | annual_only | excluded

    # The catalog is what seed/example_drugs.csv lists, read from the file. It
    # used to be defined as "whatever was accounted for", which made
    # "complete" true by construction: the set could not contain anything the
    # dataset had missed. Adding comparator products from other therapy areas
    # is what exposed that - a tautology only shows itself when something
    # arrives that it should have excluded.
    seed_products = seed_catalog()
    comparators = sorted((quarterly | annual_only) - seed_products)
    unaccounted = sorted(seed_products - accounted)
    incomplete = sorted(row["drug_name"] for row in coverage if row["missing_quarters"])
    return {
        "catalog_products": len(seed_products),
        "accounted_for": len(seed_products & accounted),
        "unaccounted_products": unaccounted,
        "complete_quarterly_series": len(quarterly),
        "quarterly_observations": sum(row["observed_quarters"] for row in coverage),
        "series_missing_quarters": incomplete,
        "annual_benchmark_series": len(annual_only),
        "annual_observations": len(annual),
        "evidence_backed_exclusions": len(excluded),
        # Products outside the seed catalog entirely: comparators from other
        # therapeutic areas. They are additive and never part of the
        # completeness claim, which is about the pulmonary hypertension catalog.
        "comparator_products": comparators,
        "therapeutic_areas": sorted(
            {
                meta.get("therapeutic_area", "Pulmonary hypertension")
                for name, meta in PRODUCT_METADATA.items()
                if name in quarterly
            }
        ),
        # True only when every seed-catalog product is accounted for and no
        # included series has a hole in its own span.
        "complete": not incomplete and not unaccounted,
    }


def seed_catalog() -> set[str]:
    """The products this dataset is answerable for, read from the seed file."""
    with (SEED_DIR / "example_drugs.csv").open(newline="") as handle:
        return {row["drug_name"] for row in csv.DictReader(handle)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=GOLD_DIR)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument(
        "--reuse-quarterly",
        action="store_true",
        help=(
            "Read the quarterly rows back from OUT_DIR/quarterly_revenue.jsonl "
            "instead of re-fetching their sources. Use this only when a build "
            "changes nothing on the quarterly side (an annual-manifest edit, "
            "say); the rows read back are still put through the full coverage "
            "check, so a stale or incomplete series fails the build."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.reuse_quarterly:
        published = out_dir / "quarterly_revenue.jsonl"
        if not published.exists():
            raise SystemExit(f"--reuse-quarterly needs {published}, which does not exist")
        # Only the fetching series are reused. Yutrepia and Winrevair are built
        # straight from their manifests with no network, so rebuilding them is
        # free - and reusing them instead would silently ignore an edit to
        # those manifests, which is exactly the kind of staleness this flag
        # must not introduce.
        rebuilt = build_yutrepia() + build_winrevair() + build_adempas() + build_opsumit() + build_tracleer() + build_letairis() + build_gilead_comparators()
        # Remodulin is only partly manifest-backed, so it is refreshed by
        # period rather than by dropping the whole product.
        early = build_remodulin_early()
        early_keys = {(row["drug_name"], row["period"]) for row in early}
        rebuilt_drugs = {row["drug_name"] for row in rebuilt}
        quarterly = deduplicate(
            [
                json.loads(line)
                for line in published.read_text().splitlines()
                if line.strip()
                and json.loads(line)["drug_name"] not in rebuilt_drugs
                and (json.loads(line)["drug_name"], json.loads(line)["period"])
                not in early_keys
            ]
            + rebuilt
            + early
        )
        quarterly = apply_acquisition_bridges(quarterly)
    else:
        client = ResearchClient(args.cache_dir)
        try:
            quarterly = deduplicate(
                build_uthr(client)
                + build_uptravi(client)
                + build_yutrepia()
                + build_winrevair()
                + build_adempas()
                + build_opsumit()
                + build_tracleer()
                + build_letairis()
                + build_gilead_comparators()
            )
            quarterly = apply_acquisition_bridges(quarterly)
        finally:
            client.close()
    annual = sorted(build_annual_rows(), key=lambda row: (row["drug_name"], row["period"]))
    coverage = coverage_rows(quarterly)
    incomplete = [row for row in coverage if not row["benchmark_eligible"]]
    if incomplete:
        details = {
            row["drug_name"]: {
                "missing": row["missing_quarters"],
                "beyond_series_end": row["quarters_beyond_series_end"],
            }
            for row in incomplete
        }
        raise ValueError(f"Incomplete independently researched series: {details}")
    peaks = build_peaks(quarterly, annual)
    exclusions = build_exclusions()
    catalog = catalog_coverage(coverage, exclusions)

    write_jsonl(out_dir / "quarterly_revenue.jsonl", quarterly)
    write_jsonl(out_dir / "annual_revenue.jsonl", annual)
    write_jsonl(out_dir / "series_coverage.jsonl", coverage)
    write_jsonl(out_dir / "peak_sales.jsonl", peaks)
    write_jsonl(out_dir / "excluded_products.jsonl", exclusions)
    (out_dir / "unresolved_quarters.jsonl").write_text("")
    completeness = gold_completeness(coverage, exclusions, annual)
    report = {
        "generation": PROVENANCE,
        "as_of_quarter": AS_OF_QUARTER,
        "quarterly_rows": len(quarterly),
        "annual_rows": len(annual),
        "complete_quarterly_series": len(coverage),
        "quarterly_coverage_pct": 100.0,
        "observed_peaks": sum(row["peak_status"] == "observed" for row in peaks),
        "not_yet_observed_peaks": sum(row["peak_status"] == "not_yet_observed" for row in peaks),
        "excluded_products": len(exclusions),
        "catalog_coverage": catalog,
        "gold_completeness": completeness,
    }
    manifest = {
        "name": "independent_pah_peak_sales_gold",
        "generation": PROVENANCE,
        "as_of_quarter": AS_OF_QUARTER,
        # The pulmonary hypertension catalog this dataset is answerable for,
        # read from seed/example_drugs.csv. Comparator products from other
        # therapy areas are counted separately and are never part of the
        # completeness claim.
        "target_product_count": len(seed_catalog()),
        "comparator_product_count": len(completeness["comparator_products"]),
        "therapeutic_areas": completeness["therapeutic_areas"],
        "quarterly_series_count": len(coverage),
        "annual_only_series_count": len(catalog["annual_only_products"]),
        "excluded_product_count": len(exclusions),
        "quarterly_coverage_pct": 100.0,
        "reported_rows_file": "quarterly_revenue.jsonl",
        "annual_rows_file": "annual_revenue.jsonl",
        "coverage_file": "series_coverage.jsonl",
        "peak_sales_file": "peak_sales.jsonl",
        "excluded_products_file": "excluded_products.jsonl",
        # Not revenue rows: inputs with no single right answer, and the verdict
        # each should reach. Kept in gold because "what the pipeline must refuse
        # to answer" is part of the benchmark, not a test fixture.
        "adjudication_cases_file": "adjudication_cases.jsonl",
        "source_manifest_directory": "source_manifests",
        "gold_builder": "scripts/build_independent_gold.py",
        "pipeline_code_allowed_in_builder": False,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out_dir / "build_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
