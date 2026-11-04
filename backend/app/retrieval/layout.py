"""Read a product's quarterly sales from a document the grid and text readers cannot.

Some documents print the figure in a layout the other readers do not follow:
a column headed by a numeric date (`Quarter Ended 12/31/24`), a single-product
filer's quarterly table whose row says "Product revenue" rather than the brand,
a pre-HTML filing laid out in fixed-width text, or a PDF. Here the document
itself goes to Gemini, without search: a PDF as a document, an HTML filing as
its tables rendered row by row (cells kept apart, so a column's position
survives) with the prose around the product, a plain-text filing as its own
lines. The model answers with the value as printed, its unit, currency and
kind, and the row label and the value's column headers copied verbatim.

Nothing the model says is taken on trust. Code checks, against the document's
own text:

- the row label is printed, and the value is one of that row's figures;
- each column header is printed, in the stretch of text above the row;
- the period evidence names the quarter (`earnings.names_quarter`): for an
  HTML table, the labels of the grid cell holding the value under that row
  (`app.retrieval.grid`), with the verified headers beside them; otherwise the
  verified headers, or for a sentence, the sentence itself;
- the row is the filer's own product sales, not a royalty, collaboration,
  licence, milestone or alliance line;
- the unit agrees with the scale the row states, or that the text above the
  row declares.

A row that does not name the product - `Product revenue, net` - is a
single-product filer's total. It is accepted only when the caller passes
`single_product_ok`, having established elsewhere that the filer sells this
product alone, and when the model also read it as the filer's total product
revenue.
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import io
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import lxml.html

from app.retrieval.earnings import (
    _NOT_OWN_SALES,
    _SCALES,
    _figure,
    declared_scale,
    names_quarter,
    passages,
    stated_scale,
    value_in_quote,
)
from app.retrieval.gemini_search import QuotaExceeded, is_quota
from app.retrieval.grid import _rows, cells
from app.retrieval.single_product import filing_text
from app.retrieval.verify import DEFAULT_USER_AGENT, _flatten, _in_row, _same_number
from app.retrieval.xbrl import quarter_end, sec_get

# What is sent of an HTML or text document, in characters. A results exhibit
# fits whole; an annual report does not, and is cut to its tables and the
# prose around the product.
MAX_INPUT_CHARS = 200_000
# Gemini takes an inline document up to a request size; a larger PDF is declined.
MAX_PDF_BYTES = 18_000_000
# How far above a row its column headers and scale declaration may be printed,
# in characters of flattened text. A table's header block and its title sit
# within this of its rows; a different table's headers usually do not.
HEADER_WINDOW = 3000
# A plain-text filing is sent as windows around the lines that matter.
TEXT_WINDOW_BEFORE, TEXT_WINDOW_AFTER = 4000, 6000
# Prose kept before a table as its heading, when tables are rendered.
TABLE_HEADING_CHARS = 300

_LONGER = re.compile(r"six months|nine months|twelve months|full[- ]year|year[- ]to[- ]date|\bytd\b|year ended",
                     re.IGNORECASE)
_QUARTER_WORDS = re.compile(r"three months|3 months|quarter|\bq[1-4]\b|\b[1-4]q", re.IGNORECASE)
_YEAR4 = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")
# Anything in a column header that says which period the column is.
_PERIOD_WORDS = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|months|quarter|\bq[1-4]\b|"
                           r"\b[1-4]q|year|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE)

PROMPT = """\
The document {how} is a company's filing or report. Find the net sales the
company itself reports for the product "{product}" for the three months ended
{end} ({label}).

The figure may sit in a table whose columns are headed by dates written as
numbers (e.g. "Quarter Ended 3/31/24"), in a quarterly results table of an
annual report, or in a sentence. If the company sells only this product, its
row may say "Product revenue" or "Net product sales" without naming it; then
give that row and say so in "filer_total_product_revenue". If the company
prints U.S., international and total lines, give the total.
If the only line is a royalty, collaboration, licence, milestone or alliance
line on another company's sales, report it with that kind.

