"""Merge the newly sourced Gilead quarters into the existing manifests.

Existing rows are never overwritten: they were verified against the issuer
already, and a row that changes silently is exactly what a gold dataset must
not do. New rows carry the SEC exhibit that reports the quarter as current,
the issuer's own figure in the issuer's own unit, and a quote naming the
product rather than a bare row of digits.
"""
import csv, json, os, pathlib, collections

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "seed" / "gold" / "source_manifests"
WORK = pathlib.Path(os.environ.get("SOURCING_WORKDIR", "/tmp/gold-sourcing"))
DATA = WORK / "edgar" / "gilead_quarters.json"

MANIFESTS = {
    "Ranexa": "gilead_ranexa_quarterly.csv", "AmBisome": "gilead_ambisome_quarterly.csv",
    "Harvoni": "gilead_harvoni_quarterly.csv", "Atripla": "gilead_atripla_quarterly.csv",
    "Biktarvy": "gilead_biktarvy_quarterly.csv", "Complera": "gilead_complera_quarterly.csv",
    "Descovy": "gilead_descovy_quarterly.csv", "Epclusa": "gilead_epclusa_quarterly.csv",
    "Genvoya": "gilead_genvoya_quarterly.csv", "Odefsey": "gilead_odefsey_quarterly.csv",
    "Stribild": "gilead_stribild_quarterly.csv", "Truvada": "gilead_truvada_quarterly.csv",
    "Letairis": "gilead_letairis_quarterly.csv",
}
ALIAS = {"Complera / Eviplera": "Complera"}
CONTEXT = (
    "Gilead's PRODUCT SALES SUMMARY prints a multi-territory product as regional "
    "lines followed by an unlabelled total; that total is the worldwide figure "
    "and is the one recorded. A single-territory product prints one line. The "
    "quote carries the regional lines with the total so the product is named. "
    "Read from the exhibit that reports this quarter as its current three "
    "months, never from a prior-year comparative column."
)

quarters = json.load(open(DATA))
best = {}
for r in quarters:
    for raw, value in r["products"].items():
        name = ALIAS.get(raw, raw)
        # Two 8-Ks were filed the same day for 2016Q2 with identical figures;
        # keep the first so the choice is deterministic.
        best.setdefault((name, r["period"]), {
            "period": r["period"], "value_reported": value / 1000 if r["unit"] == "thousands" else value,
            "source_url": r["url"], "source_quote": r["quotes"][raw],
            "derivation": "direct_reported", "context": CONTEXT,
            "source_type": "sec_filing", "source_unit": r["unit"],
            "source_value_reported": value,
        })

FIELDS = ["period", "value_reported", "source_url", "source_quote", "derivation",
          "context", "source_type", "source_unit", "source_value_reported"]
report = []
for drug, filename in MANIFESTS.items():
    path = SRC / filename
    existing = list(csv.DictReader(open(path))) if path.exists() else []
    have = {r["period"] for r in existing}
    added = [best[k] for k in best if k[0] == drug and k[1] not in have]
    if not added:
        report.append((drug, len(existing), 0, "", "")); continue
    merged = existing + added
    for row in merged:
        row.setdefault("source_type", "company_ir")
        row.setdefault("source_unit", "millions")
        row.setdefault("source_value_reported", row["value_reported"])
        if isinstance(row["value_reported"], float):
            row["value_reported"] = f"{row['value_reported']:.6f}".rstrip("0").rstrip(".")
    merged.sort(key=lambda r: (int(r["period"][:4]), int(r["period"][-1])))
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in FIELDS} for r in merged)
    report.append((drug, len(existing), len(added), merged[0]["period"], merged[-1]["period"]))

print(f"{'product':<12}{'was':>5}{'added':>7}{'now':>6}   span")
for drug, was, added, first, last in sorted(report):
    print(f"{drug:<12}{was:>5}{added:>7}{was+added:>6}   {first} -> {last}")
print(f"\ntotal added: {sum(r[2] for r in report)}")

# Contiguity: a series with a hole is worse than a shorter series.
for drug, filename in MANIFESTS.items():
    periods = sorted((int(r["period"][:4]) * 4 + int(r["period"][-1]))
                     for r in csv.DictReader(open(SRC / filename)))
    holes = [p for a, p in zip(periods, periods[1:]) if p - a != 1]
    if holes:
        print(f"  GAP in {drug}: {holes}")
