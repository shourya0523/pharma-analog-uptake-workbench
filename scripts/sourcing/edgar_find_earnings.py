"""Find the earnings-release exhibit inside each 8-K: the one that prints a
PRODUCT SALES SUMMARY. Caches every document so parsing never refetches."""
import json, os, re, sys, time, urllib.request

# SEC asks for a contact address in the User-Agent. It is read from the
# environment rather than written here, so a personal address never lands in
# the repository.
UA = os.environ.get("SEC_CONTACT")
if not UA:
    raise SystemExit(
        "Set SEC_CONTACT to a contact string for the SEC User-Agent header, "
        "e.g. SEC_CONTACT='pharma-analog-uptake-workbench you@example.com'"
    )
CACHE = "cache"
os.makedirs(CACHE, exist_ok=True)

def get(url, key):
    path = os.path.join(CACHE, key)
    if os.path.exists(path):
        return open(path, "rb").read()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            open(path, "wb").write(raw)
            time.sleep(0.12)
            return raw
        except Exception:
            if attempt == 3:
                return b""
            time.sleep(2 ** attempt)

cik = sys.argv[1]
rows = json.load(open(sys.argv[2]))
lo, hi = sys.argv[3], sys.argv[4]
found = []
for date, acc, doc in rows:
    if not (lo <= date <= hi):
        continue
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
    listing = get(f"{base}/index.json", f"{cik}-{acc}-index.json")
    if not listing:
        continue
    try:
        items = json.loads(listing)["directory"]["item"]
    except Exception:
        continue
    # The full-submission .txt contains every exhibit, so it always matches
    # first. Cite the standalone exhibit where the filing has one.
    names = [i["name"] for i in items if i["name"].endswith((".htm", ".txt"))]
    names = [n for n in names if not n.endswith("-index.htm")]
    names.sort(key=lambda n: (acc[:10] in n.replace("-", ""), n))
    for name in names:
        body = get(f"{base}/{name}", f"{cik}-{acc}-{name}")
        text = body.decode("utf-8", "ignore")
        if re.search(r"PRODUCT\s*SALES\s*SUMMARY", text, re.I):
            found.append({"date": date, "acc": acc, "doc": name, "url": f"{base}/{name}"})
            break

json.dump(found, open(sys.argv[5], "w"), indent=1)
print(f"{len(found)} earnings exhibits with a PRODUCT SALES SUMMARY")
for f in found[:5] + (["..."] if len(found) > 10 else []) + found[-3:]:
    print("  ", f if isinstance(f, str) else f"{f['date']}  {f['url']}")
