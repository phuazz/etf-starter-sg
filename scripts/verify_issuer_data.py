#!/usr/bin/env python
"""Fetch authoritative reference data for the UCITS universe.

    python scripts/verify_issuer_data.py [--refresh]

Writes data/issuer_verified.json, consumed by build_ucits_universe.py as the
top-priority source for domicile and expense ratio.

Two ungated sources, each used only for what it is authoritative on:

  LSE public instrument API
      ISIN, country of incorporation, official name, quote currency, for any
      London-listed line. ISIN is what the house rule requires for domicile --
      a fund-family string is an inference, an ISIN prefix is the identifier
      itself. This replaces the name-string heuristic entirely.

  Vanguard UK product API
      Ongoing charges figure and issuer-stated domicile. Yahoo returns no
      expense ratio for any Vanguard Ireland line, which is a systematic gap
      rather than a random one, so it needs a source of its own.

Neither sits behind a terms-of-use acceptance gate. The iShares product list
does, and is deliberately not used.

Where a fetched figure DISAGREES with one already curated in the repo, the
script records both and warns. It does not silently pick, because a silent
pick is how a stale figure survives a verification pass.
"""
import argparse
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FETCHED = "2026-08-02"

LSE_URL = "https://api.londonstockexchange.com/api/gw/lse/instruments/alldata/{}"
VG_URL = "https://www.vanguardinvestor.co.uk/api/productList"

H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.londonstockexchange.com/",
}

# London ticker -> Vanguard product-list id slug. Explicit rather than fuzzy
# name matching, so a mis-mapping is visible in review rather than silent.
# Several London tickers share one share class: they are the same share class
# quoted in a different trading currency, so the charge and domicile are
# identical. VAGU is absent on purpose -- the USD-hedged line does not appear
# in Vanguard's UK product list, so it stays unresolved rather than being
# assigned the GBP-hedged figure.
VANGUARD_SLUG = {
    "VUAA": "vanguard-s-and-p-500-ucits-etf-usd-accumulating",
    "VUAG": "vanguard-s-and-p-500-ucits-etf-usd-accumulating",
    "VUSA": "vanguard-s-and-p-500-ucits-etf-usd-distributing",
    "VWRA": "vanguard-ftse-all-world-ucits-etf-usd-accumulating",
    "VWRP": "vanguard-ftse-all-world-ucits-etf-usd-accumulating",
    "VWRL": "vanguard-ftse-all-world-ucits-etf-usd-distributing",
    "VHVG": "vanguard-ftse-developed-world-ucits-etf-usd-accumulating",
    "VHVE": "vanguard-ftse-developed-world-ucits-etf-usd-accumulating",
    "VEVE": "vanguard-ftse-developed-world-ucits-etf-usd-distributing",
    "VFEG": "vanguard-ftse-emerging-markets-ucits-etf-usd-accumulating",
    "VFEM": "vanguard-ftse-emerging-markets-ucits-etf-usd-distributing",
    "VEUR": "vanguard-ftse-developed-europe-ucits-etf-eur-distributing",
    "VJPN": "vanguard-ftse-japan-ucits-etf-usd-distributing",
    "VAGP": "vanguard-global-aggregate-bond-ucits-etf-gbp-hedged-accumulating",
}


def fetch_lse(tickers, cache, refresh):
    for n, t in enumerate(tickers, 1):
        if not refresh and t in cache:
            continue
        try:
            r = requests.get(LSE_URL.format(t), headers=H, timeout=25)
            if r.status_code == 200:
                d = r.json()
                cache[t] = {
                    "isin": d.get("isin"),
                    "country": d.get("country"),
                    "lse_name": d.get("name"),
                    "description": d.get("description"),
                    "currency": d.get("currency"),
                    "instrumenttype": d.get("instrumenttype"),
                    "fundsType": d.get("fundsType"),
                    "issuername": d.get("issuername"),
                }
            else:
                cache[t] = {"_error": f"HTTP {r.status_code}"}
        except Exception as exc:
            cache[t] = {"_error": f"{type(exc).__name__}: {exc}"}
        if n % 20 == 0:
            print(f"  lse {n}/{len(tickers)}")
        time.sleep(0.25)
    return cache


