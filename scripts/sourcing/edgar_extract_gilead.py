"""Read each Gilead earnings exhibit's PRODUCT SALES SUMMARY.

Three rules, each one a defect this dataset has already been bitten by:

* The unit is read from what the exhibit declares beside the table, never
  inferred from the filing date. Gilead moved from thousands to millions during
  the period covered here; a filing that declares neither is refused.
* The period is read from the exhibit's own "Three Months Ended" heading and
  then checked against the filing date. A filing whose headings disagree with
  each other is refused rather than resolved by guessing.
* Only the current-quarter column is read. Every quarter therefore cites the
  filing that reports it as current, never a prior-year comparative.
"""
import json, os, pathlib, re
from bs4 import BeautifulSoup
import edgar_tables as tables

BASE = pathlib.Path(os.environ.get("SOURCING_WORKDIR", "/tmp/gold-sourcing")) / "edgar"
CACHE = BASE / "cache"
DASH = re.compile(r"[–—-]")
QUARTER_END = {3: 1, 6: 2, 9: 3, 12: 4}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
# Gilead split territories two ways early on and three ways later.
# Gilead split territories two ways early, three later, and broke Japan out
# separately for the hepatitis C products.
REGIONS = ("U.S.", "U.S", "International", "Europe", "Japan",
           "Other International", "Other")
FOOTNOTE = re.compile(r"\s*\(\d+\)")
# "December 31," sits on its own header row in the later exhibits.
DATE_LINE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},")
YEAR = re.compile(r"^(19|20)\d{2}$")
REGION_LINE = re.compile(r"-\s*(U\.S\.?|Europe|Japan|Other International|International|Other)$")
THREE_MONTHS = re.compile(r"Three Months Ended\s+([A-Z][a-z]+)\s+(\d{1,2}),?\s*((?:19|20)\d{2})?")


def text_of(html):
    return re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))


def declared_unit(html):
    text = text_of(html)
    spot = text.upper().find("PRODUCT SALES SUMMARY")
    window = text[spot:spot + 400] if spot >= 0 else text
    if re.search(r"\(\s*in thousands", window, re.I):
        return "thousands"
    if re.search(r"\(\s*in millions", window, re.I):
        return "millions"
    return None


def period_of(table, filed):
    """The quarter, from the sales table's own heading, checked against the date.

    The heading is often split across cells - "Three Months Ended" in one row,
    "June 30," in the next, the years in a third - so it is read from the
    table's leading rows joined together rather than from a single string.
    """
    header = " ".join(" ".join(row) for row in table[:4])
    if "three months ended" not in header.lower():
        return None
    month_day = re.search(r"\b([A-Z][a-z]+)\s+(\d{1,2})\b", header)
    year_hit = re.search(r"\b((?:19|20)\d{2})\b", header)
    if not (month_day and year_hit):
        return None
    month = MONTHS.get(month_day.group(1))
    if month not in QUARTER_END:
        return None
    year = int(year_hit.group(1))
    # The release must land after the quarter it reports and within two
    # quarters of it, or the heading and the filing date disagree.
    end = year * 12 + month
    filed_month = int(filed[:4]) * 12 + int(filed[5:7])
    if not 0 < filed_month - end <= 5:
        return None
    return f"{year}Q{QUARTER_END[month]}"


def sales_rows(html):
    """The PRODUCT SALES SUMMARY rows, which may span several HTML tables.

    Long releases break the summary across a page boundary, so the table
    holding "Total product sales" can carry only its tail. Every table that
    prints the summary's own regional lines is therefore taken, in document
    order, and read as one.
    """
    all_tables = tables.rows_of(html)
    parts, seen_total = [], False
    for table in all_tables:
        is_summary = any(
            REGION_LINE.search(DASH.sub("-", r[0])) for r in table if r
        ) or any(r[0].lower().startswith("total product sales") for r in table)
        if is_summary:
            parts.append(table)
            if any(r[0].lower().startswith("total product sales") for r in table):
                seen_total = True
                break
    if not (parts and seen_total):
        return []
    return [row for table in parts for row in table]


def is_number(cell):
    return bool(tables.MONEY.match(cell.replace("$", "").strip()))


