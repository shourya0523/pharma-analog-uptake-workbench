"""Read J&J's Sales of Key Products/Franchises schedule.

The schedule prints each product as a name line followed by US, Intl and WW
rows. The WW row is the worldwide figure and the only one recorded - J&J sums
unrounded regional figures and rounds once, so US + Intl need not equal the
worldwide line the company itself states (2019 Opsumit prints 766 + 562 against
a stated 1,327). The first number on the WW row is the quarter the schedule
heads as current; the second is the prior-year comparative and is never used.
"""
import json, os, pathlib, re

BASE = pathlib.Path(os.environ.get("SOURCING_WORKDIR", "/tmp/gold-sourcing")) / "jnj"
NUM = re.compile(r"^\(?\$?\s*-?[\d,]+(?:\.\d+)?\)?$")
ROW = re.compile(r"^(US|Intl|WW)\s+(.*)$")
QUARTER_WORD = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4}


def numbers(text):
    out = []
    for token in text.replace("$", " ").split():
        token = token.strip()
        if token in {"-", "—"}:
            out.append(None)
        elif NUM.match(token) and "%" not in token:
            out.append(float(token.strip("()").replace(",", "")))
    return out


def page_lines(path):
    """Lines rebuilt from word positions, not from the PDF's text layer.

    Some of these schedules carry a text layer that splits a number in two -
    "1 ,613" for 1,613, "7 04" for 704 - which a naive reader turns into the
    value 1. Rebuilding from word boxes and rejoining fragments that are
    physically touching avoids guessing at it with a regex: the gap between
    two words either is a real separator or it is not, and the page says so.
    """
    import pdfplumber
    lines = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            rows = {}
            for word in page.extract_words(use_text_flow=False, keep_blank_chars=False):
                rows.setdefault(round(word["top"] / 3), []).append(word)
            for _, words in sorted(rows.items()):
                words.sort(key=lambda w: w["x0"])
                parts = []
                for word in words:
                    if parts and word["x0"] - parts[-1][1] < 2.0:
                        parts[-1] = (parts[-1][0] + word["text"], word["x1"])
                    else:
                        parts.append((word["text"], word["x1"]))
                lines.append(" ".join(text for text, _ in parts))
    return lines


ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth"}


def declared_period(lines):
    """The quarter and unit the schedule states about itself."""
    # Rejoining split words can also close the gap between real words, so the
    # heading is matched with whitespace removed rather than as written.
    head = "".join(" ".join(lines[:8]).upper().split())
    if "$MM" not in head and "MILLIONS" not in head:
        return None, None, None
    quarter = next((n for word, n in QUARTER_WORD.items() if word + "QUARTER" in head), None)
    years = re.findall(r"(20\d{2})", head)
    if not (quarter and len(years) >= 2):
        return None, None, None
    return f"{years[0]}Q{quarter}", "millions", years[:2]


def products(lines):
    """Worldwide value per product name, from the WW row under each name."""
    out, name = {}, None
    for raw in lines:
        line = raw.strip()
        match = ROW.match(line)
        if not match:
            # A product or franchise heading: text, no leading region marker.
            if line and not line[0].isdigit() and "%" not in line and len(line) < 80:
                if not numbers(line):
                    name = line
            continue
        region, rest = match.group(1), match.group(2)
        if region == "WW" and name:
            # Keep the document's own tokens, not reformatted floats: the
            # quote has to contain the figure exactly as the schedule prints
            # it. Percentage columns are dropped, which the quote says.
            tokens = [t.replace("$", "") for t in rest.split()
                      if "%" not in t and NUM.match(t.replace("$", ""))]
            if tokens:
                out.setdefault(name, (float(tokens[0].replace(",", "")), tokens))
            name = None
    return out


def main():
    urls = json.load(open(BASE / "urls.json"))
    rows = []
    for path in sorted((BASE / "pdfs").glob("*.pdf")):
        key = path.stem
        lines = page_lines(path)
        period, unit, years = declared_period(lines)
        if not period:
            print(f"  refused {key}: no declared quarter/unit"); continue
        if period != key:
            print(f"  refused {key}: schedule declares {period}"); continue
        found = products(lines)
        quarter = int(period[-1])
        rows.append({"period": period, "unit": unit, "url": urls.get(key, ""),
                     "years": years, "ordinal": ORDINAL[quarter],
                     "products": {k: v[0] for k, v in found.items()},
                     "tokens": {k: v[1] for k, v in found.items()}})
    json.dump(rows, open(BASE / "jnj_quarters.json", "w"), indent=1)
    print(f"{len(rows)} quarters parsed")
    r = rows[len(rows)//2]
    print(f"\nsample {r['period']}:")
    for k, v in list(r["products"].items())[:12]:
        print(f"   {k:<44}{v:,.0f}   tokens={r['tokens'][k]}")


if __name__ == "__main__":
    main()
