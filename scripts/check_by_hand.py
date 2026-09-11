"""Hold each published figure up against the document it cites.

Not against gold and not against a test: the job's own citation names a URL,
and the figure either appears in that document beside the product's name or it
does not. This reads the run back through the API and then fetches what the
API says it read, so the check is on what a caller was handed.

    python scripts/check_by_hand.py --run <run_id> [--base URL]

Prints, per published quarterly figure, the sentence or row in the cited
document that carries it - or NOT FOUND, which is the line to read twice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

UA = "pharma-analog-research mehr.anand@bitsdime.com"
CACHE = Path("/tmp/evalrun/byhand")


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read().decode())


def fetch(url: str) -> bytes | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / re.sub(r"[^A-Za-z0-9]", "_", url)[-150:]
    if path.exists():
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
    except Exception as exc:  # noqa: BLE001
        print(f"      fetch failed: {exc}")
        return None
    path.write_bytes(raw)
    return raw


def visible_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u200b", " ").replace("&nbsp;", " ").replace("&#160;", " ")
    return re.sub(r"\s+", " ", text)


def spellings(value_reported: float, millions: float) -> list[str]:
    """How the figure could be printed: as reported, in millions, in thousands."""
    out = set()
    for v in (value_reported, millions, millions * 1000):
        if v is None:
            continue
        for fmt in ("{:,.0f}", "{:,.1f}", "{:,.2f}", "{:.0f}", "{:.1f}"):
            s = fmt.format(v)
            s = s.removesuffix(".0")
            out.add(s)
    return sorted(out, key=len, reverse=True)


def check_instance(raw: bytes, drug: str, period_start: str | None, value: float) -> str | None:
    """A tagged fact, checked as a person would: find the context that names
    the product for that period, then the fact filed against it."""
    text = raw.decode("utf-8", errors="replace")
    key = drug.lower().replace(" ", "")
    contexts = []
    for m in re.finditer(r"<(?:xbrli:)?context[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</(?:xbrli:)?context>", text, re.DOTALL):
        cid, body = m.group(1), m.group(2)
        if key not in body.lower().replace(" ", ""):
            continue
        if period_start and period_start not in body:
            continue
        contexts.append(cid)
    for cid in contexts:
        for m in re.finditer(r"<([\w\-]+:[\w\-]+)[^>]*contextRef=\"" + re.escape(cid) + r"\"[^>]*>([^<]+)<", text):
            try:
                v = float(m.group(2).replace(",", ""))
            except ValueError:
                continue
            if abs(v - value) < 1:
                return f"{m.group(1)} contextRef={cid} = {m.group(2)}"
    return None


def printed_sibling(url: str) -> str | None:
    """The human-readable filing beside an extracted instance."""
    if url.endswith("_htm.xml"):
        return url[: -len("_htm.xml")] + ".htm"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    found = missing = 0
    for run_id in args.run:
        run = get_json(f"{args.base}/runs/{run_id}")
        for job in run["jobs"]:
            detail = get_json(f"{args.base}/jobs/{job['id']}")
            drug = job["drug_name"]
            for d in detail.get("datapoints") or []:
                if d.get("validation_status") not in {"auto_pass", "confirmed"}:
                    continue
                if d.get("period_type") != "quarterly":
                    continue
                url = d.get("source_url") or ""
                print(f"\n  {drug} {d['period']}  {d['value_normalized_usd_millions']:,.1f}m "
                      f"via {d.get('extraction_method')}\n      {url}")
                raw = fetch(url) if url else None
                if raw is None:
                    missing += 1
                    print("      NOT FOUND (no document)")
                    continue
                if d.get("extraction_method") == "xbrl_fact":
                    fact = check_instance(raw, drug, None, float(d.get("value_reported") or 0))
                    printed = None
                    sibling = printed_sibling(url)
                    if sibling and (sraw := fetch(sibling)):
                        stext = visible_text(sraw)
                        for s in spellings(None, d["value_normalized_usd_millions"]):
                            for m in re.finditer(r"(?<![\d,.])" + re.escape(s) + r"(?![\d,])", stext):
                                window = stext[max(0, m.start() - 300): m.end() + 60]
                                if drug.lower().split()[0] in window.lower():
                                    printed = stext[max(0, m.start() - 90): m.end() + 40].strip()
                                    break
                            if printed:
                                break
                    if fact:
                        found += 1
                        print(f"      tagged fact: {fact}")
                    else:
                        missing += 1
                        print("      NOT FOUND as a tagged fact for that product in the instance")
                    print(f"      printed beside the name: {'...' + printed + '...' if printed else 'NOT FOUND in ' + (sibling or 'n/a')}")
                    continue
                text = visible_text(raw)
                low = text.lower()
                hit = None
                for s in spellings(d.get("value_reported"), d["value_normalized_usd_millions"]):
                    for m in re.finditer(re.escape(s), text):
                        window = low[max(0, m.start() - 400): m.end() + 120]
                        if drug.lower().split()[0] in window:
                            hit = text[max(0, m.start() - 110): m.end() + 40]
                            break
                    if hit:
                        break
                if hit:
                    found += 1
                    print(f"      in the document: ...{hit.strip()}...")
                else:
                    missing += 1
                    print(f"      NOT FOUND beside {drug!r} in the cited document")
    print(f"\n  {found} published figures found in their cited documents, {missing} not")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
