"""Build the held-out corpus: earnings exhibits from issuers absent from gold.

Every other eval here scores the pipeline on documents gold cites, which makes
it easy to tune until those documents pass and call the result an improvement.
This corpus exists to catch that. Nothing about these four issuers may be
encoded anywhere in the pipeline, so a change that moves gold a lot and this
barely is fitted to gold's corpus rather than to the problem.

That is not hypothetical. A "FIRST QUARTER" heading reader written during this
work lifted gold's documents by 18 rows and turned out to be worth exactly zero
here, even though three quarters of these documents use that phrasing. It was
removed on the strength of that number.

The corpus feeds two gates:

    scripts/eval_period_generalization.py   periods, read from prose and geometry
    scripts/eval_pdf_geometry.py            the same documents printed to PDF

It lives outside the repository because it is fetched, not authored - set
HOLDOUT_DIR to keep it somewhere that survives.

    SEC_CONTACT='project you@example.com' HOLDOUT_DIR=/tmp/holdout \
        python scripts/sourcing/fetch_holdout.py
"""

from __future__ import annotations

# ruff: noqa: BLE001 - a fetch that fails is a document skipped, not a crash
import json
import os
import pathlib
import re
import time
import urllib.request

WORK = pathlib.Path(os.environ.get("HOLDOUT_DIR", "/tmp/holdout"))
DOCS = WORK / "docs"

# EDGAR asks that automated clients identify themselves, and refuses those that
# do not. There is no default: a shared one would be a lie about who is asking.
UA = os.environ.get("SEC_CONTACT")

# Large pharmaceutical filers with no product in seed/gold. They are here to be
# unfamiliar; the moment one of their products enters gold, it leaves this list.
HELD_OUT = {"78003": "Pfizer", "1551152": "AbbVie", "318154": "Amgen", "59478": "EliLilly"}

# Months an issuer reports a quarter in. Filings in other months are 8-Ks about
# something else.
EARNINGS_MONTHS = {1, 2, 4, 5, 7, 8, 10, 11}
PER_ISSUER = 6
EARLIEST = "2022-01-01"


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except Exception:
            time.sleep(2**attempt)
    return b""


def expected_quarter(filed: str) -> str:
    """The quarter an earnings 8-K filed on this date reports.

    This is the answer key, and it is derived from the filing date rather than
    from the document, so it cannot be influenced by how the document reads.
    """
    year, month = int(filed[:4]), int(filed[5:7])
    if month in (1, 2):
        return f"{year - 1}Q4"
    if month in (4, 5):
        return f"{year}Q1"
    if month in (7, 8):
        return f"{year}Q2"
    return f"{year}Q3"


def earnings_filings(cik: str) -> list[tuple[str, str]]:
    """(filing date, accession) for this issuer's 8-Ks, newest first."""
    payload = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"))
    recent = payload["filings"]["recent"]
    return [
        (date, accession.replace("-", ""))
        for form, accession, date in zip(
            recent["form"], recent["accessionNumber"], recent["filingDate"], strict=False
        )
        if form == "8-K" and date >= EARLIEST and int(date[5:7]) in EARNINGS_MONTHS
    ]


def looks_like_an_earnings_exhibit(text: str) -> bool:
    """Revenue prose under a period heading, in a document long enough to be one.

    Deliberately loose. Tightening it towards what these four issuers happen to
    print would defeat the purpose of the corpus.
    """
    return (
        len(text) >= 20_000
        and bool(re.search(r"(months ended|quarter)", text, re.IGNORECASE))
        and bool(re.search(r"revenue|net sales|product sales", text, re.IGNORECASE))
    )


def main() -> int:
    if not UA:
        raise SystemExit("Set SEC_CONTACT, e.g. 'project you@example.com'")
    DOCS.mkdir(parents=True, exist_ok=True)
    manifest = []
    for cik, issuer in HELD_OUT.items():
        taken = 0
        for filed, accession in earnings_filings(cik):
            if taken >= PER_ISSUER:
                break
            base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}"
            listing = get(f"{base}/index.json")
            if not listing:
                continue
            for item in json.loads(listing)["directory"]["item"]:
                name = item["name"]
                if not name.endswith((".htm", ".html")) or accession[:10] in name:
                    continue
                body = get(f"{base}/{name}")
                if not looks_like_an_earnings_exhibit(body.decode("utf-8", "ignore")):
                    continue
                path = DOCS / f"{issuer}-{filed}-{name[:40]}"
                path.write_bytes(body)
                manifest.append({
                    "issuer": issuer,
                    "filed": filed,
                    "expected": expected_quarter(filed),
                    "path": str(path),
                    "url": f"{base}/{name}",
                })
                taken += 1
                break
            time.sleep(0.2)
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(manifest)} held-out earnings exhibits in {DOCS}")
    for entry in manifest:
        print(f"   {entry['issuer']:<10}{entry['filed']}  expect {entry['expected']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
