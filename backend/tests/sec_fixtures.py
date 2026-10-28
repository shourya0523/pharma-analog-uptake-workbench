"""Invented SEC filings served from memory, for tests that walk the XBRL readers."""

from __future__ import annotations

import datetime as dt

HEAD = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:us-gaap="http://fasb.org/us-gaap/2023" xmlns:acme="http://acme.example/2023"><body>"""


def inline(facts: list[tuple[str, float, str, str, dict[str, str]]], text: str = "") -> bytes:
    """An inline-XBRL document. Each fact is (concept, value in units, start, end, members)."""
    contexts, body = [], []
    for i, (concept, value, start, end, members) in enumerate(facts):
        dims = "".join(
            f'<xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember>'
            for axis, member in members.items()
        )
        segment = f"<xbrli:segment>{dims}</xbrli:segment>" if dims else ""
        contexts.append(
            f'<xbrli:context id="c{i}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>{segment}'
            f"</xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate>"
            f"<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>"
        )
        body.append(
            f'<p><ix:nonFraction name="{concept}" contextRef="c{i}" unitRef="usd" scale="0" '
            f'format="ixt:num-dot-decimal">{value:.0f}</ix:nonFraction></p>'
        )
    resources = "".join(contexts) + '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    return (HEAD + f"<ix:header><ix:resources>{resources}</ix:resources></ix:header>".encode()
            + "".join(body).encode() + f"<p>{text}</p></body></html>".encode())


class Response:
    def __init__(self, content: bytes = b"", body=None):
        self.content, self._body = content, body

    def json(self):
        return self._body


def fake_sec(filings: list[dict]):
    """A `get` serving full-text search and documents for `filings`, each
    {form, period_end, cik, filer, document, content}."""

    def get(url, **params):
        if "search-index" in url:
            start = dt.date.fromisoformat(params["startdt"])
            hits = [
                {"_id": f"{f['accession']}:{f['document']}",
                 "_source": {"adsh": f["accession"], "ciks": [f"000000{f['cik']}"], "display_names": [f["filer"]],
                             "period_ending": f["period_end"]}}
                for f in filings
                if f["form"] == params["forms"] and abs((dt.date.fromisoformat(f["period_end"]) - start).days) <= 10
            ]
            return Response(body={"hits": {"hits": hits}})
        if url.endswith("index.json"):
            return Response(body={"directory": {"item": []}})
        for f in filings:
            if url.endswith(f["document"]):
                return Response(f["content"])
        raise AssertionError(url)

    return get


def filing(form: str, period_end: str, content: bytes, cik: int = 1, filer: str = "ACME PHARMA INC (CIK 1)") -> dict:
    stem = f"acme-{period_end.replace('-', '')}-{form.lower()}"
    return {"form": form, "period_end": period_end, "cik": cik, "filer": filer, "content": content,
            "document": f"{stem}.htm", "accession": f"000000000{cik}-{period_end[2:4]}-{abs(hash(stem)) % 10**6:06d}"}