def fetch_vanguard():
    try:
        r = requests.get(VG_URL, headers=H, timeout=40)
        if r.status_code != 200:
            print(f"  ! Vanguard HTTP {r.status_code}")
            return {}
        return {x["id"]: x for x in r.json() if x.get("id")}
    except Exception as exc:
        print(f"  ! Vanguard {type(exc).__name__}: {exc}")
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    with open(os.path.join(DATA, "ucits_seed.json"), encoding="utf-8") as fh:
        seed = json.load(fh)
    tickers = [c["t"] for c in seed["candidates"]]
    curated = seed.get("curated_overrides", {})

    cpath = os.path.join(DATA, ".lse_cache.json")
    cache = {}
    if os.path.exists(cpath) and not args.refresh:
        with open(cpath, encoding="utf-8") as fh:
            cache = json.load(fh)

    print(f"LSE instrument data for {len(tickers)} tickers ...")
    cache = fetch_lse(tickers, cache, args.refresh)
    with open(cpath, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)

    print("Vanguard product list ...")
    vg = fetch_vanguard()
    print(f"  {len(vg)} products")

    out, warnings = {}, []
    for t in tickers:
        rec = {}
        L = cache.get(t, {})
        if L.get("isin"):
            isin = L["isin"]
            rec["isin"] = isin
            rec["isin_src"] = "LSE instrument API"
            # The house rule: domicile is the ISIN prefix, not an inference
            # from a fund-family string.
            rec["domicile"] = isin[:2]
            rec["domicile_src"] = "ISIN prefix (LSE instrument API)"
            rec["lse_name"] = L.get("lse_name")
            rec["lse_currency"] = L.get("currency")
            if L.get("country") and L["country"] != isin[:2]:
                warnings.append({
                    "ticker": t,
                    "warn": f"LSE country {L['country']} disagrees with ISIN prefix {isin[:2]}",
                })
        else:
            warnings.append({"ticker": t, "warn": f"no ISIN from LSE ({L.get('_error', 'absent')})"})

        slug = VANGUARD_SLUG.get(t)
        if slug:
            v = vg.get(slug)
            if v and v.get("ocfValue") is not None:
                rec["ter"] = float(v["ocfValue"])
                rec["ter_src"] = f"Vanguard UK product API, {slug}"
                if v.get("domicileType"):
                    rec["issuer_domicile_note"] = v["domicileType"]
            else:
                warnings.append({"ticker": t, "warn": f"Vanguard slug {slug!r} not found or no OCF"})

        # Disagreement with an already-curated figure is reported, never
        # silently resolved -- a silent pick is how a stale number survives.
        if t in curated and "ter" in curated[t] and "ter" in rec:
            if abs(curated[t]["ter"] - rec["ter"]) > 1e-9:
                warnings.append({
                    "ticker": t,
                    "warn": (f"TER DISAGREEMENT: curated {curated[t]['ter']} "
                             f"vs issuer {rec['ter']} -- issuer figure used, "
                             f"curated.json may be stale"),
                    "curated": curated[t]["ter"],
                    "issuer": rec["ter"],
                })
        if rec:
            out[t] = rec

    doc = {
        "_meta": {
            "purpose": "Authoritative reference data. Top-priority input to build_ucits_universe.py for domicile (ISIN) and expense ratio.",
            "fetched": FETCHED,
            "builder": "scripts/verify_issuer_data.py",
            "sources": {
                "isin_domicile": "London Stock Exchange public instrument API -- authoritative, ungated",
                "vanguard_ter": "Vanguard UK product list API -- issuer-published ongoing charges figure",
            },
            "not_used": "iShares product list, which sits behind a terms-of-use acceptance gate.",
            "counts": {
                "tickers": len(tickers),
                "with_isin": sum(1 for v in out.values() if v.get("isin")),
                "with_issuer_ter": sum(1 for v in out.values() if v.get("ter") is not None),
                "warnings": len(warnings),
            },
        },
        "data": out,
        "warnings": warnings,
    }

    path = os.path.join(DATA, "issuer_verified.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)

    c = doc["_meta"]["counts"]
    print(f"\nISIN resolved      : {c['with_isin']}/{c['tickers']}")
    print(f"issuer TER resolved: {c['with_issuer_ter']}")
    print(f"warnings           : {c['warnings']}")
    for w in warnings:
        if "DISAGREEMENT" in w["warn"]:
            print(f"  !! {w['ticker']}: {w['warn']}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