Answer with one JSON object and nothing else:
{{"found": true or false,
  "value": <number exactly as printed, without currency signs or commas>,
  "unit": "<thousands | millions | billions | units>",
  "currency": "<ISO code>",
  "kind": "<product_sales | royalty | collaboration | other>",
  "row_label": "<the row's label exactly as printed, or the whole sentence if the figure is in a sentence>",
  "column_headers": ["<each header printed above the value's column, outermost first, exactly as
                      printed, e.g. "Three Months Ended" and "June 30, 2024"; only the headers of
                      this one column; empty if the figure is in a sentence>"],
  "filer_total_product_revenue": <true if the row is the company's total product revenue and does
                                  not name the product, else false>}}
"""


@dataclass
class LayoutReading:
    """What the layout reader found in one document, and what checking it showed."""

    url: str
    status: str  # accepted | not_found | row_not_in_document | value_not_in_row | header_not_in_document | wrong_period | not_own_sales | generic_row | unit_conflict | unreadable | not_a_quarter | error
    value: float | None = None  # in units of currency
    currency: str | None = None
    row_label: str | None = None
    headers: list[str] = field(default_factory=list)
    unit: str | None = None
    kind: str | None = None
    generic_row: bool = False  # the row does not name the product
    method: str | None = None  # pdf | html | text
    cell: str | None = None  # the grid cell that carried the period, for an HTML table
    reason: str = ""


# ---------------------------------------------------------------- fetching


def fetch(url: str, *, get: Callable = sec_get) -> tuple[bytes, str]:
    """`(content, content type)`. SEC documents through `sec_get`, with the
    User-Agent SEC requires; any other host with the same declared agent the
    verifier uses."""
    if re.match(r"https?://(?:[a-z0-9-]+\.)*sec\.gov/", url, re.IGNORECASE):
        response = get(url)
    else:
        agent = os.environ.get("SEC_USER_AGENT") or DEFAULT_USER_AGENT
        response = httpx.get(url, headers={"User-Agent": agent}, follow_redirects=True, timeout=120)
        response.raise_for_status()
    headers = getattr(response, "headers", None) or {}
    return response.content, str(headers.get("content-type", ""))


def kind_of(content: bytes, content_type: str, url: str) -> str:
    """`pdf`, `html` or `text`, from the bytes first and the declared type after."""
    if content[:5] == b"%PDF-" or "pdf" in content_type.lower():
        return "pdf"
    head = content[:4000].lower()
    body = content[:200_000].lower()
    if b"<html" in head or (b"<table" in body and b"<tr" in body):
        return "html"
    if "html" in content_type.lower() or url.lower().endswith((".htm", ".html")):
        return "html"
    return "text"


def pdf_text(content: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def document_text(content: bytes, kind: str) -> str:
    """The text the answer is checked against."""
    if kind == "pdf":
        return pdf_text(content)
    if kind == "html":
        return filing_text(content)
    return content.decode("utf-8", "ignore")


# ---------------------------------------------------------------- what is sent


def render_html(content: bytes, product: str, end: dt.date, cap: int = MAX_INPUT_CHARS) -> str:
    """The document with each table rendered one row per line, cells split
    by ` | `, between `[TABLE]` and `[END TABLE]` and headed by the prose just
    above it.

    When it all fits under `cap` it is sent whole. Otherwise the tables that
    name the product come first, then those whose headers say a quarter of the
    target year, then those whose headers name its end month, then the prose
    around the product; within that order, whatever fits, put back in
    document order.
    """
    root = lxml.html.fromstring(content)
    tables = [t for t in root.iter("table") if not any(a.tag == "table" for a in t.iterancestors())]
    rendered = []
    for i, table in enumerate(tables):
        rows = [" | ".join(text for text, _, _ in row if text and text != "$") for row in _rows(table)]
        rendered.append("\n".join(r for r in rows if r))
        marker = lxml.html.Element("p")
        marker.text = f" @@TABLE{i}@@ "
        marker.tail = table.tail
        table.getparent().replace(table, marker)
    flat = re.sub(r"\s+", " ", " ".join(root.itertext()).replace("\xa0", " ")).strip()
    parts = re.split(r"@@TABLE(\d+)@@", flat)
    blocks: list[tuple[int, int, str]] = []  # (priority, order, text); lower priority is kept first
    name = product.split("/")[0].strip().lower()
    prose = parts[0::2]
    for k, index in enumerate(parts[1::2]):
        body = rendered[int(index)]
        if not body:
            continue
        heading = prose[k][-TABLE_HEADING_CHARS:].strip()
        top = heading + " " + " ".join(body.splitlines()[:4])
        if name in body.lower():
            priority = 0
        elif names_quarter(top, end) and _QUARTER_WORDS.search(top):
            priority = 1
        elif names_quarter(top, end):
            priority = 2
        else:
            priority = 3
        blocks.append((priority, 2 * k + 1, f"[TABLE] {heading}\n{body}\n[END TABLE]"))
    whole = "\n\n".join(text for _, _, text in sorted(blocks + [(3, 2 * k, p) for k, p in enumerate(prose)],
                                                      key=lambda b: b[1]) if text.strip())
    if len(whole) <= cap:
        return whole
    # Cut down, the tables come first; the prose around the product gets what
    # a quarter of the budget holds.
    whole = "\n\n".join(text for _, _, text in sorted(blocks, key=lambda b: b[1]))
    around = passages(" ".join(prose), product)[: cap // 4]
    if len(whole) + len(around) <= cap:
        return f"{whole}\n\n[TEXT AROUND THE PRODUCT]\n{around}".strip()
    kept, size = [], len(around)
    for block in sorted(blocks, key=lambda b: (b[0], b[1])):
        if block[0] > 2 or size + len(block[2]) > cap:
            continue
        kept.append(block)
        size += len(block[2])
    body = "\n\n".join(text for _, _, text in sorted(kept, key=lambda b: b[1]))
    return f"{body}\n\n[TEXT AROUND THE PRODUCT]\n{around}".strip()


def excerpt_text(text: str, product: str, end: dt.date, cap: int = MAX_INPUT_CHARS) -> str:
    """A plain-text filing, line layout kept: whole when it fits, otherwise
    windows around the lines naming the product and the lines whose wording
    says a quarter of the target year."""
    if len(text) <= cap:
        return text
    name = product.split("/")[0].strip().lower()
    anchors, offset = [], 0
    for line in text.splitlines(keepends=True):
        low = line.lower()
        if name in low or (_QUARTER_WORDS.search(line) and names_quarter(line, end)):
            anchors.append(offset)
        offset += len(line)
    spans: list[list[int]] = []
    for a in anchors:
        start, stop = max(0, a - TEXT_WINDOW_BEFORE), min(len(text), a + TEXT_WINDOW_AFTER)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], stop)
        else:
            spans.append([start, stop])
    out, size = [], 0
    for start, stop in spans:
        if size + (stop - start) > cap:
            break
        out.append(text[start:stop])
        size += stop - start
    return "\n...\n".join(out)


def contents_for(content: bytes, kind: str, text: str, product: str, end: dt.date, label: str) -> list[dict]:
    """The Interactions API input: the prompt, then the document."""
    end_words = f"{calendar.month_name[end.month]} {end.day}, {end.year}"
    if kind == "pdf":
        prompt = PROMPT.format(how="attached", product=product, end=end_words, label=label)
        return [{"type": "text", "text": prompt},
                {"type": "document", "mime_type": "application/pdf", "data": base64.b64encode(content).decode()}]
    if kind == "html":
        how = "below (its tables rendered one row per line, cells separated by ' | ')"
        body = render_html(content, product, end)
    else:
        how = "below (plain text, line layout as filed)"
        body = excerpt_text(text, product, end)
    prompt = PROMPT.format(how=how, product=product, end=end_words, label=label)
    return [{"type": "text", "text": f"{prompt}\nDocument:\n{body}"}]


# ---------------------------------------------------------------- asking


def gemini_layout_reader(contents: list[dict], *, client: Any = None, model: str | None = None) -> dict[str, Any]:
    """Ask Gemini, without search, about the document in `contents`; its JSON answer or {}."""
    from app.retrieval.gemini_search import make_client, parse_json_answer

    client = client or make_client()
    model = model or os.environ.get("GEMINI_READER_MODEL") or "gemini-3.8-flash"
    reply = client.interactions.create(model=model, input=contents)
    data = reply.model_dump(mode="json") if hasattr(reply, "model_dump") else reply
    return parse_json_answer(data.get("output_text") or "") or {}


# ---------------------------------------------------------------- checking


def _rows_with_value(flat: str, label: str, value: Any) -> list[int]:
    """Start offsets in `flat` of each printing of `label` whose row holds `value`."""
    found = []
    sentence = value_in_quote(value, label)  # a sentence carries its figure inside its own label
    for m in re.finditer(re.escape(label), flat):
        if sentence or _in_row(flat, m.end(), value) is not None:
            found.append(m.start())
    return found


def _grid_cells(content: bytes, label: str, value: Any) -> list:
    """Grid cells whose row's own label is `label` (or holds it) and whose figure is `value`."""
    want = _flatten(label).strip(" :")
    out = []
    for cell in cells(content):
        own = _flatten(cell.row[-1]).strip(" :")
        if _figure(cell.text) is None or not _same_number(cell.text.replace("$", "").strip(), value):
            continue
        if own == want or (want and want in own) or (own and own in want):
            out.append(cell)
    return out


def _printed(header: str, text: str, *, loose: bool) -> bool:
    """Whether `header` is printed in flattened `text`: verbatim, or with
    `loose`, as its words in order with anything between them."""
    wanted = _flatten(header)
    if wanted in text:
        return True
    if not loose:
        return False
    position = 0
    for word in wanted.split():
        found = re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text[position:])
        if not found:
            return False
        position += found.end()
    return True


