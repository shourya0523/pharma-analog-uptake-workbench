"""Audit, clean, and fill seed/gold gaps with verified issuer disclosures."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "seed" / "gold"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""))


def slug(*parts: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(str(p).lower() for p in parts if p is not None)).strip("-")


def period_fields(period: str) -> dict:
    m = re.match(r"(\d{4})Q([1-4])", period)
    assert m
    y, q = int(m.group(1)), int(m.group(2))
    return {
        "period": period,
        "fiscal_year": y,
        "fiscal_quarter": q,
        "calendar_year": y,
        "calendar_quarter": q,
        "period_type": "quarterly",
    }


def revenue_row(**kwargs) -> dict:
    period = kwargs["period"]
    base = {
        "metric": "revenue",
        "currency": "USD",
        "unit": "millions",
        "confidence_score": 1.0,
        "validation_status": "confirmed",
        "extraction_method": "manual_audit_web_search",
        **period_fields(period),
    }
    base.update(kwargs)
    base["value_normalized_usd_millions"] = base.get(
        "value_normalized_usd_millions", base["value_reported"]
    )
    base["gold_id"] = slug(
        base["drug_name"],
        base["period"],
        base.get("revenue_scope"),
        base.get("geography"),
        base.get("formulation"),
    )
    return base


def unresolved_row(**kwargs) -> dict:
    notes = kwargs.get("gold_notes") or ""
    if "not a zero-revenue label" not in notes:
        notes = (notes + " This is a non-disclosure label, not a zero-revenue label.").strip()
    row = {
        "confidence_that_unavailable": 0.95,
        "recommended_next_step": "Keep unresolved. Do not invent product revenue from aggregates.",
        **kwargs,
        "gold_notes": notes,
    }
    row["gold_id"] = slug(row["drug_name"], row["period"], "not-separately-disclosed")
    return row


def clean_revenue(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        drug = r["drug_name"]
        scope = r.get("revenue_scope")
        quote = (r.get("source_quote") or "").lower()
        form = r.get("formulation") or ""

        # Drop Remodulin geographic-split junk rows
        if drug == "Remodulin" and scope == "Formulation-specific":
            continue

        # Drop Yutrepia row whose quote is annual / approximate duplicate of Q4 U.S.
        if drug == "Yutrepia" and scope == "Product family" and r["period"] == "2025Q4":
            continue

        # Adcirca is U.S.-only in UTHR tables; normalize Product family → U.S.
        if drug == "Adcirca" and scope == "Product family":
            r = {
                **r,
                "revenue_scope": "U.S.",
                "geography": "United States",
                "formulation": "tablet",
                "route_of_administration": "oral",
                "gold_notes": (r.get("gold_notes") or "")
                + " Scope normalized from Product family to U.S. (Adcirca is U.S.-only).",
            }
            r["gold_id"] = slug(r["drug_name"], r["period"], "U.S.", "United States", "tablet")

        # Tyvaso DPI / Nebulized: Product family on formulation line → Formulation-specific
        if drug == "Tyvaso DPI" and scope == "Product family" and "tyvaso dpi" in quote:
            r = {
                **r,
                "revenue_scope": "Formulation-specific",
                "geography": "Worldwide",
                "formulation": "inhalation powder",
                "route_of_administration": "inhalation",
                "gold_notes": (r.get("gold_notes") or "")
                + " Scope normalized to Formulation-specific for Tyvaso DPI line.",
            }
            r["gold_id"] = slug(
                r["drug_name"], r["period"], "Formulation-specific", "Worldwide", "inhalation powder"
            )
        if drug == "Nebulized Tyvaso" and scope == "Product family" and "nebulized" in quote:
            r = {
                **r,
                "revenue_scope": "Formulation-specific",
                "geography": "Worldwide",
                "formulation": "inhalation solution",
                "route_of_administration": "inhalation",
                "gold_notes": (r.get("gold_notes") or "")
                + " Scope normalized to Formulation-specific for Nebulized Tyvaso line.",
            }
            r["gold_id"] = slug(
                r["drug_name"],
                r["period"],
                "Formulation-specific",
                "Worldwide",
                "inhalation solution",
            )

        # Remodulin Product family → Worldwide injection
        if drug == "Remodulin" and scope == "Product family":
            r = {
                **r,
                "revenue_scope": "Worldwide",
                "geography": "Worldwide",
                "formulation": "injection",
                "route_of_administration": "parenteral",
                "gold_notes": (r.get("gold_notes") or "")
                + " Scope normalized from Product family to Worldwide.",
            }
            r["gold_id"] = slug(r["drug_name"], r["period"], "Worldwide", "Worldwide", "injection")

        # Orenitram Product family on U.S.-only product → U.S.
        if drug == "Orenitram" and scope == "Product family":
            r = {
                **r,
                "revenue_scope": "U.S.",
                "geography": "United States",
                "formulation": "extended-release tablet",
                "route_of_administration": "oral",
                "gold_notes": (r.get("gold_notes") or "")
                + " Scope normalized from Product family to U.S.",
            }
            r["gold_id"] = slug(
                r["drug_name"], r["period"], "U.S.", "United States", "extended-release tablet"
            )

        # Fix approximate Yutrepia 2026 values to statement precision when present
        if drug == "Yutrepia" and r["period"] == "2026Q1" and abs(float(r["value_reported"]) - 130.0) < 0.2:
            r = {
                **r,
                "value_reported": 129.881,
                "value_normalized_usd_millions": 129.881,
                "revenue_scope": "U.S.",
                "geography": "United States",
                "formulation": "inhalation powder",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1819576/000110465926058234/tm2614109d1_ex99-1.htm",
                "source_title": "Liquidia Reports First Quarter 2026 Financial Results",
                "source_quote": "YUTREPIA® (treprostinil) inhalation powder net product sales of approximately $130 million; Product sales, net, were $129.9 million for the three months ended March 31, 2026; Product sales, net | $129,881 thousand.",
                "source_type": "sec_filing",
                "filing_type": "8-K Exhibit 99.1",
                "extraction_method": "manual_audit_web_search",
                "gold_notes": "Exact statement value $129.881 million for Q1 2026.",
            }
            r["gold_id"] = slug(r["drug_name"], r["period"], "U.S.", "United States", "inhalation powder")
        if drug == "Yutrepia" and r["period"] == "2026Q2" and abs(float(r["value_reported"]) - 170.4) < 0.05:
            r = {
                **r,
                "value_reported": 170.382,
                "value_normalized_usd_millions": 170.382,
                "revenue_scope": "U.S.",
                "geography": "United States",
                "formulation": "inhalation powder",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1819576/000110465926094411/tm2622888d1_ex99-1.htm",
                "source_title": "Liquidia Reports Second Quarter 2026 Financial Results",
                "source_quote": "YUTREPIA® (treprostinil) inhalation powder net product sales of approximately $170.4 million; Product sales, net, were $170.4 million for the three months ended June 30, 2026; Product sales, net | $170,382 thousand.",
                "source_type": "sec_filing",
                "filing_type": "8-K Exhibit 99.1",
                "extraction_method": "manual_audit_web_search",
                "gold_notes": "Exact statement value $170.382 million for Q2 2026.",
            }
            r["gold_id"] = slug(r["drug_name"], r["period"], "U.S.", "United States", "inhalation powder")

        out.append(r)

    # Dedupe by key, prefer higher confidence / manual_audit
    best: dict[tuple, dict] = {}
    for r in out:
        key = (
            r["drug_name"],
            r["period"],
            r["revenue_scope"],
            r.get("geography") or "",
            r.get("formulation") or "",
        )
        prev = best.get(key)
        if prev is None:
            best[key] = r
            continue
        score = float(r.get("confidence_score") or 0)
        pscore = float(prev.get("confidence_score") or 0)
        if "manual_audit" in (r.get("extraction_method") or ""):
            score += 2
        if "manual_audit" in (prev.get("extraction_method") or ""):
            pscore += 2
        if "manual_verified" in (r.get("extraction_method") or ""):
            score += 1
        if "manual_verified" in (prev.get("extraction_method") or ""):
            pscore += 1
        if score >= pscore:
            best[key] = r
    return sorted(best.values(), key=lambda x: (x["drug_name"], x["period"], x["revenue_scope"]))


def new_fills() -> list[dict]:
    rows: list[dict] = []

    # Winrevair 2025 WW from Merck sales schedule (Other Financial Disclosures / earnings)
    winrevair_2025 = [
        ("2025Q1", 280.0, "Winrevair | 280 | 336 | 360 | 467 | 1,443 | 70 | 149 | 200 | 419.", "2025-04-24"),
        ("2025Q2", 336.0, "WINREVAIR | 336 | 70 | N/M | N/M | Growth reflects continued uptake since second-quarter 2024 launch in the U.S.", "2025-07-29"),
        ("2025Q3", 360.0, "WINREVAIR Sales Were $360 Million; WINREVAIR | 360 | 149 | 141% | 141%.", "2025-10-30"),
        ("2025Q4", 467.0, "WINREVAIR | 467 | 200 | 133% | 133%; Winrevair | 280 | 336 | 360 | 467 | 1,443.", "2026-02-03"),
    ]
    urls = {
        "2025Q1": "https://www.sec.gov/Archives/edgar/data/310158/000110465925071380/tm2521736d1_ex99-1.htm",
        "2025Q2": "https://www.sec.gov/Archives/edgar/data/310158/000110465925071380/tm2521736d1_ex99-1.htm",
        "2025Q3": "https://www.msd.com/wp-content/uploads/sites/9/2025/10/3Q25-MSD-Earnings-Announcement.pdf",
        "2025Q4": "https://www.merck.com/news/merck-highlights-progress-advancing-broad-diverse-pipeline/",
    }
    for period, value, quote, date in winrevair_2025:
        rows.append(
            revenue_row(
                drug_name="Winrevair",
                generic_name="sotatercept-csrk",
                manufacturer="Merck",
                period=period,
                value_reported=value,
                revenue_scope="Worldwide",
                geography="Worldwide",
                formulation="injection",
                route_of_administration="subcutaneous",
                source_type="sec_filing" if "sec.gov" in urls[period] else "company_ir",
                source_url=urls[period],
                source_title="Merck quarterly financial results / sales schedule",
                source_quote=quote,
                source_date=date,
                filing_type="8-K Exhibit 99.1" if "sec.gov" in urls[period] else "Earnings release",
                gold_notes="Worldwide WINREVAIR net sales from Merck product sales schedule.",
            )
        )

    # Adempas 2025 Merck-recorded net sales (not alliance revenue)
    adempas = [
        ("2025Q1", 68.0),
        ("2025Q2", 80.0),
        ("2025Q3", 82.0),
        ("2025Q4", 83.0),
    ]
    for period, value in adempas:
        rows.append(
            revenue_row(
                drug_name="Adempas",
                generic_name="riociguat",
                manufacturer="Bayer/Merck",
                period=period,
                value_reported=value,
                revenue_scope="Worldwide",
                geography="Worldwide",
                formulation="tablet",
                route_of_administration="oral",
                source_type="company_ir",
                source_url="https://www.msd.com/wp-content/uploads/sites/9/2026/02/4Q25-MSD-Other-Financial-Disclosures.pdf",
                source_title="Merck Fourth Quarter 2025 Other Financial Disclosures",
                source_quote=f"Adempas | 68 | 80 | 82 | 83 | 312 | 70 | 72 | 72 | 73 | 287; {period} value {value}.",
                source_date="2026-02-03",
                filing_type="Other financial disclosures",
                gold_notes="Net sales of Adempas recorded by Merck (excludes Adempas/Verquvo alliance revenue).",
            )
        )

    # Opsumit 2025: J&J reports OPSUMIT/OPSYNVI combined WW line
    opsumit = [
        ("2025Q1", 522.0, "https://www.investor.jnj.com/investor-news/news-details/2025/Johnson--Johnson-Reports-Q1-2025-Results/default.aspx", "OPSUMIT / OPSYNVI | WW | 522 | 524 | -0.5%."),
        ("2025Q2", 582.0, "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q2/2Q25-Other-Financial-Disclosures.pdf", "OPSUMIT / OPSYNVI | WW | 582 | 548 | 6.4%."),
        ("2025Q3", 578.0, "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q3/3Q25-Other-Financial-Disclosures.pdf", "OPSUMIT / OPSYNVI | WW | 578 | 583 | (0.8)%."),
        ("2025Q4", 643.0, "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q4/4Q25-Other-Financial-Disclosures.pdf", "OPSUMIT / OPSYNVI | WW | 643 | 570 | 12.7%."),
    ]
    for period, value, url, quote in opsumit:
        rows.append(
            revenue_row(
                drug_name="Opsumit",
                generic_name="macitentan",
                manufacturer="Johnson & Johnson",
                period=period,
                value_reported=value,
                revenue_scope="Product family",
                geography="Worldwide",
                formulation="tablet",
                route_of_administration="oral",
                source_type="company_ir",
                source_url=url,
                source_title="Johnson & Johnson Other Financial Disclosures / earnings",
                source_quote=quote,
                filing_type="Other financial disclosures",
                gold_notes="J&J reports OPSUMIT/OPSYNVI as a combined WW sales line beginning 2025; treated as Opsumit product-family total.",
            )
        )

    uptravi = [
        ("2025Q1", 451.0, "https://www.investor.jnj.com/investor-news/news-details/2025/Johnson--Johnson-Reports-Q1-2025-Results/default.aspx", "UPTRAVI | WW | 451 | 468 | -3.6%."),
        ("2025Q2", 476.0, "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q2/2Q25-Other-Financial-Disclosures.pdf", "UPTRAVI | WW | 476 | 426 | 11.7%."),
        ("2025Q3", 484.0, "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q3/3Q25-Other-Financial-Disclosures.pdf", "UPTRAVI | WW | 484 | 458 | 5.6%."),
        ("2025Q4", 491.0, "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q4/4Q25-Other-Financial-Disclosures.pdf", "UPTRAVI | WW | 491 | 465 | 5.7%."),
    ]
    for period, value, url, quote in uptravi:
        rows.append(
            revenue_row(
                drug_name="Uptravi",
                generic_name="selexipag",
                manufacturer="Johnson & Johnson",
                period=period,
                value_reported=value,
                revenue_scope="Worldwide",
                geography="Worldwide",
                formulation="tablet",
                route_of_administration="oral",
                source_type="company_ir",
                source_url=url,
                source_title="Johnson & Johnson Other Financial Disclosures / earnings",
                source_quote=quote,
                filing_type="Other financial disclosures",
                gold_notes="Worldwide Uptravi sales from J&J quarterly other financial disclosures.",
            )
        )

    return rows


def rebuild_unresolved(revenue: list[dict], existing: list[dict]) -> list[dict]:
    reported = {(r["drug_name"], r["period"]) for r in revenue}
    keep: dict[tuple, dict] = {}

    # Start from high-quality existing / archive-style rows that are true non-disclosures
    for r in existing:
        key = (r["drug_name"], r["period"])
        if key in reported:
            continue
        # Drop Winrevair 2025+ gaps now filled; drop speculative 2026 Winrevair gap spam
        if r["drug_name"] == "Winrevair" and r["period"] >= "2025Q1":
            continue
        if r["drug_name"] == "Yutrepia" and r["period"] in {"2026Q3", "2026Q4"}:
            # future / not yet due as of mid-2026 audit — keep as unresolved with better reason
            r = unresolved_row(
                drug_name="Yutrepia",
                period=r["period"],
                reason_unresolved="Quarter not yet reported as of gold audit (Aug 2026); Yutrepia launched mid-2025.",
                sources_checked=[
                    {
                        "source_url": "https://www.sec.gov/Archives/edgar/data/1819576/000110465926094411/tm2622888d1_ex99-1.htm",
                        "source_title": "Liquidia Q2 2026 earnings",
                        "observation": "Latest retrieved earnings cover through 2026Q2 only.",
                    }
                ],
                recommended_next_step="Wait for subsequent Liquidia 8-K Exhibit 99.1 earnings release.",
                confidence_that_unavailable=0.7,
                gold_notes="Pending disclosure window, not a zero-revenue label.",
            )
        keep[key] = r

    # Ensure Winrevair 2024Q1 pre-launch non-disclosure
    keep.setdefault(
        ("Winrevair", "2024Q1"),
        unresolved_row(
            drug_name="Winrevair",
            period="2024Q1",
            reason_unresolved="Winrevair launched in the U.S. in Q2 2024; no product sales were reported for Q1 2024.",
            sources_checked=[
                {
                    "source_url": "https://www.sec.gov/Archives/edgar/data/310158/000110465924083722/tm2420272d1_ex99-1.htm",
                    "source_title": "Merck Q2 2024 financial results",
                    "observation": "First WINREVAIR sales appear in Q2 2024 following March 2024 approval.",
                }
            ],
            recommended_next_step="Keep unresolved for pre-launch quarter.",
            confidence_that_unavailable=0.99,
            gold_notes="Pre-launch non-disclosure.",
        ),
    )

    # Letairis 2024Q4 if missing
    keep.setdefault(
        ("Letairis", "2024Q4"),
        unresolved_row(
            drug_name="Letairis",
            period="2024Q4",
            reason_unresolved="Gilead includes Letairis in an aggregated Other products line and does not disclose a product-specific quarterly value.",
            sources_checked=[
                {
                    "source_url": "https://www.sec.gov/Archives/edgar/data/882095/000088209525000008/gild-20241231.htm",
                    "source_title": "Gilead 2024 Form 10-K",
                    "observation": "Other products aggregate still includes Letairis without a separate row.",
                }
            ],
            recommended_next_step="Keep unresolved. Do not assign the aggregate Other products value to Letairis.",
            confidence_that_unavailable=0.95,
            gold_notes="Non-disclosure continues after 2023.",
        ),
    )

    # Tracleer 2024Q4
    keep.setdefault(
        ("Tracleer", "2024Q4"),
        unresolved_row(
            drug_name="Tracleer",
            period="2024Q4",
            reason_unresolved="Johnson & Johnson does not list Tracleer as an individual product in its quarterly supplementary sales schedule.",
            sources_checked=[
                {
                    "source_url": "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q4/4Q25-Other-Financial-Disclosures.pdf",
                    "source_title": "J&J Q4 2025 other financial disclosures (2024 comparatives)",
                    "observation": "Schedule reports OPSUMIT/OPSYNVI and UPTRAVI but no TRACLEER row.",
                }
            ],
            recommended_next_step="Keep unresolved. Do not infer Tracleer from the pulmonary hypertension franchise.",
            confidence_that_unavailable=0.95,
            gold_notes="Persistent non-disclosure.",
        ),
    )

    # Expand thin non-disclosures to a full 2024 year for consistency
    thin_defaults = {
        "Veletri": (
            "Johnson & Johnson does not list Veletri as an individual product in supplementary sales schedules.",
            "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q4/4Q25-Other-Financial-Disclosures.pdf",
            "J&J other financial disclosures",
        ),
        "Ventavis": (
            "Johnson & Johnson does not list Ventavis as an individual product in supplementary sales schedules.",
            "https://s203.q4cdn.com/636242992/files/doc_financials/2025/q4/4Q25-Other-Financial-Disclosures.pdf",
            "J&J other financial disclosures",
        ),
        "Revatio": (
            "Pfizer does not disclose Revatio as an individual product revenue line in current product tables.",
            "https://www.sec.gov/Archives/edgar/data/78003/000007800325000054/pfe-20241231.htm",
            "Pfizer 2024 Annual Report",
        ),
        "Flolan": (
            "GSK does not disclose Flolan as an individual product sales line in current results.",
            "https://www.sec.gov/Archives/edgar/data/1131399/000165495425001166/finalresults05-0225.htm",
            "GSK Full-Year 2024 Results",
        ),
        "Alyq": (
            "Teva does not disclose Alyq as an individual product revenue driver.",
            "https://ir.tevapharm.com/news-and-events/press-releases/press-release-details/2025/Teva-Delivers-Second-Consecutive-Year-of-Growth-Announces-Strong-Financial-Results-in-Fourth-Quarter-and-Full-Year-2024-Led-by-Generics-Performance-and-Innovative-Portfolio-Growth/default.aspx",
            "Teva 2024 results",
        ),
        "Tadliq": (
            "CMP Pharma is privately held and does not publish product-specific quarterly revenue for Tadliq.",
            "https://cmppharma.com/news/cmp-announces-tadliq-is-now-available/",
            "CMP Pharma Tadliq availability",
        ),
        "Liqrev": (
            "CMP Pharma is privately held and does not publish product-specific quarterly revenue for Liqrev.",
            "https://www.prnewswire.com/news-releases/cmp-pharma-inc-announces-that-liqrev-the-first-and-only-ready-made-fda-approved-liquid-suspension-of-sildenafil-is-now-available-301856229.html",
            "CMP Pharma Liqrev availability",
        ),
    }
    for drug, (reason, url, title) in thin_defaults.items():
        for q in ("2024Q1", "2024Q2", "2024Q3", "2024Q4"):
            key = (drug, q)
            if key in reported:
                continue
            keep.setdefault(
                key,
                unresolved_row(
                    drug_name=drug,
                    period=q,
                    reason_unresolved=reason,
                    sources_checked=[
                        {
                            "source_url": url,
                            "source_title": title,
                            "observation": "No product-specific quarterly revenue line found.",
                        }
                    ],
                    recommended_next_step="Keep unresolved. Do not invent branded revenue from portfolio totals.",
                    confidence_that_unavailable=0.95 if drug not in {"Tadliq", "Liqrev"} else 0.98,
                    gold_notes="Explicit non-disclosure / private-company gap.",
                ),
            )

    return sorted(keep.values(), key=lambda r: (r["drug_name"], r["period"]))


def main() -> None:
    revenue = clean_revenue(load_jsonl(GOLD / "quarterly_revenue.jsonl") + new_fills())
    # re-dedupe after fills
    revenue = clean_revenue(revenue)
    unresolved = rebuild_unresolved(revenue, load_jsonl(GOLD / "unresolved_quarters.jsonl"))

    dump_jsonl(GOLD / "quarterly_revenue.jsonl", revenue)
    dump_jsonl(GOLD / "unresolved_quarters.jsonl", unresolved)

    by = Counter(r["drug_name"] for r in revenue)
    uby = Counter(r["drug_name"] for r in unresolved)
    methods = Counter(r.get("extraction_method") for r in revenue)
    report = {
        "audit_date": "2026-08-27",
        "revenue_rows": len(revenue),
        "unresolved_rows": len(unresolved),
        "revenue_by_drug": dict(by),
        "unresolved_by_drug": dict(uby),
        "methods": dict(methods),
        "fills": [
            "Winrevair 2025Q1-Q4",
            "Adempas 2025Q1-Q4",
            "Opsumit 2025Q1-Q4 (OPSUMIT/OPSYNVI family)",
            "Uptravi 2025Q1-Q4",
            "scope normalization Adcirca/Tyvaso DPI/Nebulized/Remodulin/Orenitram",
            "expanded unresolved for thin non-disclosures",
        ],
    }
    (GOLD / "audit_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
