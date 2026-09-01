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
GOLD_DIR = REPO_ROOT / "seed" / "gold"
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
        "commercial_start_quarter": "2002Q2",
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
    # Opsumit, Veletri and Ventavis stay excluded from the quarterly benchmark
    # (no contiguous launch-to-end series is citable), but Actelion published
    # per-product annual figures for them. Carrying those gives each product a
    # verified number instead of nothing at all - the same annual-context role
    # Flolan already has.
    "Opsumit": {
        "generic_name": "macitentan",
        "manufacturer": "Actelion",
        "benchmark_identity": "actelion_opsumit_worldwide_partial_chf",
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

    for source in read_csv(SOURCE_DIR / "uthr_remodulin_early.csv"):
        rows.append(
            revenue_row(
                drug_name="Remodulin",
                period=source["period"],
                value=float(source["value_reported"]),
                source_url=source["source_url"],
                source_quote=source["source_quote"],
                source_type="sec_filing",
                derivation=source["derivation"],
                precision=source["precision"],
                notes="Early issuer history researched independently from filed reports.",
            )
        )
    return deduplicate(rows)


def pdf_text(raw: bytes) -> str:
    with pdfplumber.open(BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def uptravi_ww(text: str) -> tuple[float, str]:
    match = re.search(r"UPTRAVI[^\n]*\nUS[^\n]*\nIntl[^\n]*\nWW\s+([\d,]+)[^\n]*", text, re.IGNORECASE)
    if not match:
        raise ValueError("UPTRAVI WW row not found")
    return float(match.group(1).replace(",", "")), re.sub(r"\s+", " ", match.group(0))


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
            notes="Combines Actelion sales through June 15 with J&J's June 16-July 2 fiscal stub.",
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
            source_type="company_ir",
            derivation=source["derivation"],
            notes="Merck worldwide product sales; alliance revenue is not used.",
        )
        for source in read_csv(SOURCE_DIR / "merck_winrevair_quarterly.csv")
    ]


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
    total = len(quarterly_products) + len(annual_only) + len(excluded)
    return {
        "catalog_products": total,
        "quarterly_series_products": quarterly_products,
        "annual_only_products": annual_only,
        "excluded_products": excluded,
        "quarterly_series_pct": round(100 * len(quarterly_products) / total, 1),
        "quarterly_observations": sum(row["observed_quarters"] for row in coverage),
        "bounded_series": sorted(
            row["drug_name"] for row in coverage if "series_end_reason" in row
        ),
    }


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
        quarterly = [
            json.loads(line) for line in published.read_text().splitlines() if line.strip()
        ]
    else:
        client = ResearchClient(args.cache_dir)
        try:
            quarterly = deduplicate(
                build_uthr(client) + build_uptravi(client) + build_yutrepia() + build_winrevair()
            )
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
    }
    manifest = {
        "name": "independent_pah_peak_sales_gold",
        "generation": PROVENANCE,
        "as_of_quarter": AS_OF_QUARTER,
        "target_product_count": len(
            set(PRODUCT_METADATA)
            | set(ANNUAL_METADATA)
            | {row["drug_name"] for row in exclusions}
        ),
        "quarterly_series_count": len(coverage),
        "annual_only_series_count": len(catalog["annual_only_products"]),
        "excluded_product_count": len(exclusions),
        "quarterly_coverage_pct": 100.0,
        "reported_rows_file": "quarterly_revenue.jsonl",
        "annual_rows_file": "annual_revenue.jsonl",
        "coverage_file": "series_coverage.jsonl",
        "peak_sales_file": "peak_sales.jsonl",
        "excluded_products_file": "excluded_products.jsonl",
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