def _period_refusal(evidence: str, column: str, end: dt.date, above: str = "") -> str | None:
    """Why `evidence` does not name the quarter, or None if it does. `column`
    is the part that belongs to the value's column alone: its years and any
    longer-period wording count against it.

    A column that prints no year takes the year printed nearest above the row
    (`above`), when the rest of the evidence names several or none: a table
    split into year sections prints `2025`, its rows, then `2024` and the same
    rows again, under one set of quarter headers.
    """
    years = set(_YEAR4.findall(column))
    if years and years != {str(end.year)}:
        return f"the column names {sorted(years)}, not {end.year}"
    if _LONGER.search(column):
        return f"the column is a longer period: {column[:120]!r}"
    named = set(_YEAR4.findall(evidence))
    if not years and (len(named) > 1 or (not named and not names_quarter(evidence, end))):
        nearest = _YEAR4.findall(above)[-1:]
        if nearest != [str(end.year)]:
            return f"the column names no year, and the nearest year above the row is {nearest or 'none'}"
        evidence = f"{evidence} {nearest[0]}"
    if not names_quarter(evidence, end):
        return f"{evidence[:160]!r} does not name the quarter"
    return None


def check_layout(answer: dict[str, Any], *, content: bytes, kind: str, text: str, product: str, end: dt.date,
                 single_product_ok: bool = False) -> tuple[str, str, dict[str, Any]]:
    """`(status, reason, details)` for a model answer against the document it read.

    `details` carries what checking settled: `generic_row`, and for an HTML
    table the `cell` whose labels carried the period.
    """
    details: dict[str, Any] = {"generic_row": False, "cell": None}
    if not answer.get("found"):
        return "not_found", "the model found no line for the product", details
    flat = _flatten(text)
    label = _flatten(str(answer.get("row_label") or "")).strip()
    value = answer.get("value")
    headers = [str(h) for h in (answer.get("column_headers") or []) if str(h).strip()]
    try:
        float(value)
    except (TypeError, ValueError):
        return "value_not_in_row", f"the value {value!r} is not a number", details
    if not label or label not in flat:
        return "row_not_in_document", f"the row label {label[:80]!r} is not in the document", details

    starts = _rows_with_value(flat, label, value)
    if not starts:
        return "value_not_in_row", f"{value!r} is not among the figures of any row labelled {label[:80]!r}", details

    # Headers must be printed above the row, within a table's reach of it.
    # Under an HTML table whose grid holds the cell, a header may be the
    # model's join of header rows printed apart (`Fourth` over `Quarter`), so
    # its words need only appear there in order; elsewhere the headers are
    # the period's only evidence and must be printed as quoted.
    grid = _grid_cells(content, str(answer.get("row_label")), value) if kind == "html" else []
    missing = [h for h in headers if not _printed(h, flat, loose=bool(grid))]
    if missing:
        return "header_not_in_document", f"headers not in the document: {missing}", details
    start = next((s for s in starts
                  if all(_printed(h, flat[max(0, s - HEADER_WINDOW): s], loose=bool(grid)) for h in headers)), None)
    if start is None:
        return "header_not_in_document", f"headers {headers} are not printed above the row", details
    above = flat[max(0, start - HEADER_WINDOW): start]

    name = _flatten(product.split("/")[0])
    generic = name not in label
    details["generic_row"] = generic

    # The period: an HTML table's cell carries its own labels; otherwise the
    # verified headers, or a sentence's own words.
    column_text = " ".join(headers)
    refusal = None
    if grid:
        refusal = "no cell"
        for cell in grid:
            # The cell's own column decides when it prints a period; the
            # model's headers are heard only for a column that prints none
            # (`Global Sales` under a dated heading row the grid set aside).
            column = " ".join(cell.column)
            if not _PERIOD_WORDS.search(column):
                column = f"{column} {column_text}"
            evidence = " ".join(cell.context) + " " + " ".join(cell.row[:-1]) + " " + column
            refusal = _period_refusal(evidence, column, end, above)
            if refusal is None:
                details["cell"] = cell.label()
                break
    elif headers:
        refusal = _period_refusal(column_text, column_text, end, above)
    else:
        refusal = _period_refusal(label, "", end)
    if refusal:
        return "wrong_period", refusal, details

    row_words = label + " " + (details["cell"] or "")
    if answer.get("kind") != "product_sales" or _NOT_OWN_SALES.search(label):
        return "not_own_sales", f"kind {answer.get('kind')!r}; row {label[:80]!r}", details
    if generic:
        if not single_product_ok:
            return "generic_row", (f"the row {label[:80]!r} does not name the product, and the caller has not "
                                   "established that the filer sells it alone"), details
        if answer.get("filer_total_product_revenue") is not True:
            return "generic_row", (f"the row {label[:80]!r} does not name the product and was not read as the "
                                   "filer's total product revenue"), details

    unit = str(answer.get("unit") or "").lower()
    if unit not in _SCALES:
        return "unit_conflict", f"unit {unit!r} is not a scale", details
    stated = stated_scale(label, value)
    if stated:
        if stated != unit:
            return "unit_conflict", f"the row says {stated}, the answer says {unit}", details
        return "accepted", "", details
    declared = declared_scale(above + " " + row_words) or declared_scale(text)
    if len(declared) == 1 and unit != "units" and unit not in declared:
        return "unit_conflict", f"the document declares {next(iter(declared))}, the answer says {unit}", details
    return "accepted", "", details


