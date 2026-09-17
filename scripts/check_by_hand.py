"""Hold each published figure up against the document it cites.

Not against gold and not against a test: the job's own citation names a URL,
and this fetches it. Three things are checked, because a citation claims three
and they fail separately:

* the **quote** the row stored is a span of that document. A row whose figure
  sits near the product name passes the value check with a quote nobody wrote
  down from the page.
* the **value** is printed beside the product's name.
* for a tagged fact, the fact is filed against that product **for that
  period**. Period attribution is the defect class
  `docs/research/sec-table-period-context.md` exists for, and a checker that
  passes any period cannot see it.

Quarterly and annual rows are both checked: an annual figure is published to
the same analyst with the same citation.

    SEC_USER_AGENT="name you@example.com" python scripts/check_by_hand.py \
        --run <run_id> [--base URL]

EDGAR requires a User-Agent naming a real contact, and refuses requests
without one. It is read from the environment rather than stored here so the
script carries nobody's address.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

CACHE = Path("/tmp/evalrun/byhand")


def user_agent() -> str:
    """The contact EDGAR requires, from `SEC_USER_AGENT`.

    The same variable the application reads, so a shell configured to run the
    pipeline can run this without a second setting.
    """
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise SystemExit(
            "SEC_USER_AGENT is not set. EDGAR refuses requests without a "
            "User-Agent naming a real contact; export it as "
            'SEC_USER_AGENT="Your Name you@example.com" and run again.'
        )
    return ua


def _read(url: str, *, attempts: int = 5) -> bytes:
    """One HTTP read, retried: the API is busy running jobs and EDGAR is slow."""
    delay = 5.0
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            print(f"      ({type(exc).__name__} on {url[-60:]}; retrying in {delay:.0f}s)")
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def get_json(url: str) -> dict:
    return json.loads(_read(url).decode())


def fetch(url: str) -> bytes | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / re.sub(r"[^A-Za-z0-9]", "_", url)[-150:]
    if path.exists():
        return path.read_bytes()
    try:
        raw = _read(url)
    except Exception as exc:  # noqa: BLE001
        print(f"      fetch failed: {exc}")
        return None
    path.write_bytes(raw)
    return raw


def visible_text(raw: bytes) -> str:
    """What a reader sees: markup removed, entities resolved, spacing collapsed.

    Entities are unescaped after the tags are stripped, not before, so an
    escaped `&lt;` in the prose cannot become a tag on the way through. They
    have to be resolved at all because a stored quote holds the character a
    filer's `&#8212;` stands for, not the escape.
    """
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\u200b", " ").replace("\xa0", " ")
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


# A period label as the pipeline writes it: a calendar quarter, or a year.
_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_YEAR = re.compile(r"^(?:FY)?(\d{4})$")
# A filer on a 52/53-week calendar ends its quarter up to a few days either
# side of the calendar quarter end, so the window is not a date equality.
CALENDAR_DRIFT = dt.timedelta(days=10)


def period_window(period: str) -> tuple[dt.date, dt.date] | None:
    """The calendar span a period label stands for, or None if unrecognised.

    Returned as the first and last day. A context whose own end date falls
    outside this window by more than the 52/53-week drift is a different
    period, whatever else about the fact matches.
    """
    if m := _QUARTER.match(period.strip()):
        year, quarter = int(m.group(1)), int(m.group(2))
        start = dt.date(year, 3 * quarter - 2, 1)
        end = dt.date(year + (quarter == 4), (3 * quarter) % 12 + 1, 1) - dt.timedelta(days=1)
        return start, end
    if m := _YEAR.match(period.strip()):
        year = int(m.group(1))
        return dt.date(year, 1, 1), dt.date(year, 12, 31)
    return None


def _context_covers(body: str, window: tuple[dt.date, dt.date]) -> bool:
    """Whether an XBRL context's own dates are the period we asked about.

    Both ends are compared, so a nine-month context that happens to end in the
    right quarter is not mistaken for the quarter - that mistake is the one
    this check exists to catch.
    """
    dates = {}
    for tag in ("startDate", "endDate", "instant"):
        if m := re.search(rf"<(?:xbrli:)?{tag}>\s*(\d{{4}}-\d{{2}}-\d{{2}})", body):
            dates[tag] = dt.date.fromisoformat(m.group(1))
    start, end = window
    if "instant" in dates:
        return abs(dates["instant"] - end) <= CALENDAR_DRIFT
    if "startDate" not in dates or "endDate" not in dates:
        return False
    return (
        abs(dates["startDate"] - start) <= CALENDAR_DRIFT
        and abs(dates["endDate"] - end) <= CALENDAR_DRIFT
    )


def check_instance(raw: bytes, drug: str, period: str | None, value: float) -> str | None:
    """A tagged fact, checked as a person would: find the context that names
    the product for that period, then the fact filed against it.

    ``period`` is the row's own period label. Passing None drops the period
    check and answers a weaker question - whether the value is tagged against
    the product at all, in any period.
    """
    text = raw.decode("utf-8", errors="replace")
    key = drug.lower().replace(" ", "")
    window = period_window(period) if period else None
    contexts = []
    for m in re.finditer(r"<(?:xbrli:)?context[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</(?:xbrli:)?context>", text, re.DOTALL):
        cid, body = m.group(1), m.group(2)
        if key not in body.lower().replace(" ", ""):
            continue
        if window and not _context_covers(body, window):
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


def names(drug: str, window: str, generic: str | None = None) -> bool:
    """Whether a stretch of the document names the product.

    A document prints the brand; the name a person typed may carry more -
    "Nebulized Calderon" for a document that says "Calderon" - so any word of
    the name long enough to be a name of its own will do. A filer that prints
    the generic - "calderonib products" for Calderon - names it too, so the
    job's generic name counts where the job has one.
    """
    low = window.lower()
    words = [w for name in (drug, generic or "") for w in name.lower().split() if len(w) >= 4]
    return any(word in low for word in words)


def _comparable(text: str) -> str:
    """Text reduced to what two renderings of the same span agree on.

    A quote is taken from a flattened table and stored with the separators the
    flattener used; the document prints the same cells with markup, entities
    and line breaks between them. Comparing on the letters and digits alone is
    what lets one be found in the other.
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def quote_is_in_document(quote: str, text: str) -> bool:
    """Whether the row's stored quote is a span of the cited document.

    This is the check that makes a citation a citation. A value found near the
    product name says the document mentions both; only the quote says the row
    was read off this page.
    """
    quote = (quote or "").strip()
    if not quote:
        return False
    return _comparable(quote) in _comparable(text)


