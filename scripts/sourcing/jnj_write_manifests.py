"""Write the J&J backfill and the three new J&J series.

Only products whose reported line means the same thing throughout are
backfilled. INVEGA SUSTENNA is deliberately left alone: J&J renamed that line
five times between 2014 and 2018 as brands were folded into it, so the earlier
quarters are a different quantity from the one gold records - the same identity
rule that keeps Tyvaso and Tyvaso DPI apart.
"""
import csv, json, os, pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "seed" / "gold" / "source_manifests"
WORK = pathlib.Path(os.environ.get("SOURCING_WORKDIR", "/tmp/gold-sourcing"))
BASE = WORK / "jnj"

# drug -> (manifest, accepted schedule labels, first quarter to backfill)
BACKFILL = {
    "Stelara":  ("jnj_stelara_quarterly.csv",  ["STELARA"], "2014Q3"),
    "Remicade": ("jnj_remicade_quarterly.csv", ["REMICADE"], "2014Q3"),
    "Velcade":  ("jnj_velcade_quarterly.csv",  ["VELCADE"], "2014Q3"),
    "Xarelto":  ("jnj_xarelto_quarterly.csv",  ["XARELTO"], "2014Q3"),
    # The 2016Q4 schedule prints ZYTIGA's figures on a line of their own,
    # below the US/Intl/WW labels rather than beside them, so that quarter is
    # refused rather than realigned by position. The backfill starts after it.
    "Zytiga":   ("jnj_zytiga_quarterly.csv",   ["ZYTIGA"], "2017Q1"),
    "Simponi":  ("jnj_simponi_quarterly.csv",  ["SIMPONI / SIMPONI ARIA", "SIMPONI/SIMPONI ARIA"], "2014Q3"),
    "Imbruvica": ("jnj_imbruvica_quarterly.csv", ["IMBRUVICA"], "2015Q1"),
    "Darzalex": ("jnj_darzalex_quarterly.csv", ["DARZALEX"], "2016Q4"),
    "Tremfya":  ("jnj_tremfya_quarterly.csv",  ["TREMFYA"], "2018Q4"),
}
NEW = {
    "Invokana": ("jnj_invokana_quarterly.csv", ["INVOKANA / INVOKAMET", "INVOKANA/INVOKAMET"], "2015Q1"),
    "Procrit":  ("jnj_procrit_quarterly.csv",  ["PROCRIT / EPREX", "PROCRIT/EPREX"], "2014Q3"),
    "Concerta": ("jnj_concerta_quarterly.csv", ["CONCERTA / METHYLPHENIDATE", "CONCERTA / Methylphenidate",
                                               "CONCERTA / methlyphenidate", "CONCERTA/METHYLPHENIDATE"], "2014Q3"),
}
CONTEXT = (
    "J&J states this product on its own line of the quarterly Sales of Key "
    "Products/Franchises schedule, in US, Intl and WW rows. The WW row is the "
    "one recorded - J&J sums unrounded regional figures and rounds once, so US "
    "+ Intl need not equal the worldwide line it states. The first figure on "
    "the WW row is the quarter the schedule heads as current; the second is "
    "the prior-year comparative and is never read."
)
FIELDS = ["period", "value_reported", "source_url", "source_quote", "derivation",
          "context", "source_type", "source_unit", "source_value_reported"]

quarters = {r["period"]: r for r in json.load(open(BASE / "jnj_quarters.json"))}


def key(label):
    """Labels compared without spacing: some schedules' text layer closes it."""
    return "".join(label.upper().split())


def rows_for(labels, start, end=None):
    out = []
    wanted = {key(l) for l in labels}
    for period in sorted(quarters):
        if period < start or (end and period > end):
            continue
        r = quarters[period]
        label = next((l for l in r["products"] if key(l) in wanted), None)
        if label is None:
            continue
        # Same quote dialect the existing J&J rows use: the schedule's own
        # heading words naming the periods, then the WW row's figures exactly
        # as printed. The percentage columns are dropped and the quote says so,
        # because they sit between the value columns in the PDF.
        tokens = r["tokens"][label]
        year, prior = r["years"][0], r["years"][1]
        if r["ordinal"] == "fourth" and len(tokens) >= 4:
            head = (f"{label} WW fourth quarter {year} and {prior} then twelve "
                    f"months {year} and {prior}, USD millions, "
                    f"percentage-change columns omitted")
            body = " | ".join(tokens[:4])
        elif r["ordinal"] == "first" or len(tokens) < 4:
            head = (f"{label} WW first quarter {year} and {prior}, USD "
                    f"millions, percentage-change columns omitted")
            body = " | ".join(tokens[:2])
        else:
            span = {"second": "six months", "third": "nine months"}[r["ordinal"]]
            head = (f"{label} WW {r['ordinal']} quarter {year} and {prior} then "
                    f"{span} {year} and {prior}, USD millions, "
                    f"percentage-change columns omitted")
            body = " | ".join(tokens[:4])
        out.append({"period": period, "value_reported": r["products"][label],
                    "source_url": r["url"], "source_quote": f"{head} | {body}",
                    "derivation": "direct_reported", "context": CONTEXT,
                    "source_type": "company_ir", "source_unit": "millions",
                    "source_value_reported": r["products"][label]})
    return out


def contiguous(periods):
    idx = sorted(int(p[:4]) * 4 + int(p[-1]) for p in periods)
    return [b for a, b in zip(idx, idx[1:]) if b - a != 1]


added_total = 0
print(f"{'product':<12}{'was':>5}{'added':>7}{'now':>6}   span")
# The 2019Q1 schedule is published only inside the full press release, whose
# layout this pass does not read, so the new series stop at 2018Q4 rather than
# carry a hole.
for drug, (filename, labels, start) in {**BACKFILL, **NEW}.items():
    end = "2018Q4" if drug in NEW else None
    path = SRC / filename
    existing = list(csv.DictReader(open(path))) if path.exists() else []
    have = {r["period"] for r in existing}
    added = [r for r in rows_for(labels, start, end) if r["period"] not in have]
    merged = existing + added
    for row in merged:
        row.setdefault("source_type", "company_ir")
        row.setdefault("source_unit", "millions")
        row.setdefault("source_value_reported", row["value_reported"])
    merged.sort(key=lambda r: (int(r["period"][:4]), int(r["period"][-1])))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in merged)
    gaps = contiguous(r["period"] for r in merged)
    added_total += len(added)
    print(f"{drug:<12}{len(existing):>5}{len(added):>7}{len(merged):>6}   "
          f"{merged[0]['period']} -> {merged[-1]['period']}" + (f"   GAPS {gaps}" if gaps else ""))
print(f"\ntotal added: {added_total}")