# ---------------------------------------------------------------- reading


def read_layout(url: str, product: str, period: str, *, single_product_ok: bool = False,
                get: Callable = sec_get, ask: Callable = gemini_layout_reader) -> LayoutReading:
    """Read `product`'s sales for calendar quarter `period` (`2024Q2` or
    `Q2 2024`) from the document at `url`.

    `single_product_ok` lets a row that does not name the product (`Product
    revenue, net`) stand for it; the caller passes it only once it has shown
    the filer sells this product alone. `get` fetches SEC documents and `ask`
    takes the Interactions API input and returns the model's JSON answer.
    """
    end = quarter_end(period)
    if end is None:
        return LayoutReading(url, "not_a_quarter", reason=f"{period!r} is not a calendar quarter")
    reading = LayoutReading(url, "error")
    try:
        content, content_type = fetch(url, get=get)
        reading.method = kind_of(content, content_type, url)
        if reading.method == "pdf" and len(content) > MAX_PDF_BYTES:
            reading.status, reading.reason = "unreadable", f"the PDF is {len(content)} bytes"
            return reading
        text = document_text(content, reading.method)
        if not text.strip():
            reading.status, reading.reason = "unreadable", "the document has no text to check an answer against"
            return reading
        answer = ask(contents_for(content, reading.method, text, product, end, period))
    except Exception as exc:  # a document that cannot be fetched or read declines; it does not raise
        if is_quota(exc):
            raise QuotaExceeded(str(exc)) from exc
        reading.reason = f"{type(exc).__name__}: {exc}"
        return reading

    reading.row_label = answer.get("row_label")
    reading.headers = [str(h) for h in (answer.get("column_headers") or [])]
    reading.unit, reading.kind = answer.get("unit"), answer.get("kind")
    reading.status, reading.reason, details = check_layout(
        answer, content=content, kind=reading.method, text=text, product=product, end=end,
        single_product_ok=single_product_ok)
    reading.generic_row, reading.cell = details["generic_row"], details["cell"]
    if reading.status == "accepted":
        reading.value = float(answer["value"]) * _SCALES[str(answer["unit"]).lower()]
        reading.currency = str(answer.get("currency") or "").upper() or None
    return reading
