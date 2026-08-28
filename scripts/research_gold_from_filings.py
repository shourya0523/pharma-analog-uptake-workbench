"""Independently research seed/gold from issuer filings and IR, not the pipeline.

Gold is a source of truth. This script fetches SEC exhibits and company IR sales
schedules, reads product-level quarterly tables, and writes cited gold rows.
The extraction pipeline is evaluated against that gold; it must not write it.

Usage:
    cd backend && uv run python ../scripts/research_gold_from_filings.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import date
from io import BytesIO
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.analytics.gold_dataset import (
    fill_lifecycle_unresolved,
    lifecycle_record,
    peak_record,
    reported_periods,
    revenue_gold_row,
    unresolved_row,
)
from app.analytics.lifecycle import latest_completed_quarter
from app.config import get_settings
from app.connectors.openfda import search_queries
from app.connectors.openfda_fields import selected_approval_date
from app.connectors.sources import is_earnings_exhibit, parse_filing_date
from app.llm.aliases import merge_aliases
from app.parsing.periods import MONTHS, quarter_of_month
from app.quality.candidate_filters import filter_revenue_candidates
from app.quality.comparative import ABS_TOLERANCE, parse_numbers

GOLD = REPO_ROOT / "seed" / "gold"
SEED = REPO_ROOT / "seed" / "example_drugs.csv"
CACHE = Path("/tmp/gold-research")
AS_OF = date(2026, 8, 28)

CIKS = {
    "UTHR": "0001082554",
    "JNJ": "0000200406",
    "MRK": "0000310158",
    "GILD": "0000882095",
    "LQDA": "0001819576",
}

UT_PRODUCTS = {
    "Tyvaso DPI": {
        "drug_name": "Tyvaso DPI",
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "labels": ("tyvaso dpi",),
        "revenue_scope": "Formulation-specific",
        "geography": "Worldwide",
        "formulation": "inhalation powder",
        "route_of_administration": "inhalation",
    },
    "Nebulized Tyvaso": {
        "drug_name": "Nebulized Tyvaso",
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "labels": ("nebulized tyvaso",),
        "revenue_scope": "Formulation-specific",
        "geography": "Worldwide",
        "formulation": "inhalation solution",
        "route_of_administration": "inhalation",
    },
    "Tyvaso": {
        "drug_name": "Tyvaso",
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "labels": ("total tyvaso", "tyvaso"),
        "revenue_scope": "Product family",
        "geography": "Worldwide",
        "formulation": "inhalation",
        "route_of_administration": "inhalation",
    },
    "Remodulin": {
        "drug_name": "Remodulin",
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "labels": ("remodulin",),
        "revenue_scope": "Worldwide",
        "geography": "Worldwide",
        "formulation": "injection",
        "route_of_administration": "parenteral",
    },
    "Orenitram": {
        "drug_name": "Orenitram",
        "generic_name": "treprostinil",
        "manufacturer": "United Therapeutics",
        "labels": ("orenitram",),
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "extended-release tablet",
        "route_of_administration": "oral",
    },
    "Adcirca": {
        "drug_name": "Adcirca",
        "generic_name": "tadalafil",
        "manufacturer": "United Therapeutics",
        "labels": ("adcirca",),
        "revenue_scope": "U.S.",
        "geography": "United States",
        "formulation": "tablet",
        "route_of_administration": "oral",
    },
}

JNJ_IR_PDFS = []
for year in range(2018, 2027):
    for q in (1, 2, 3, 4):
        if (year, q) > (2026, 2):
            continue
        yy = str(year)[2:]
        JNJ_IR_PDFS.extend(
            [
                f"https://s203.q4cdn.com/636242992/files/doc_financials/{year}/q{q}/{q}Q{yy}-Other-Financial-Disclosures.pdf",
                f"https://s203.q4cdn.com/636242992/files/doc_financials/{year}/q{q}/PROTECTED-{q}Q{yy}-Other-Financial-Disclosures.pdf",
                f"https://s203.q4cdn.com/636242992/files/doc_financials/{year}/q{q}/{q}Q{yy}-Sales-of-Key-Products-Franchises.pdf",
            ]
        )

MERCK_IR_PDFS = [
    "https://www.merck.com/wp-content/uploads/sites/124/2026/08/2Q26-Merck-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2026/05/1Q26-Merck-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2026/02/4Q25-Merck-Other-Financial-Disclosures.pdf",
    "https://www.msd.com/wp-content/uploads/sites/9/2026/02/4Q25-MSD-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2025/10/3Q25-Merck-Other-Financial-Disclosures.pdf",
    "https://www.msd.com/wp-content/uploads/sites/9/2025/10/3Q25-MSD-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2025/07/2Q25-Merck-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2025/04/1Q25-Merck-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2025/02/4Q24-Merck-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2024/10/3Q24-Merck-Other-Financial-Disclosures.pdf",
    "https://www.merck.com/wp-content/uploads/sites/124/2024/07/2Q24-Merck-Other-Financial-Disclosures.pdf",
]

_THREE_MONTHS = re.compile(r"three\s+months?\s+ended\s+([A-Za-z]+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def load_seed() -> list[dict]:
    with SEED.open(newline="") as handle:
        return list(csv.DictReader(handle))


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""))


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sec_headers() -> dict[str, str]:
    return {
        "User-Agent": get_settings().sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
    }


def cache_path(url: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", url)[-180:]
    return CACHE / safe


def fetch_bytes(client: httpx.Client, url: str, *, throttle: bool = False) -> bytes | None:
    path = cache_path(url)
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()
    if throttle:
        time.sleep(0.12)
    try:
        resp = client.get(url, timeout=60, follow_redirects=True)
    except httpx.HTTPError as exc:
        print(f"fetch_fail {url} {exc}")
        return None
    if resp.status_code in {403, 503}:
        time.sleep(1.5)
        try:
            resp = client.get(url, timeout=60, follow_redirects=True)
        except httpx.HTTPError as exc:
            print(f"fetch_fail {url} {exc}")
            return None
    if resp.status_code != 200:
        print(f"fetch_status {resp.status_code} {url}")
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return resp.content


def html_tables(html: str) -> list[list[list[str]]]:
    soup = BeautifulSoup(html, "lxml")
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if len(rows) >= 3:
            tables.append(rows)
    return tables


def _join_row(row: list[str]) -> str:
    text = " ".join(cell for cell in row if cell and str(cell).strip())
    text = text.replace("\xa0", " ")
    text = re.sub(r"\(\s*([\d,.]+)\s*\)", r"(\1)", text)
    return re.sub(r"\s+", " ", text)


def monetary_values(text: str) -> list[float]:
    """Keep decimal monetary cells; drop integer % change and footnote markers."""

    values = parse_numbers(text)
    return [value for value in values if abs(value - round(value)) > 0.001 or abs(value) >= 100]


def clean_label(cell: str) -> str:
    text = re.sub(r"\(\d+\)", "", cell or "")
    text = text.replace("®", " ").replace("™", " ").replace("©", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip(" :").lower()


def match_ut_product(label: str) -> str | None:
    normalized = clean_label(label)
    if "unituxin" in normalized or normalized in {"other", "total revenues", "total revenue"}:
        return None
    if "tyvaso dpi" in normalized:
        return "Tyvaso DPI"
    if "nebulized tyvaso" in normalized:
        return "Nebulized Tyvaso"
    if normalized in {"total tyvaso", "tyvaso"} or normalized.startswith("tyvaso ") and "dpi" not in normalized and "nebulized" not in normalized:
        return "Tyvaso"
    if normalized.startswith("remodulin"):
        return "Remodulin"
    if normalized.startswith("orenitram"):
        return "Orenitram"
    if normalized.startswith("adcirca"):
        return "Adcirca"
    return None


def three_month_quarter(rows: list[list[str]]) -> int | None:
    blob = _join_row([" ".join(_join_row(row) for row in rows[:8])])
    match = _THREE_MONTHS.search(blob)
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if not month:
        return None
    return quarter_of_month(month)


def year_headers(rows: list[list[str]]) -> list[int]:
    for row in rows[:8]:
        years = [int(y) for cell in row for y in _YEAR_RE.findall(cell)]
        if len(years) >= 2:
            return years
    return []


def parse_ut_tables(tables: list[list[list[str]]], *, url: str, title: str, source_date: str | None, accession: str) -> list[dict]:
    rows_out: list[dict] = []
    for table in tables:
        quarter = three_month_quarter(table)
        years = year_headers(table)
        if quarter is None or len(years) < 2:
            continue
        blob = " ".join(_join_row(row) for row in table[:6]).lower()
        if "net product sales" not in blob and "net sales" not in blob:
            continue
        for row in table:
            if not row:
                continue
            product_key = match_ut_product(row[0] if row[0].strip() else _join_row(row[:1]))
            if not product_key:
                continue
            values = monetary_values(_join_row(row[1:] or row))
            if len(values) < 2:
                continue
            if len(values) >= 3 and abs((values[0] - values[1]) - values[2]) > ABS_TOLERANCE:
                continue
            meta = UT_PRODUCTS[product_key]
            quote = _join_row(row)
            for year, value in zip(years[:2], values[:2], strict=False):
                period = f"{year}Q{quarter}"
                try:
                    gold = revenue_gold_row(
                        drug_name=meta["drug_name"],
                        generic_name=meta["generic_name"],
                        manufacturer=meta["manufacturer"],
                        period=period,
                        value_reported=value,
                        revenue_scope=meta["revenue_scope"],
                        geography=meta["geography"],
                        formulation=meta["formulation"],
                        route_of_administration=meta["route_of_administration"],
                        source_type="sec_filing",
                        source_url=url,
                        source_title=title,
                        source_quote=quote,
                        source_date=source_date,
                        filing_type="8-K Exhibit 99.1",
                        accession_number=accession,
                        page_or_section="Net product sales",
                        gold_notes="Read from United Therapeutics earnings exhibit net product sales table.",
                    )
                except ValueError:
                    continue
                candidate = {
                    "period": gold["period"],
                    "value_reported": gold["value_reported"],
                    "revenue_scope": gold["revenue_scope"],
                    "source_quote": gold["source_quote"],
                    "period_type": "quarterly",
                    "currency": "USD",
                }
                kept, dropped = filter_revenue_candidates(
                    [candidate], product=meta["drug_name"], generic=meta["generic_name"]
                )
                if not kept or dropped:
                    continue
                rows_out.append(gold)
    return rows_out


def list_earnings_exhibits(client: httpx.Client, cik: str) -> list[dict]:
    cik_z = cik.zfill(10)
    payload = fetch_bytes(client, f"https://data.sec.gov/submissions/CIK{cik_z}.json", throttle=True)
    if not payload:
        return []
    data = json.loads(payload)
    filings = data.get("filings", {})
    recent = filings.get("recent", {})
    extras: list[dict] = []
    for file_meta in filings.get("files") or []:
        name = file_meta.get("name")
        if not name:
            continue
        extra = fetch_bytes(client, f"https://data.sec.gov/submissions/{name}", throttle=True)
        if extra:
            extras.append(json.loads(extra))
    buckets = [recent, *extras]
    out: list[dict] = []
    seen: set[str] = set()
    for bucket in buckets:
        forms = bucket.get("form", [])
        accessions = bucket.get("accessionNumber", [])
        filing_dates = bucket.get("filingDate", [])
        items = bucket.get("items", [])
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            filing_items = items[i] if i < len(items) else ""
            if "2.02" not in (filing_items or ""):
                continue
            accession = accessions[i]
            if accession in seen:
                continue
            seen.add(accession)
            fdate = filing_dates[i] if i < len(filing_dates) else None
            filed_on = parse_filing_date(fdate)
            if filed_on and filed_on > AS_OF:
                continue
            out.append({"accession": accession, "filing_date": fdate, "cik": cik_z})
    return out


def exhibit_url(cik: str, accession: str, doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"


def list_documents(client: httpx.Client, cik: str, accession: str) -> list[str]:
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/index.json"
    raw = fetch_bytes(client, url, throttle=True)
    if not raw:
        return []
    try:
        items = json.loads(raw).get("directory", {}).get("item", [])
    except json.JSONDecodeError:
        return []
    return [item.get("name", "") for item in items if item.get("name")]


def scrape_ut(client: httpx.Client) -> list[dict]:
    exhibits = list_earnings_exhibits(client, CIKS["UTHR"])
    print(f"ut_exhibits {len(exhibits)}")
    rows: list[dict] = []
    for item in exhibits:
        docs = [name for name in list_documents(client, item["cik"], item["accession"]) if is_earnings_exhibit(name)]
        if not docs:
            continue
        url = exhibit_url(item["cik"], item["accession"], docs[0])
        raw = fetch_bytes(client, url, throttle=True)
        if not raw:
            continue
        html = raw.decode("utf-8", errors="ignore")
        title = f"United Therapeutics 8-K Exhibit 99.1 {item['filing_date'] or ''}".strip()
        parsed = parse_ut_tables(
            html_tables(html),
            url=url,
            title=title,
            source_date=item["filing_date"],
            accession=item["accession"],
        )
        print(f"ut {item['filing_date']} {docs[0]} rows={len(parsed)}")
        rows.extend(parsed)
    return rows


def pdf_text(raw: bytes) -> str:
    import pdfplumber

    blocks: list[str] = []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        for page in pdf.pages[:20]:
            text = page.extract_text() or ""
            if text.strip():
                blocks.append(text)
    return "\n".join(blocks)


def parse_jnj_pdf(raw: bytes, url: str) -> list[dict]:
    """J&J supplementary sales: WW product rows for UPTRAVI and OPSUMIT/OPSYNVI."""

    text = pdf_text(raw).replace("\xa0", " ")
    q_match = re.search(r"([1-4])Q(\d{2})", url)
    if not q_match:
        return []
    quarter = int(q_match.group(1))
    year = 2000 + int(q_match.group(2))
    patterns = [
        (
            "Uptravi",
            r"UPTRAVI\s+US\s+[^\n]*\nIntl[^\n]*\nWW\s+([\d,.]+)\s+([\d,.]+)",
            "Worldwide",
            "selexipag",
            "Worldwide Uptravi sales from J&J supplementary sales (WW line).",
        ),
        (
            "Opsumit",
            r"OPSUMIT\s*/\s*OPSYNVI\s+US\s+[^\n]*\nIntl[^\n]*\nWW\s+([\d,.]+)\s+([\d,.]+)",
            "Product family",
            "macitentan",
            "J&J reports OPSUMIT/OPSYNVI as a combined WW line; treated as Opsumit product-family total.",
        ),
        (
            "Opsumit",
            r"OPSUMIT\s+US\s+[^\n]*\nIntl[^\n]*\nWW\s+([\d,.]+)\s+([\d,.]+)",
            "Worldwide",
            "macitentan",
            "Worldwide Opsumit sales from J&J supplementary sales (WW line).",
        ),
        (
            "Tracleer",
            r"TRACLEER\s+US\s+[^\n]*\nIntl[^\n]*\nWW\s+([\d,.]+)\s+([\d,.]+)",
            "Worldwide",
            "bosentan",
            "Worldwide Tracleer sales from J&J supplementary sales (WW line).",
        ),
    ]
    rows_out: list[dict] = []
    for drug, pattern, scope, generic, notes in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        current = float(match.group(1).replace(",", ""))
        prior = float(match.group(2).replace(",", ""))
        quote = re.sub(r"\s+", " ", match.group(0))
        form = "tablet"
        mfr = "Johnson & Johnson"
        for period, value in ((f"{year}Q{quarter}", current), (f"{year - 1}Q{quarter}", prior)):
            try:
                gold = revenue_gold_row(
                    drug_name=drug,
                    generic_name=generic,
                    manufacturer=mfr,
                    period=period,
                    value_reported=value,
                    revenue_scope=scope,
                    geography="Worldwide",
                    formulation=form,
                    route_of_administration="oral",
                    source_type="company_ir",
                    source_url=url,
                    source_title=f"Johnson & Johnson Other Financial Disclosures {year}Q{quarter}",
                    source_quote=quote,
                    filing_type="Other financial disclosures",
                    gold_notes=notes,
                )
            except ValueError:
                continue
            candidate = {
                "period": gold["period"],
                "value_reported": gold["value_reported"],
                "revenue_scope": gold["revenue_scope"],
                "source_quote": gold["source_quote"],
                "period_type": "quarterly",
                "currency": "USD",
            }
            kept, dropped = filter_revenue_candidates([candidate], product=drug, generic=generic)
            if kept and not dropped:
                rows_out.append(gold)
    return rows_out


def parse_merck_schedule(text: str, url: str) -> list[dict]:
    """Merck other-financial-disclosures sales schedule.

    2Q documents typically list 1Q, 2Q, YTD for the current year then the prior
    year's 1Q, 2Q, YTD, 3Q, 4Q, FY. 4Q documents list 1Q-4Q + FY for both years.
    """

    one = re.sub(r"\s+", " ", text)
    rows_out: list[dict] = []

    def add(drug: str, generic: str, mfr: str, period: str, value: float, quote: str, scope: str, form: str, notes: str) -> None:
        try:
            gold = revenue_gold_row(
                drug_name=drug,
                generic_name=generic,
                manufacturer=mfr,
                period=period,
                value_reported=value,
                revenue_scope=scope,
                geography="Worldwide",
                formulation=form,
                route_of_administration="subcutaneous" if drug == "Winrevair" else "oral",
                source_type="company_ir",
                source_url=url,
                source_title="Merck Other Financial Disclosures",
                source_quote=quote[:500],
                filing_type="Other financial disclosures",
                gold_notes=notes,
            )
        except ValueError:
            return
        candidate = {
            "period": gold["period"],
            "value_reported": gold["value_reported"],
            "revenue_scope": gold["revenue_scope"],
            "source_quote": gold["source_quote"],
            "period_type": "quarterly",
            "currency": "USD",
        }
        kept, dropped = filter_revenue_candidates([candidate], product=drug, generic=generic)
        if kept and not dropped:
            rows_out.append(gold)

    # Winrevair 2Q26-style: 525 588 1114 280 336 615 360 467 1443
    m = re.search(
        r"Winrevair\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)",
        one,
        re.IGNORECASE,
    )
    q_from_url = re.search(r"([1-4])Q(\d{2})", url)
    if m and q_from_url and q_from_url.group(1) == "2":
        year = 2000 + int(q_from_url.group(2))
        nums = [float(x.replace(",", "")) for x in m.groups()]
        quote = m.group(0)
        mapping = [
            (f"{year}Q1", nums[0]),
            (f"{year}Q2", nums[1]),
            (f"{year - 1}Q1", nums[3]),
            (f"{year - 1}Q2", nums[4]),
            (f"{year - 1}Q3", nums[6]),
            (f"{year - 1}Q4", nums[7]),
        ]
        for period, value in mapping:
            add(
                "Winrevair",
                "sotatercept-csrk",
                "Merck",
                period,
                value,
                quote,
                "Worldwide",
                "injection",
                "Worldwide WINREVAIR net sales from Merck product sales schedule.",
            )
        am = re.search(
            r"Adempas(?:\s+\(\d+\))?\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)",
            one,
            re.IGNORECASE,
        )
        if am:
            anums = [float(x.replace(",", "")) for x in am.groups()]
            aquote = am.group(0)
            amap = [
                (f"{year}Q1", anums[0]),
                (f"{year}Q2", anums[1]),
                (f"{year - 1}Q1", anums[3]),
                (f"{year - 1}Q2", anums[4]),
                (f"{year - 1}Q3", anums[6]),
                (f"{year - 1}Q4", anums[7]),
            ]
            for period, value in amap:
                add(
                    "Adempas",
                    "riociguat",
                    "Bayer/Merck",
                    period,
                    value,
                    aquote,
                    "Worldwide",
                    "tablet",
                    "Merck-recorded Adempas net sales (not Adempas/Verquvo alliance revenue).",
                )

    # 4Q-style: 1Q 2Q 3Q 4Q FY current then prior 1Q 2Q 3Q 4Q FY
    m4 = re.search(
        r"Winrevair\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)",
        one,
        re.IGNORECASE,
    )
    if m4 and q_from_url and q_from_url.group(1) == "4":
        year = 2000 + int(q_from_url.group(2))
        nums = [float(x.replace(",", "")) for x in m4.groups()]
        quote = m4.group(0)
        # Prefer 2Q mapping when both match; 4Q docs still fill prior-year Q3/Q4/Q1/Q2
        mapping = [
            (f"{year}Q1", nums[0]),
            (f"{year}Q2", nums[1]),
            (f"{year}Q3", nums[2]),
            (f"{year}Q4", nums[3]),
            (f"{year - 1}Q1", nums[5]),
            (f"{year - 1}Q2", nums[6]),
            (f"{year - 1}Q3", nums[7]),
            (f"{year - 1}Q4", nums[8]),
        ]
        for period, value in mapping:
            add(
                "Winrevair",
                "sotatercept-csrk",
                "Merck",
                period,
                value,
                quote,
                "Worldwide",
                "injection",
                "Worldwide WINREVAIR net sales from Merck product sales schedule.",
            )
        am = re.search(
            r"Adempas(?:\s+\(\d+\))?\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)",
            one,
            re.IGNORECASE,
        )
        if am:
            anums = [float(x.replace(",", "")) for x in am.groups()]
            aquote = am.group(0)
            amap = [
                (f"{year}Q1", anums[0]),
                (f"{year}Q2", anums[1]),
                (f"{year}Q3", anums[2]),
                (f"{year}Q4", anums[3]),
                (f"{year - 1}Q1", anums[5]),
                (f"{year - 1}Q2", anums[6]),
                (f"{year - 1}Q3", anums[7]),
                (f"{year - 1}Q4", anums[8]),
            ]
            for period, value in amap:
                add(
                    "Adempas",
                    "riociguat",
                    "Bayer/Merck",
                    period,
                    value,
                    aquote,
                    "Worldwide",
                    "tablet",
                    "Merck-recorded Adempas net sales (not Adempas/Verquvo alliance revenue).",
                )
    return rows_out


def scrape_jnj(client: httpx.Client) -> list[dict]:
    rows: list[dict] = []
    for url in JNJ_IR_PDFS:
        raw = fetch_bytes(client, url)
        if not raw or not raw.startswith(b"%PDF"):
            continue
        parsed = parse_jnj_pdf(raw, url)
        print(f"jnj {url.split('/')[-1]} rows={len(parsed)}")
        rows.extend(parsed)
    return rows


ACTELION_SALES_URL = (
    "https://s203.q4cdn.com/636242992/files/doc_financials/2017/q2/Actelion_Historical_Sales_Schedule.pdf"
)
ACTELION_WW_PERIODS = ["2017Q1", "2016Q4", "2016Q3", "2016Q2", "2016Q1"]
ACTELION_PRODUCTS = (
    ("Opsumit", "macitentan"),
    ("Tracleer", "bosentan"),
    ("Uptravi", "selexipag"),
)


def parse_actelion_schedule(text: str, url: str = ACTELION_SALES_URL) -> list[dict]:
    """Issuer USD schedule. Columns are Q2'17 stub, Q1'17, then 2016 Q4-Q1, then FY.

    Product labels sit on their own line above US / Intl / WW. Skip the stub
    Q2 2017 column (through 15 Jun) and the full-year column.
    """

    rows_out: list[dict] = []
    for drug, generic in ACTELION_PRODUCTS:
        match = re.search(
            rf"{re.escape(drug)}\s*\nUS[^\n]*\nIntl[^\n]*\nWW\s+([^\n]+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            print(f"actelion miss {drug}")
            continue
        nums = parse_numbers(match.group(1))
        if len(nums) < 6:
            print(f"actelion short {drug} {nums}")
            continue
        quote = re.sub(r"\s+", " ", match.group(0))
        for period, value in zip(ACTELION_WW_PERIODS, nums[1:6], strict=True):
            try:
                gold = revenue_gold_row(
                    drug_name=drug,
                    generic_name=generic,
                    manufacturer="Actelion/J&J",
                    period=period,
                    value_reported=value,
                    revenue_scope="Worldwide",
                    geography="Worldwide",
                    formulation="tablet",
                    route_of_administration="oral",
                    source_type="company_ir",
                    source_url=url,
                    source_title="Historical Actelion Sales (Unaudited)",
                    source_quote=quote,
                    source_date="2017-06",
                    filing_type="Historical sales schedule",
                    gold_notes=(
                        "Actelion USD sales schedule; 2016 figures converted by the "
                        "issuer at average FX. Independent of the extraction pipeline."
                    ),
                )
            except ValueError:
                continue
            candidate = {
                "period": gold["period"],
                "value_reported": gold["value_reported"],
                "revenue_scope": gold["revenue_scope"],
                "source_quote": gold["source_quote"],
                "period_type": "quarterly",
                "currency": "USD",
            }
            kept, dropped = filter_revenue_candidates([candidate], product=drug, generic=generic)
            if kept and not dropped:
                rows_out.append(gold)
    return rows_out


def scrape_actelion(client: httpx.Client) -> list[dict]:
    """Actelion historical USD sales schedule (pre-J&J close). Q2 2017 is stub-period; skip it."""

    raw = fetch_bytes(client, ACTELION_SALES_URL)
    if not raw or not raw.startswith(b"%PDF"):
        return []
    rows_out = parse_actelion_schedule(pdf_text(raw), ACTELION_SALES_URL)
    print(f"actelion rows={len(rows_out)}")
    return rows_out


def scrape_merck(client: httpx.Client) -> list[dict]:
    rows: list[dict] = []
    for url in MERCK_IR_PDFS:
        raw = fetch_bytes(client, url)
        if not raw or not raw.startswith(b"%PDF"):
            continue
        parsed = parse_merck_schedule(pdf_text(raw), url)
        print(f"merck {url.split('/')[-1]} rows={len(parsed)}")
        rows.extend(parsed)
    return rows


def parse_liquidia_tables(
    tables: list[list[list[str]]],
    *,
    html: str,
    url: str,
    title: str,
    source_date: str | None,
    accession: str,
) -> list[dict]:
    soup_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    if not re.search(r"YUTREPIA", soup_text, re.IGNORECASE):
        return []
    thousands = bool(re.search(r"in thousands", soup_text, re.IGNORECASE))
    rows_out: list[dict] = []
    for table in tables:
        quarter = three_month_quarter(table)
        years = year_headers(table)
        if quarter is None or not years:
            continue
        for row in table:
            if not row:
                continue
            label = clean_label(_join_row(row))
            if "product sales" not in label:
                continue
            if "cost of" in label:
                continue
            values = monetary_values(_join_row(row))
            if not values:
                continue
            period_years = years[:2] if len(years) >= 2 and len(values) >= 2 else years[:1]
            for year, value in zip(period_years, values[: len(period_years)], strict=False):
                usd_m = round(value / 1000.0, 3) if thousands or value > 1000 else value
                if usd_m <= 0:
                    continue
                quote = (
                    "YUTREPIA (treprostinil) inhalation powder. "
                    + _join_row(row)
                    + (f" ({usd_m} million; dollars in thousands)" if thousands or value > 1000 else "")
                )
                try:
                    gold = revenue_gold_row(
                        drug_name="Yutrepia",
                        generic_name="treprostinil",
                        manufacturer="Liquidia",
                        period=f"{year}Q{quarter}",
                        value_reported=usd_m,
                        revenue_scope="U.S.",
                        geography="United States",
                        formulation="inhalation powder",
                        route_of_administration="inhalation",
                        source_type="sec_filing",
                        source_url=url,
                        source_title=title,
                        source_quote=quote,
                        source_date=source_date,
                        filing_type="8-K Exhibit 99.1",
                        accession_number=accession,
                        gold_notes="Yutrepia is Liquidia's commercial product; U.S. net product sales from the earnings exhibit.",
                    )
                except ValueError:
                    continue
                candidate = {
                    "period": gold["period"],
                    "value_reported": gold["value_reported"],
                    "revenue_scope": gold["revenue_scope"],
                    "source_quote": gold["source_quote"],
                    "period_type": "quarterly",
                    "currency": "USD",
                }
                kept, dropped = filter_revenue_candidates(
                    [candidate], product="Yutrepia", generic="treprostinil"
                )
                if kept and not dropped:
                    rows_out.append(gold)
    return rows_out


def scrape_liquidia(client: httpx.Client) -> list[dict]:
    exhibits = list_earnings_exhibits(client, CIKS["LQDA"])
    print(f"lqda_exhibits {len(exhibits)}")
    rows: list[dict] = []
    for item in exhibits:
        docs = [name for name in list_documents(client, item["cik"], item["accession"]) if is_earnings_exhibit(name)]
        if not docs:
            continue
        url = exhibit_url(item["cik"], item["accession"], docs[0])
        raw = fetch_bytes(client, url, throttle=True)
        if not raw:
            continue
        html = raw.decode("utf-8", errors="ignore")
        parsed = parse_liquidia_tables(
            html_tables(html),
            html=html,
            url=url,
            title=f"Liquidia 8-K Exhibit 99.1 {item['filing_date'] or ''}".strip(),
            source_date=item["filing_date"],
            accession=item["accession"],
        )
        print(f"lqda {item['filing_date']} rows={len(parsed)}")
        rows.extend(parsed)
    return rows


def scrape_gilead_letairis(client: httpx.Client) -> list[dict]:
    exhibits = list_earnings_exhibits(client, CIKS["GILD"])
    print(f"gild_exhibits {len(exhibits)}")
    rows: list[dict] = []
    for item in exhibits:
        docs = [name for name in list_documents(client, item["cik"], item["accession"]) if is_earnings_exhibit(name)]
        if not docs:
            continue
        url = exhibit_url(item["cik"], item["accession"], docs[0])
        raw = fetch_bytes(client, url, throttle=True)
        if not raw:
            continue
        html = raw.decode("utf-8", errors="ignore")
        tables = html_tables(html)
        for table in tables:
            quarter = three_month_quarter(table)
            years = year_headers(table)
            if quarter is None or len(years) < 2:
                continue
            for row in table:
                if not row:
                    continue
                label = clean_label(row[0])
                if "letairis" not in label:
                    continue
                values = parse_numbers(" ".join(row[1:]))
                if len(values) < 2:
                    continue
                quote = " ".join(cell for cell in row if cell and cell.strip())
                for year, value in zip(years[:2], values[:2], strict=False):
                    try:
                        gold = revenue_gold_row(
                            drug_name="Letairis",
                            generic_name="ambrisentan",
                            manufacturer="Gilead",
                            period=f"{year}Q{quarter}",
                            value_reported=value,
                            revenue_scope="U.S.",
                            geography="United States",
                            formulation="tablet",
                            route_of_administration="oral",
                            source_type="sec_filing",
                            source_url=url,
                            source_title=f"Gilead 8-K Exhibit 99.1 {item['filing_date'] or ''}".strip(),
                            source_quote=quote,
                            source_date=item["filing_date"],
                            filing_type="8-K Exhibit 99.1",
                            accession_number=item["accession"],
                            gold_notes="Letairis U.S. product sales from Gilead earnings exhibit.",
                        )
                    except ValueError:
                        continue
                    candidate = {
                        "period": gold["period"],
                        "value_reported": gold["value_reported"],
                        "revenue_scope": gold["revenue_scope"],
                        "source_quote": gold["source_quote"],
                        "period_type": "quarterly",
                        "currency": "USD",
                    }
                    kept, dropped = filter_revenue_candidates(
                        [candidate], product="Letairis", generic="ambrisentan"
                    )
                    if kept and not dropped:
                        rows.append(gold)
    return rows


def prefer_current_filing(rows: list[dict]) -> list[dict]:
    """Keep one row per drug/period/scope/geo/formulation; prefer later source_date."""

    best: dict[tuple, dict] = {}
    for row in rows:
        key = (
            row["drug_name"],
            row["period"],
            row["revenue_scope"],
            row.get("geography") or "",
            row.get("formulation") or "",
        )
        prev = best.get(key)
        if prev is None or str(row.get("source_date") or "") >= str(prev.get("source_date") or ""):
            best[key] = row
    return sorted(best.values(), key=lambda r: (r["drug_name"], r["period"], r["revenue_scope"]))


def fetch_approvals(seed: list[dict], client: httpx.Client) -> dict[str, tuple[date | None, str | None]]:
    approvals: dict[str, tuple[date | None, str | None]] = {}
    for item in seed:
        aliases = merge_aliases(item["drug_name"], item.get("generic_name"))
        queries = search_queries(item["drug_name"], item.get("generic_name"))
        found_date: date | None = None
        found_url: str | None = None
        for _scope, search in queries:
            url = f"https://api.fda.gov/drug/drugsfda.json?search={search}&limit=10"
            raw = fetch_bytes(client, url)
            if not raw:
                continue
            try:
                results = json.loads(raw).get("results") or []
            except json.JSONDecodeError:
                continue
            found = selected_approval_date(
                results, product=item["drug_name"], generic=item.get("generic_name"), aliases=aliases
            )
            if found:
                found_date, found_url = found, url
                break
        approvals[item["drug_name"]] = (found_date, found_url)
        print(f"approval {item['drug_name']}={found_date}")
    return approvals


def company_sources(drug: str) -> list[dict]:
    catalogs = {
        "Adcirca": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001082554&type=8-K", "United Therapeutics EDGAR 8-K exhibits"),
        "Tyvaso": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001082554&type=8-K", "United Therapeutics EDGAR 8-K exhibits"),
        "Tyvaso DPI": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001082554&type=8-K", "United Therapeutics EDGAR 8-K exhibits"),
        "Nebulized Tyvaso": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001082554&type=8-K", "United Therapeutics EDGAR 8-K exhibits"),
        "Remodulin": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001082554&type=8-K", "United Therapeutics EDGAR 8-K exhibits"),
        "Orenitram": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001082554&type=8-K", "United Therapeutics EDGAR 8-K exhibits"),
        "Opsumit": ("https://www.investor.jnj.com/financials/quarterly-results/default.aspx", "J&J quarterly other financial disclosures"),
        "Uptravi": ("https://www.investor.jnj.com/financials/quarterly-results/default.aspx", "J&J quarterly other financial disclosures"),
        "Tracleer": ("https://www.investor.jnj.com/financials/quarterly-results/default.aspx", "J&J quarterly other financial disclosures"),
        "Veletri": ("https://www.investor.jnj.com/financials/quarterly-results/default.aspx", "J&J quarterly other financial disclosures"),
        "Ventavis": ("https://www.investor.jnj.com/financials/quarterly-results/default.aspx", "J&J quarterly other financial disclosures"),
        "Winrevair": ("https://www.merck.com/investor-relations/", "Merck other financial disclosures"),
        "Adempas": ("https://www.merck.com/investor-relations/", "Merck other financial disclosures"),
        "Yutrepia": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001819576&type=8-K", "Liquidia EDGAR 8-K exhibits"),
        "Letairis": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000882095&type=8-K", "Gilead EDGAR 8-K exhibits"),
        "Revatio": ("https://www.pfizer.com/investors", "Pfizer quarterly reporting"),
        "Flolan": ("https://www.gsk.com/en-gb/investors/", "GSK results"),
        "Alyq": ("https://ir.tevapharm.com/", "Teva investor results"),
        "Tadliq": ("https://cmppharma.com/", "CMP Pharma (private)"),
        "Liqrev": ("https://cmppharma.com/", "CMP Pharma (private)"),
    }
    url, title = catalogs.get(
        drug,
        ("https://www.sec.gov/edgar/searchedgar/companysearch", "SEC EDGAR company search"),
    )
    return [
        {
            "source_url": url,
            "source_title": title,
            "observation": "Issuer product tables were checked; this quarter is not broken out as product-level net sales.",
        }
    ]


def non_disclosure_reason(drug: str) -> str:
    reasons = {
        "Tracleer": "Johnson & Johnson supplementary sales no longer list Tracleer as a separate product line.",
        "Veletri": "Johnson & Johnson supplementary sales do not list Veletri as a separate product line.",
        "Ventavis": "Johnson & Johnson supplementary sales do not list Ventavis as a separate product line.",
        "Revatio": "Pfizer product tables do not disclose Revatio as an individual quarterly revenue line.",
        "Flolan": "GSK results do not disclose Flolan as an individual quarterly sales line.",
        "Alyq": "Teva does not disclose Alyq as an individual product revenue line.",
        "Tadliq": "CMP Pharma is private and does not publish Tadliq quarterly product revenue.",
        "Liqrev": "CMP Pharma is private and does not publish Liqrev quarterly product revenue.",
        "Letairis": "Gilead rolled Letairis into Other products and no longer discloses a product-specific quarterly value.",
        "Adempas": "Merck/Bayer schedules do not break out this Adempas quarter as Merck-recorded product net sales.",
        "Winrevair": "Merck product schedule does not report WINREVAIR net sales for this quarter.",
        "Opsumit": "J&J supplementary sales do not list an OPSUMIT or OPSUMIT/OPSYNVI WW figure for this quarter.",
        "Uptravi": "J&J supplementary sales do not list UPTRAVI WW for this quarter.",
    }
    return reasons.get(
        drug,
        "Issuer disclosures checked for this commercial quarter do not break out product-level net sales.",
    )


SCRAPERS = {
    "ut": scrape_ut,
    "jnj": scrape_jnj,
    "actelion": scrape_actelion,
    "merck": scrape_merck,
    "liquidia": scrape_liquidia,
    "gilead": scrape_gilead_letairis,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research seed/gold from issuer filings. Does not run the pipeline.")
    parser.add_argument(
        "--issuers",
        default="all",
        help="Comma list of ut,jnj,actelion,merck,liquidia,gilead or all. Partial runs merge into existing gold.",
    )
    return parser.parse_args(argv)


def selected_scrapers(issuers: str) -> dict:
    if issuers.strip().lower() == "all":
        return SCRAPERS
    wanted = {name.strip().lower() for name in issuers.split(",") if name.strip()}
    unknown = wanted - set(SCRAPERS)
    if unknown:
        raise SystemExit(f"Unknown issuers: {sorted(unknown)}")
    return {name: fn for name, fn in SCRAPERS.items() if name in wanted}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scrapers = selected_scrapers(args.issuers)
    CACHE.mkdir(parents=True, exist_ok=True)
    seed = load_seed()
    headers = sec_headers()
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
        approvals = fetch_approvals(seed, client)
        researched: list[dict] = []
        for name, scrape in scrapers.items():
            print(f"scrape {name}")
            researched.extend(scrape(client))

    existing = load_jsonl(GOLD / "quarterly_revenue.jsonl")
    revenue = prefer_current_filing(existing + researched)
    expanded_unresolved: list[dict] = []
    lifecycle_rows: list[dict] = []
    peak_rows: list[dict] = []
    for item in seed:
        drug = item["drug_name"]
        approval, url = approvals[drug]
        reported = reported_periods(revenue, drug)
        sources = company_sources(drug)
        drug_unresolved = fill_lifecycle_unresolved(
            drug_name=drug,
            approval_date=approval,
            as_of=AS_OF,
            reported=reported,
            existing_unresolved=[
                unresolved_row(
                    drug_name=drug,
                    period=period,
                    sources_checked=sources,
                    reason_unresolved=non_disclosure_reason(drug),
                    recommended_next_step="Keep unresolved. Do not invent product revenue from franchise totals.",
                    confidence_that_unavailable=0.9,
                    gold_notes="Independent filing/IR research did not find a product-level quarterly disclosure.",
                )
                for period in []
            ],
            source_rows=revenue,
        )
        # Replace default gap rows with researched non-disclosure language
        filled = []
        for row in drug_unresolved:
            if row["period"] in reported:
                continue
            filled.append(
                unresolved_row(
                    drug_name=drug,
                    period=row["period"],
                    sources_checked=row.get("sources_checked") or sources,
                    reason_unresolved=non_disclosure_reason(drug),
                    recommended_next_step="Keep unresolved. Do not invent product revenue from franchise totals.",
                    confidence_that_unavailable=0.9,
                    gold_notes="Independent filing/IR research did not find a product-level quarterly disclosure.",
                )
            )
        expanded_unresolved.extend(filled)
        life = lifecycle_record(
            drug_name=drug,
            approval_date=approval,
            as_of=AS_OF,
            reported=reported,
            unresolved={row["period"] for row in filled},
            approval_source_url=url,
        )
        drug_revenue = [row for row in revenue if row["drug_name"] == drug]
        peak = peak_record(
            drug_name=drug,
            rows=drug_revenue,
            as_of=AS_OF,
            expected_count=life["expected_quarter_count"],
        )
        life["peak_eligible"] = peak["peak_eligible"]
        lifecycle_rows.append(life)
        peak_rows.append(peak)

    revenue.sort(key=lambda row: (row["drug_name"], row["period"], row.get("revenue_scope") or ""))
    expanded_unresolved.sort(key=lambda row: (row["drug_name"], row["period"]))
    lifecycle_rows.sort(key=lambda row: row["drug_name"])
    peak_rows.sort(key=lambda row: row["drug_name"])

    dump_jsonl(GOLD / "quarterly_revenue.jsonl", revenue)
    dump_jsonl(GOLD / "unresolved_quarters.jsonl", expanded_unresolved)
    dump_jsonl(GOLD / "lifecycle.jsonl", lifecycle_rows)
    dump_jsonl(GOLD / "peak_sales.jsonl", peak_rows)

    manifest = {
        "coverage_mode": "full_lifecycle",
        "as_of_date": AS_OF.isoformat(),
        "as_of_quarter": latest_completed_quarter(AS_OF),
        "target_drug_count": len(seed),
        "purpose": (
            "Independent source of truth for analog peak sales: every commercial "
            "quarter from FDA approval through the latest completed quarter, cited "
            "from issuer filings and IR, not generated by the extraction pipeline."
        ),
        "generation": "independent_filing_research",
        "reported_rows_file": "quarterly_revenue.jsonl",
        "unresolved_rows_file": "unresolved_quarters.jsonl",
        "lifecycle_file": "lifecycle.jsonl",
        "peak_sales_file": "peak_sales.jsonl",
        "edge_cases_file": "edge_cases.jsonl",
        "metadata_file": "metadata.jsonl",
    }
    (GOLD / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    report = {
        "generated": AS_OF.isoformat(),
        "pipeline": "independent_filing_research",
        "as_of_quarter": manifest["as_of_quarter"],
        "drugs": len(seed),
        "revenue_rows": len(revenue),
        "unresolved_rows": len(expanded_unresolved),
        "peak_eligible": sum(1 for row in peak_rows if row["peak_eligible"]),
        "revenue_by_drug": dict(Counter(row["drug_name"] for row in revenue)),
        "unresolved_by_drug": dict(Counter(row["drug_name"] for row in expanded_unresolved)),
        "coverage_pct_by_drug": {row["drug_name"]: row["coverage_pct"] for row in lifecycle_rows},
        "complete_comparable_years": {row["drug_name"]: row["complete_comparable_years"] for row in peak_rows},
    }
    (GOLD / "build_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
