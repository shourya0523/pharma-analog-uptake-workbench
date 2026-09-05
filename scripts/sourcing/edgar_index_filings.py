"""List an issuer's 8-K filings from EDGAR, newest shard plus the older ones."""
import os
import json, os, sys, time, urllib.request

# SEC asks for a contact address in the User-Agent. It is read from the
# environment rather than written here, so a personal address never lands in
# the repository.
UA = os.environ.get("SEC_CONTACT")
if not UA:
    raise SystemExit(
        "Set SEC_CONTACT to a contact string for the SEC User-Agent header, "
        "e.g. SEC_CONTACT='pharma-analog-uptake-workbench you@example.com'"
    )

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip; raw = gzip.decompress(raw)
                return raw
        except Exception as exc:
            if attempt == 3: raise
            time.sleep(2 ** attempt)

cik = sys.argv[1].zfill(10)
data = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
rows = []

def collect(block):
    for form, date, acc, doc in zip(block["form"], block["filingDate"],
                                    block["accessionNumber"], block["primaryDocument"]):
        if form.startswith("8-K"):
            rows.append((date, acc.replace("-", ""), doc))

collect(data["filings"]["recent"])
for extra in data["filings"].get("files", []):
    time.sleep(0.15)
    collect(json.loads(get(f"https://data.sec.gov/submissions/{extra['name']}")))

rows.sort()
print(f"{len(rows)} 8-K filings, {rows[0][0]} to {rows[-1][0]}")
with open(sys.argv[2], "w") as fh:
    json.dump(rows, fh)
