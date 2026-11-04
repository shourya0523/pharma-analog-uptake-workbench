"""The ownership timeline, against invented annual reports."""

from __future__ import annotations

from app.retrieval.ownership import timeline
from sec_fixtures import Response

REPORTS = {
    2016: [("ACME PHARMA (CIK 1)", 1, "acme-2016.htm", b"<p>Net product sales of Calderon were $120 million.</p>")],
    2017: [("BETA BIO (CIK 2)", 2, "beta-2017.htm", b"<p>Following the merger we recorded Calderon sales of $90 million.</p>"),
           ("GAMMA LABS (CIK 3)", 3, "gamma-2017.htm", b"<p>We compete with Calderon.</p>")],
}


def get(url, **params):
    if "search-index" in url:
        year = int(params["startdt"][:4]) - 1
        hits = [{"_id": f"000{cik}-{year}:{doc}", "_source": {"ciks": [f"000{cik}"], "display_names": [name], "file_type": "10-K"}}
                for name, cik, doc, _ in REPORTS.get(year, [])]
        return Response(body={"hits": {"hits": hits}})
    for rows in REPORTS.values():
        for _, _, doc, content in rows:
            if url.endswith(doc):
                return Response(content)
    raise AssertionError(url)


def ask(prompt):
    if "Net product sales of Calderon" in prompt:
        return {"role": "seller", "quote": "Net product sales of Calderon were $120 million."}
    if "Following the merger" in prompt:
        return {"role": "seller", "quote": "Following the merger we recorded Calderon sales of $90 million."}
    return {"role": "seller", "quote": "an invented sentence"}


def test_sellers_by_year_follow_the_product_to_its_new_owner():
    result = timeline("Calderon", range(2016, 2019), get=get, ask=ask)
    assert result.sellers(2016) == ["ACME PHARMA (CIK 1)"]
    assert result.sellers(2017) == ["BETA BIO (CIK 2)"]
    assert {r.filer: r.role for r in result.years[2017]}["GAMMA LABS (CIK 3)"] == "unverified"
    assert result.gaps() == [2018]
