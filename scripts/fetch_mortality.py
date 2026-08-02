#!/usr/bin/env python
"""Fetch Singapore age-specific death rates for the risk-scale panel.

    python scripts/fetch_mortality.py

Writes data/mortality_sg.json

Source: SingStat Table Builder table M810141, "Age-Specific Death Rates,
Annual", resident population (citizens and permanent residents). Official and
ungated.

WHY THE TOOL NEEDS THIS. US estate tax on a US-situs holding is a certain
liability conditional on death and nothing at all otherwise. Quoting only the
headline number answers "how much" while leaving "how likely" entirely to the
reader's imagination, which in practice means fear. Quoting only the expected
value answers "how likely" and invites someone to shrug off a swap that costs
them nothing. Both belong on the page, side by side.

WHAT THIS IS NOT. An age-specific death rate is deaths in a year per 1,000
residents in that age band. It is a population average, not a personal
probability -- it says nothing about any individual's health, and it is not a
forecast. It is also a five-year band, so it is stated as the band's rate
rather than interpolated to a single year of age, which would manufacture
precision the source does not carry.

The expected-cost figure this feeds is an ILLUSTRATION OF SCALE, not advice,
and it must never be presented as the reason to act or not act. The variance
is the point: a low annual probability attached to a six-figure liability is
exactly the shape of risk people insure against rather than average away.
"""
import json
import os

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

TABLE = "M810141"
URL = f"https://tablebuilder.singstat.gov.sg/api/table/tabledata/{TABLE}"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

SEX_HEADINGS = {
    "Total Age Specific Death Rate": "total",
    "Male Age Specific Death Rate": "male",
    "Female Age Specific Death Rate": "female",
}

# Only the disjoint five-year bands. The table also carries cumulative
# "70 Years & Over" style rows, which would double-count if mixed in.
def is_band(text):
    return " - " in text and text.endswith("Years")


def main():
    r = requests.get(URL, headers=H, params={"limit": 5000, "timeFilter": "2025,2024"},
                     timeout=60)
    r.raise_for_status()
    d = r.json()["Data"]
    rows = d.get("row", [])

    years = sorted({c["key"] for x in rows for c in (x.get("columns") or [])})
    latest = years[-1]
    print(f"table {TABLE}: {d.get('title')}  latest year {latest}")

    out, sex = {}, None
    for x in rows:
        t = x.get("rowText", "").strip()
        if t in SEX_HEADINGS:
            sex = SEX_HEADINGS[t]
            out[sex] = {}
            continue
        if sex is None or not is_band(t):
            continue
        vals = {c["key"]: c["value"] for c in (x.get("columns") or [])}
        v = vals.get(latest)
        if v in (None, "-", ""):
            continue
        lo = int(t.split(" - ")[0])
        out[sex][str(lo)] = {"band": t, "age_from": lo,
                             "rate_per_1000": float(v)}

    doc = {
        "_meta": {
            "purpose": "Age-specific death rates, for showing the SCALE of US estate-tax risk beside its headline size.",
            "source": ("SingStat Table Builder, table M810141 'Age-Specific Death Rates, "
                       "Annual', resident population (citizens and permanent residents)"),
            "source_url": f"https://tablebuilder.singstat.gov.sg/table/TS/{TABLE}",
            "year": latest,
            "fetched": "2026-08-02",
            "units": "deaths per 1,000 residents in the age band, per year",
            "what_this_is_not": (
                "A population average, not a personal probability. It reflects nothing "
                "about an individual's health and is not a forecast. Five-year bands are "
                "kept as bands rather than interpolated to single years of age, which "
                "would manufacture precision the source does not carry."),
            "how_to_present": (
                "Always beside the headline liability, never instead of it. A low annual "
                "probability attached to a large liability is the shape of risk people "
                "insure against rather than average away, and the page must say so. This "
                "is an illustration of scale, not a recommendation to act or not act."),
            "bands": {k: len(v) for k, v in out.items()},
        },
        "rates": out,
    }

    p = os.path.join(DATA, "mortality_sg.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    for s in out:
        print(f"  {s:7s} {len(out[s])} bands, "
              f"age 45 = {out[s].get('45', {}).get('rate_per_1000')} per 1,000")
    print(f"-> {p}")


if __name__ == "__main__":
    main()