def printed_sibling(url: str) -> str | None:
    """The human-readable filing beside an extracted instance.

    An inline filing's instance is extracted from the document itself, so the
    document is the instance's own name without the suffix. An older instance
    was filed as a separate file, and the document beside it is whatever the
    issuer's submissions index names as the filing's primary document.
    """
    if url.endswith("_htm.xml"):
        return url[: -len("_htm.xml")] + ".htm"
    m = re.search(r"/edgar/data/(\d+)/(\d{10})(\d{2})(\d{6})/", url)
    if not m:
        return None
    cik, folder = m.group(1), url[: m.end()]
    accession = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
    submissions = fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if not submissions:
        return None
    index = json.loads(submissions)
    shards = [index.get("filings", {}).get("recent", {})]
    for older in index.get("filings", {}).get("files", []):
        shard = fetch("https://data.sec.gov/submissions/" + older["name"])
        if shard:
            shards.append(json.loads(shard))
    for shard in shards:
        for number, primary in zip(shard.get("accessionNumber", []), shard.get("primaryDocument", [])):
            if number == accession and primary:
                return folder + primary
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--period-type", action="append", default=None,
                    help="Only rows of this period_type. Repeatable. The "
                         "default is every published row, quarterly and annual.")
    args = ap.parse_args()

    # One tally per thing a citation claims, because they fail separately and
    # a single number hides which one did.
    checked = 0
    failures = {"quote": 0, "value": 0, "period": 0, "document": 0}
    for run_id in args.run:
        run = get_json(f"{args.base}/runs/{run_id}")
        for job in run["jobs"]:
            detail = get_json(f"{args.base}/jobs/{job['id']}")
            drug = job["drug_name"]
            generic = detail.get("generic_name")
            for d in detail.get("datapoints") or []:
                if d.get("validation_status") not in {"auto_pass", "confirmed"}:
                    continue
                if args.period_type and d.get("period_type") not in args.period_type:
                    continue
                checked += 1
                url = d.get("source_url") or ""
                print(f"\n  {drug} {d['period']} ({d.get('period_type')})"
                      f"  {d['value_normalized_usd_millions']:,.1f}m "
                      f"via {d.get('extraction_method')}\n      {url}")
                raw = fetch(url) if url else None
                if raw is None:
                    failures["document"] += 1
                    print("      NOT FOUND (no document)")
                    continue

                if d.get("extraction_method") == "xbrl_fact":
                    reported = d.get("value_reported")
                    if reported is None:
                        # Searching for 0.0 finds a zero somewhere and calls it
                        # the row's figure. A row with no reported value has
                        # nothing to look for.
                        failures["value"] += 1
                        print("      NOT CHECKED: the row stores no value_reported")
                        fact = None
                    else:
                        fact = check_instance(raw, drug, d.get("period"), float(reported))
                    if fact:
                        print(f"      tagged fact for {d['period']}: {fact}")
                    elif reported is not None:
                        failures["period"] += 1
                        loose = check_instance(raw, drug, None, float(reported))
                        if loose:
                            print(f"      WRONG PERIOD: not tagged for {d['period']}, "
                                  f"but tagged in another context: {loose}")
                        else:
                            print("      NOT FOUND as a tagged fact for that product "
                                  "in any period of the instance")
                    # An instance is machine text; the quote and the printed
                    # figure belong to the filing a person reads beside it.
                    sibling = printed_sibling(url)
                    sraw = fetch(sibling) if sibling else None
                    if sraw is None:
                        failures["quote"] += 1
                        print(f"      quote NOT CHECKED: no readable filing "
                              f"beside the instance ({sibling or 'none found'})")
                        continue
                    text = visible_text(sraw)
                    where = sibling
                else:
                    text = visible_text(raw)
                    where = url

                if quote_is_in_document(d.get("source_quote") or "", text):
                    print("      quote is a span of the cited document")
                else:
                    failures["quote"] += 1
                    print(f"      QUOTE NOT IN {where}: "
                          f"{(d.get('source_quote') or '(empty)')[:110]!r}")

                if d.get("extraction_method") == "xbrl_fact":
                    continue
                hit = None
                low = text.lower()
                for s in spellings(d.get("value_reported"), d["value_normalized_usd_millions"]):
                    for m in re.finditer(re.escape(s), text):
                        window = low[max(0, m.start() - 400): m.end() + 120]
                        if names(drug, window, generic):
                            hit = text[max(0, m.start() - 110): m.end() + 40]
                            break
                    if hit:
                        break
                if hit:
                    print(f"      in the document: ...{hit.strip()}...")
                else:
                    failures["value"] += 1
                    print(f"      NOT FOUND beside {drug!r} in the cited document")

    print(f"\n  {checked} published figures checked")
    for what, count in failures.items():
        print(f"      {count} failed the {what} check")
    return 0 if not any(failures.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
