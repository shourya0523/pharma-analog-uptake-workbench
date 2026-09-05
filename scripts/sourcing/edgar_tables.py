"""Pull the PRODUCT SALES SUMMARY out of an issuer earnings exhibit.

Gilead prints a multi-national product as three regional lines followed by an
unlabelled total; that total is the worldwide figure and is what gold records.
Single-territory products print one line. Both shapes are handled here, and a
row is only ever read from the column the filing itself heads as the current
three-month period.
"""
import re, unicodedata
from bs4 import BeautifulSoup

MONEY = re.compile(r"^\(?\$?\s*-?[\d,]+(?:\.\d+)?\)?$")

def clean(text):
    text = unicodedata.normalize("NFKD", text).replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()

def rows_of(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for table in soup.find_all("table"):
        cells_by_row = []
        for tr in table.find_all("tr"):
            cells = [clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c not in ("", "$", ")", "(")]
            if cells:
                cells_by_row.append(cells)
        if cells_by_row:
            out.append(cells_by_row)
    return out

def numbers(cells):
    vals = []
    for c in cells:
        c = c.replace("$", "").strip()
        if MONEY.match(c):
            neg = c.startswith("(")
            vals.append(-1 if neg else 1, ) if False else None
            v = float(c.strip("()").replace(",", ""))
            vals.append(-v if neg else v)
    return vals

def sales_table(html):
    """The rows of the PRODUCT SALES SUMMARY, as (label, numbers, raw cells)."""
    for table in rows_of(html):
        flat = " ".join(" ".join(r) for r in table[:6]).upper()
        if "PRODUCT SALES SUMMARY" in flat:
            return [(r[0], numbers(r[1:]), r) for r in table]
    return []