def products(table):
    """Worldwide value per product, in the filing's own unit.

    A product sold in several territories prints three regional lines and then
    an unlabelled total line - that total is the worldwide figure. A product
    sold in one territory prints a single line. Nothing else is read.
    """
    out, awaiting, block = {}, None, []
    for cells in table:
        joined = " ".join(cells)
        if ("ended" in joined.lower()
                or all(YEAR.match(c.replace(",", "")) for c in cells)):
            continue
        if is_number(cells[0]):
            values = tables.numbers(cells)
            if awaiting and values:
                # Quote the regional lines with the total, or the quote is a
                # bare row of digits that names no product - unreadable without
                # a hand-written legend, which is what this dataset refuses.
                out[awaiting] = (values[0], block + [cells])
            awaiting, block = None, []
            continue
        label = FOOTNOTE.sub("", cells[0]).strip()
        # Split on the LAST dash: a line like "Revenue share - Symtuza -
        # Europe" is a regional line whose name itself contains a dash. And a
        # footnote marker must not stop a region being recognised, or the
        # regional figure gets recorded as though it were the worldwide one.
        parts = [p.strip() for p in FOOTNOTE.sub("", DASH.sub("-", label)).rsplit("-", 1)]
        if len(parts) == 2 and parts[1] in REGIONS:
            # The unlabelled total follows the last regional line, and how many
            # regional lines there are changed over the years - so remember the
            # name on every one and let the total line consume the last.
            if parts[0].lower().startswith(("total", "other")):
                awaiting, block = None, []
            else:
                block = block + [cells] if awaiting == parts[0] else [cells]
                awaiting = parts[0]
            continue
        awaiting, block = None, []
        values = tables.numbers(cells[1:])
        # Standalone "Other", "Other (3)" and "Other Antiviral" rows are the
        # table's own aggregates, never a product.
        if values and not label.lower().startswith(("total", "other")):
            out[label] = (values[0], [cells])
    return out


def header_rows(table):
    """The table's own period heading, so a quote says what its columns are.

    Without it a quote is a row of digits whose meaning lives in the reader's
    head - the legend problem this dataset already spent a pass removing. The
    heading is the filing's own text, so quoting it adds nothing of ours.
    """
    out = []
    for cells in table[:6]:
        joined = " ".join(cells)
        is_head = ("months ended" in joined.lower() or "year ended" in joined.lower()
                   or all(YEAR.match(c.replace(",", "")) for c in cells)
                   or DATE_LINE.match(joined))
        if is_head:
            out.append(cells)
        elif out:
            break
    return out


def main():
    rows, skipped = [], []
    for f in json.load(open(BASE / "gilead_earnings.json")):
        html = (CACHE / f"882095-{f['acc']}-{f['doc']}").read_bytes().decode("utf-8", "ignore")
        table = sales_rows(html)
        period, unit = period_of(table, f["date"]), declared_unit(html)
        if not (table and period and unit):
            skipped.append((f["date"], f["url"], bool(table), period, unit))
            continue
        found = products(table)
        head = header_rows(table)
        rows.append({"filed": f["date"], "url": f["url"], "period": period, "unit": unit,
                     "products": {k: v[0] for k, v in found.items()},
                     "quotes": {k: " | ".join(" | ".join(row) for row in head + v[1])
                                for k, v in found.items()}})
    json.dump(rows, open(BASE / "gilead_quarters.json", "w"), indent=1)
    print(f"{len(rows)} quarters parsed, {len(skipped)} refused")
    for s in skipped:
        print("   refused:", s[0], "table" if s[2] else "NO TABLE", s[3], s[4])
    per = sorted(r["period"] for r in rows)
    print("range:", per[0], "->", per[-1], "| duplicate periods:", len(per) - len(set(per)))
    for r in [rows[0], rows[len(rows) // 2], rows[-1]]:
        print(f"\n  {r['period']} ({r['unit']}) {r['url'].split('/')[-1]}")
        for k, v in list(r["products"].items())[:10]:
            print(f"     {k:<24} {v:,.0f}")


if __name__ == "__main__":
    main()
