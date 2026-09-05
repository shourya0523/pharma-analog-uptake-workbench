"""Download J&J's quarterly Sales of Key Products/Franchises schedules."""
import os, pathlib, urllib.request, urllib.error

BASE = "https://s203.q4cdn.com/636242992/files/doc_financials/{y}/q{q}/Sales_of_Key_Products_Franchises_{q}Q{y}.pdf"
ALT = [
    "https://s203.q4cdn.com/636242992/files/doc_financials/{y}/q{q}/Sales-of-Key-Products-Franchises-{q}Q{y}.pdf",
    "https://s203.q4cdn.com/636242992/files/doc_financials/{y}/q{q}/{q}Q{y}-Sales-of-Key-Products-Franchises.pdf",
]
WORK = pathlib.Path(os.environ.get("SOURCING_WORKDIR", "/tmp/gold-sourcing")) / "jnj"
out = WORK / "pdfs"
out.mkdir(parents=True, exist_ok=True)
got, missing = [], []
for y in range(2013, 2020):
    for q in (1, 2, 3, 4):
        path = out / f"{y}Q{q}.pdf"
        if path.exists() and path.stat().st_size > 5000:
            got.append((f"{y}Q{q}", BASE.format(y=y, q=q))); continue
        for template in [BASE] + ALT:
            url = template.format(y=y, q=q)
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    body = r.read()
                if body[:4] == b"%PDF":
                    path.write_bytes(body); got.append((f"{y}Q{q}", url)); break
            except Exception:
                continue
        else:
            missing.append(f"{y}Q{q}")
print(f"{len(got)} downloaded, missing: {missing}")
import json; json.dump(dict(got), open(WORK / "urls.json", "w"), indent=1)
