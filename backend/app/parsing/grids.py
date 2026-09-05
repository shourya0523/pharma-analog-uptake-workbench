"""Format-agnostic table recovery: any document becomes text plus grids.

The extraction stages downstream read one shape - a list of rows, each a list
of cells, with the header rows first - and they read it the same way whether
the document was an HTML filing, a PDF, a markdown rendering of either, or a
plain-text dump. This module is where every other physical form is reduced to
that shape, and it knows nothing about who published the document.

Three physical forms are handled, all generically:

* **Pipe tables** (markdown, or any ``|``-delimited text). Cells are split on
  the delimiter; cells that carry only a currency sign, a percent sign or a
  closing parenthesis are folded into their neighbour, because renderers put
  ``$`` and ``)%`` in cells of their own and that would otherwise shift every
  later column.
* **Flattened text**, where a PDF's grid arrived as lines of "label numbers
  numbers ..." - the column boundaries are gone but the order survives. A row
  is a run of words followed by a run of numeric tokens. Header lines are
  the non-row lines that sit immediately above a block of rows.
* **One-cell-per-line dumps**, where the same grid arrived with every cell on
  its own line. Detected by the shape of the text (long runs of single-token
  lines) and re-joined into a token stream, after which it is the flattened
  case.

Whatever the form, the paragraph immediately before a table is attached to it
as a caption row, because that is where issuers declare the unit
("dollars in thousands") that the table's own cells do not repeat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

Row = list[str]
Table = list[Row]

# A numeric cell: optional sign or opening parenthesis, digits with optional
# thousands separators and decimals, optional closing parenthesis and percent.
_NUMBER_TOKEN_RE = re.compile(
    r"^[\(\-–−]?\$?\s?\d[\d,]*(?:\.\d+)?\)?%?$|^[\(\-–−]?\$?\s?\.\d+\)?%?$"
)
# Cells an issuer prints where a number would go: nothing to report.
_PLACEHOLDER_TOKEN_RE = re.compile(
    r"^(?:[-–—−*]+%?|n/?a|nm|n\.m\.|not\s+meaningful)$", re.I
)
# Cells that are punctuation attached to a neighbouring number.
_SYMBOL_ONLY_RE = re.compile(r"^[\s$€£¥%)\(]*$")
_OPEN_PAREN_NUMBER_RE = re.compile(r"^\(\s*\$?\s*\d[\d,]*(?:\.\d+)?$")
_CLOSE_PAREN_RE = re.compile(r"^\)\s*%?$")
_PERCENT_RE = re.compile(r"^%$")
_FOOTNOTE_RE = re.compile(r"^\(\s*[a-z0-9]{1,2}\s*\)$", re.I)
_FOOTNOTE_MARKER_RE = re.compile(r"^\(\s*[a-z1-9]\s*\)$", re.I)

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_MONTH_RE = re.compile(
    r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?$", re.I
)
_DAY_TOKEN_RE = re.compile(r"^[0-3]?\d,?$")

_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__)(.*?)\1", re.S)
_MD_ITALIC_CELL_RE = re.compile(r"^_(.*?)_$", re.S)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+")
_MD_SEPARATOR_ROW_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HR_RE = re.compile(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$")


def clean_markup(text: str) -> str:
    """Strip markdown emphasis and escapes without touching the words."""
    text = text.replace(" ", " ")
    text = _MD_EMPHASIS_RE.sub(r"\2", text)
    text = _MD_ESCAPE_RE.sub(r"\1", text)
    return text


_CURRENCY_PREFIX_RE = re.compile(r"^[$€£¥]\s*(?=[\d(\-–—−*])")


_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


def _clean_cell(cell: str) -> str:
    # Zero-width characters are spacer cells in some filings' HTML; they
    # hold no value and no column.
    cell = clean_markup(_INVISIBLE_RE.sub("", cell)).strip()
    match = _MD_ITALIC_CELL_RE.match(cell)
    if match:
        cell = match.group(1).strip()
    # "$6,517" and "$—" are a number and a placeholder wearing a currency
    # sign; the sign is declared by the header, not by the cell.
    cell = _CURRENCY_PREFIX_RE.sub("", cell)
    return re.sub(r"\s+", " ", cell)


def is_number_token(token: str) -> bool:
    return bool(_NUMBER_TOKEN_RE.match(token.strip()))


def is_placeholder_token(token: str) -> bool:
    return bool(_PLACEHOLDER_TOKEN_RE.match(token.strip()))


def is_value_token(token: str) -> bool:
    return is_number_token(token) or is_placeholder_token(token)


def is_year_token(token: str) -> bool:
    return bool(_YEAR_RE.match(token.strip()))


def normalize_cells(cells: list[str]) -> Row:
    """Fold rendering artefacts back into real cells.

    Renderers emit ``$`` and ``%`` and ``)`` as cells of their own and split
    ``(50.9)%`` across two. Folding them back keeps each column where the
    document put it, which is what positional alignment depends on.
    """
    out: list[str] = []
    cleaned = [_clean_cell(raw) for raw in cells]
    cleaned = [cell for cell in cleaned if cell]
    for index, cell in enumerate(cleaned):
        if out and (_CLOSE_PAREN_RE.match(cell) or _PERCENT_RE.match(cell)):
            previous = out[-1]
            if _OPEN_PAREN_NUMBER_RE.match(previous) or is_number_token(previous) or is_placeholder_token(previous):
                out[-1] = previous + cell.replace(" ", "")
                continue
        if _SYMBOL_ONLY_RE.match(cell):
            continue
        # A footnote marker in a cell of its own ("(1)") annotates the row;
        # it is not a value and holds no column. "(12)" followed by "%" is a
        # negative percentage, not a marker.
        following = cleaned[index + 1] if index + 1 < len(cleaned) else ""
        if (
            out and _FOOTNOTE_MARKER_RE.match(cell) and is_value_token(out[-1])
            and not (_PERCENT_RE.match(following) or _CLOSE_PAREN_RE.match(following))
        ):
            continue
        out.append(cell)
    return out


# --------------------------------------------------------------------------
# Pipe tables
# --------------------------------------------------------------------------

def _is_pipe_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") or (stripped.count("|") >= 2)


def parse_pipe_tables(text: str) -> tuple[list[str], list[Table]]:
    """Text blocks and tables from ``|``-delimited markup.

    Returns the prose with the tables removed (so a prose reader never sees a
    table row as a sentence) and each table as header-first rows with its
    preceding paragraph attached as a caption row.
    """
    lines = text.splitlines()
    blocks: list[str] = []
    tables: list[Table] = []
    paragraph: list[str] = []
    current: list[str] | None = None

    def flush_paragraph() -> None:
        if paragraph:
            joined = " ".join(part.strip() for part in paragraph if part.strip())
            if joined:
                blocks.append(clean_markup(joined))
            paragraph.clear()

    def flush_table() -> None:
        nonlocal current
        if current is None:
            return
        rows: Table = []
        for raw in current:
            if _MD_SEPARATOR_ROW_RE.match(raw.strip()):
                continue
            cells = raw.strip().strip("|").split("|")
            row = normalize_cells(cells)
            if row:
                rows.append(row)
        if rows:
            caption = _caption_for(blocks)
            tables.append(([[caption]] if caption else []) + rows)
        current = None

    for line in lines:
        if _is_pipe_row(line):
            if current is None:
                flush_paragraph()
                current = []
            stripped = line.strip()
            if current and not current[-1].rstrip().endswith("|") and not stripped.startswith("|"):
                current[-1] = current[-1] + " " + stripped
            else:
                current.append(stripped)
            continue
        if current is not None and not current[-1].rstrip().endswith("|"):
            # A cell that wrapped onto the next physical line, possibly
            # across a blank line: the row is not over until its closing
            # delimiter.
            if line.strip():
                current[-1] = current[-1] + " " + line.strip()
            continue
        if current is not None:
            flush_table()
        if not line.strip():
            flush_paragraph()
            continue
        if _HR_RE.match(line.strip()):
            flush_paragraph()
            continue
        paragraph.append(_MD_HEADING_RE.sub("", line))
    flush_table()
    flush_paragraph()
    return blocks, tables


def _caption_for(blocks: list[str], max_chars: int = 400) -> str | None:
    """The paragraph just before a table, where its unit is usually declared."""
    for block in reversed(blocks[-2:]):
        if block and len(block) <= max_chars:
            return block
    if blocks and blocks[-1]:
        return blocks[-1][-max_chars:]
    return None


# --------------------------------------------------------------------------
# Flattened text and one-cell-per-line dumps
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\S+")


@dataclass
class _Line:
    text: str
    tokens: list[str] = field(default_factory=list)

    @property
    def is_single_token(self) -> bool:
        return len(self.tokens) == 1

    @property
    def is_streamable(self) -> bool:
        """One cell, or only value cells: part of a grid that lost its rows.

        A stray currency sign or a ``%`` the renderer detached from its
        number is punctuation, not a cell, and does not make a line prose.
        """
        if len(self.tokens) == 1:
            return True
        cells = [t for t in self.tokens if not _SYMBOL_ONLY_RE.match(t)]
        return bool(cells) and all(is_value_token(t) or _FOOTNOTE_RE.match(t) for t in cells)

    @property
    def is_short(self) -> bool:
        """Few cells: a label with one value, a two-word heading."""
        return 0 < len(self.tokens) <= 3

    @property
    def is_whole_row(self) -> bool:
        """A label followed by at least two values: a row printed complete on one line."""
        if not self.is_row_start:
            return False
        split = _split_row(self.tokens)
        return split is not None and len(split[1]) >= 2

    @property
    def is_row_start(self) -> bool:
        """A label followed by values: the head of a row whose remaining
        cells may have landed on the lines after it.

        A label that names a period or contains a number is a header, and
        never the start of a row that continues below.
        """
        split = _split_row(self.tokens)
        if split is None:
            return False
        label, _values = split
        if len(label) > 6 or any(is_number_token(t) for t in label):
            return False
        return not _PERIOD_WORD_RE.search(" ".join(label))


def _fold_symbols(tokens: list[str]) -> list[str]:
    """Drop stray currency signs and fold a detached ``)`` or ``%`` onto its number.

    A "%" that opens a header phrase ("2021 % Change") stays a word.
    """
    out: list[str] = []
    for index, token in enumerate(tokens):
        if not _SYMBOL_ONLY_RE.match(token):
            out.append(token)
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if _PERCENT_RE.match(token) and following[:1].isalpha():
            out.append(token)
            continue
        if out and (_CLOSE_PAREN_RE.match(token) or _PERCENT_RE.match(token)):
            previous = out[-1]
            if _OPEN_PAREN_NUMBER_RE.match(previous) or (is_number_token(previous) and not is_year_token(previous)):
                out[-1] = previous + token.replace(" ", "")
    return out


def _split_row(tokens: list[str]) -> tuple[list[str], list[str]] | None:
    """(label words, value tokens) when the tokens end in a numeric run.

    Footnote markers like ``(1)`` between the label and its numbers belong to
    the label. A trailing run of value tokens must be preceded by at least one
    word, otherwise there is nothing the numbers are a row *of*.
    """
    tokens = _fold_symbols(tokens)
    if not tokens:
        return None
    index = len(tokens)
    while index > 0 and (is_value_token(tokens[index - 1]) or _FOOTNOTE_RE.match(tokens[index - 1])):
        index -= 1
    values = tokens[index:]
    label = tokens[:index]
    values = [v for v in values if not _FOOTNOTE_RE.match(v)]
    if not values or not label or len(label) > 12:
        return None
    if _MONTH_RE.match(label[-1]) and _DAY_TOKEN_RE.match(values[0]):
        # "December 31, 2004": a date in a header, not a label with values.
        return None
    if not any(is_number_token(v) for v in values):
        # A row of nothing but dashes ("Intl - - - - -") is still a row of
        # the grid: the issuer printed a line with nothing to report, and
        # the rows after it still belong to the same product.
        if len(values) >= 3 and len(label) <= 6:
            return label, values
        return None
    return label, values


def _is_year_run(values: list[str]) -> bool:
    numbers = [v for v in values if is_number_token(v)]
    return bool(numbers) and all(is_year_token(v) for v in numbers)


def _merge_single_token_runs(lines: list[_Line], min_run: int = 6) -> list[_Line]:
    """Re-join a one-cell-per-line dump into flattened rows.

    A run of many consecutive single-token lines is a grid whose cells each
    landed on their own line. Joining the run into one token stream and
    cutting it wherever a word run follows a value run recovers the rows.
    """
    out: list[_Line] = []
    index = 0

    def in_region(line: _Line) -> bool:
        return line.is_short or line.is_streamable or line.is_row_start

    while index < len(lines):
        if not in_region(lines[index]):
            out.append(lines[index])
            index += 1
            continue
        # A region of short lines, bare value runs and row heads, most of
        # them single cells or bare values, is a grid whose cells landed one
        # or a few per line. Whole rows that happen to sit next to it pass
        # through the stream unchanged: a word after a value run always
        # starts a new row.
        run_end = index
        while run_end < len(lines) and in_region(lines[run_end]):
            run_end += 1
        region = lines[index:run_end]
        core = [l for l in region if l.is_short or l.is_streamable]
        streamable = sum(1 for l in core if l.is_streamable)
        if len(core) < min_run or streamable * 2 < len(core) or not any(l.is_single_token for l in core):
            out.extend(region)
            index = run_end
            continue
        stream = [token for line in region for token in line.tokens]
        out.extend(_segment_stream(stream))
        index = run_end
    return out


def _segment_stream(stream: list[str]) -> list[_Line]:
    """Cut a token stream into rows at every word-run that follows a value-run."""
    rows: list[_Line] = []
    current: list[str] = []
    in_values = False
    for token in _fold_symbols(stream):
        value = is_value_token(token) or _FOOTNOTE_RE.match(token) is not None
        value = is_value_token(token) or _FOOTNOTE_RE.match(token) is not None
        if value:
            in_values = in_values or is_value_token(token)
            current.append(token)
            continue
        if in_values and current:
            rows.append(_Line(" ".join(current), current))
            current = []
            in_values = False
        current.append(token)
    if current:
        rows.append(_Line(" ".join(current), current))
    return rows


_YEAR_IN_TEXT_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PERIOD_WORD_RE = re.compile(
    r"\b(?:quarter|months?|year|ended|full[-\s]?year|q[1-4]|[1-4]q|fy)\b|(?:19|20)\d{2}",
    re.I,
)


def _looks_like_group_label(line: _Line) -> bool:
    if len(line.tokens) > 8:
        return False
    return not _PERIOD_WORD_RE.search(line.text)


def recover_text_grids(text: str, *, max_header_lines: int = 12) -> list[Table]:
    """Tables recovered from text whose grid structure is only in token order."""
    raw_lines = [clean_markup(line) for line in text.splitlines()]
    # Paragraph-join: a blank line separates cells or rows in these dumps, a
    # newline inside a paragraph is just wrapping.
    paragraphs: list[str] = []
    buffer: list[str] = []
    physical: list[str] = []
    for line in raw_lines:
        if line.strip() and not _HR_RE.match(line.strip()) and not _MD_SEPARATOR_ROW_RE.match(line.strip()):
            buffer.append(line.strip())
            physical.append(line.strip())
        else:
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer))

    # Text extracted from a PDF page has no blank lines at all: every
    # physical line is a whole row ("US 397 391 1.6% ..."), a label or a
    # header line, and joining them would fuse the grid into one
    # paragraph. When most lines read as rows of their own, they are the
    # units. A dump that puts one cell per line and a blank line between
    # rows keeps its paragraphs: there the blank lines are the rows.
    units = paragraphs
    if len(physical) >= 6 and len(physical) * 2 >= len(paragraphs) * 3:
        candidates = [_Line(p, _TOKEN_RE.findall(p)) for p in physical]
        whole_rows = sum(1 for l in candidates if l.is_whole_row)
        if whole_rows * 3 >= len(candidates):
            units = physical

    lines = [_Line(p, _TOKEN_RE.findall(p)) for p in units]
    lines = _merge_single_token_runs(lines)

    tables: list[Table] = []
    pending_header: list[_Line] = []
    current_rows: Table = []
    current_header: Table = []

    def flush() -> None:
        nonlocal current_rows, current_header
        if current_rows and any(len(row) > 1 for row in current_rows):
            tables.append(current_header + current_rows)
        current_rows, current_header = [], []

    for line in lines:
        split = _split_row(line.tokens)
        if split is not None:
            label, values = split
            if _is_year_run(values) and not current_rows:
                # A header row that lists the year columns.
                pending_header.append(line)
                continue
            if _is_year_run(values) and current_rows:
                # A new header block starts a new table.
                flush()
                pending_header = [line]
                continue
            if not current_rows:
                current_header = [[l.text] for l in pending_header[-max_header_lines:]]
                pending_header = []
            current_rows.append([" ".join(label)] + values)
            continue
        # Not a row. A short line without period tokens inside a table is a
        # group label ("ONCOLOGY", "Other products:", a product name above
        # its geography rows) and stays with the table as a one-cell row.
        # Anything else is header material for the next table, and ends
        # the current one.
        if current_rows and _looks_like_group_label(line):
            current_rows.append([line.text])
            continue
        if current_rows:
            # Group labels at the end of a grid ("4. INVENTORIES") were the
            # title of the grid that starts here, not a group of the last.
            trailing: list[_Line] = []
            while current_rows and len(current_rows[-1]) == 1 and len(current_rows) > 1:
                trailing.insert(0, _Line(current_rows[-1][0], _TOKEN_RE.findall(current_rows[-1][0])))
                current_rows.pop()
            flush()
            pending_header.extend(trailing)
        pending_header.append(line)
        if len(pending_header) > max_header_lines * 2:
            pending_header = pending_header[-max_header_lines:]
    flush()
    # Lines after the last grid that name years ("Historical Sales 2016 2017")
    # are that grid's footer: the years its bare quarter labels refer to.
    if tables and pending_header:
        trailing = [[l.text] for l in pending_header[-4:] if _YEAR_IN_TEXT_RE.search(l.text)]
        if trailing:
            tables[-1] = tables[-1] + trailing
    return tables


def parse_text_document(text: str) -> tuple[list[str], list[Table]]:
    """Every grid a text document contains, whichever way it was flattened."""
    blocks, pipe_tables = parse_pipe_tables(text)
    # Grid recovery sees the text with its line structure intact: whether a
    # newline is a wrapped paragraph or a row of a grid is its decision.
    kept = [
        line for line in text.splitlines()
        if not _is_pipe_row(line) and not _MD_SEPARATOR_ROW_RE.match(line.strip())
    ]
    text_tables = recover_text_grids("\n".join(kept))
    return blocks, pipe_tables + text_tables
