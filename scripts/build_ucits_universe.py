#!/usr/bin/env python
"""Validate and enrich the curated UCITS candidate seed into a built universe.

    python scripts/build_ucits_universe.py            # uses cache where present
    python scripts/build_ucits_universe.py --refresh  # re-fetch everything

Reads  data/ucits_seed.json  +  data/index_map.json
Writes data/ucits_universe.json  (built artefact, dated snapshot)

Two independent sources must agree before a candidate enters the universe:

  1. OpenFIGI  -- the ticker actually exists as a listed security on the London
                  exchange (exchCode LN). Bloomberg-sourced reference data.
  2. Yahoo     -- legal name, quote currency, expense ratio, fund size.

Anything failing either check is DROPPED and recorded in `warnings`, never
shipped with a guessed value.

--- The two things domicile is doing, kept separate on purpose ---

SITUS (drives the estate-tax verdict; a wrong answer here is catastrophic
because it tells someone they are safe when they are not). UCITS is an EU
framework, so a fund cannot be both UCITS-authorised and US-domiciled. The
verdict therefore follows structurally from UCITS status, which is visible in
the fund's own legal name, and needs no ISIN lookup.

WITHHOLDING DOMICILE (drives the dividend-drag estimate; a wrong answer here
shows a wrong number, which is bad but visible). Irish funds receive 15 per
cent US dividend withholding under the US-Ireland treaty; others get 30 per
cent. This cannot be derived structurally, so it is resolved per line from the
fund-family string and left explicitly UNVERIFIED where that string is silent.
Unverified lines carry the conservative 30 per cent in arithmetic and must be
rendered with a verify flag rather than asserted.
"""
import argparse
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, ".ucits_cache.json")

BUILD_DATE = "2026-08-02"  # session date; snapshot stamp, not computed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Fund-family strings that positively establish Irish domicile. Deliberately
# strict: a string that merely *implies* Ireland does not qualify. Everything
# else falls through to unverified and the conservative 30 per cent rate.
IE_PATTERNS = (
    re.compile(r"\bireland\b", re.I),
    re.compile(r"\(\s*ie\s*\)", re.I),
    re.compile(r"\birish\b", re.I),
)

# Strings that positively establish Luxembourg domicile. These land on the same
# 30 per cent rate as "unverified" but are a DIFFERENT state: here we know the
# rate is right, rather than defaulting to it because we could not check. The
# UI must not show a verify flag on these.
LU_PATTERNS = (
    re.compile(r"\bluxembourg\b", re.I),
    re.compile(r"\(\s*lu\s*\)", re.I),
)


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


# ---- source 1: OpenFIGI (listing exists on the London exchange) -----------
def figi_validate(tickers, sleep=2.6):
    """Return {ticker: figi_record_or_None}. Batches of 10 (unauthenticated cap)."""
    out = {}
    url = "https://api.openfigi.com/v3/mapping"
    for i in range(0, len(tickers), 10):
        chunk = tickers[i:i + 10]
        body = [{"idType": "TICKER", "idValue": t, "exchCode": "LN"} for t in chunk]
        try:
            r = requests.post(url, headers={"Content-Type": "application/json"},
                              json=body, timeout=40)
            if r.status_code != 200:
                print(f"  ! OpenFIGI HTTP {r.status_code} on batch {i//10}", file=sys.stderr)
                for t in chunk:
                    out[t] = None
                continue
            for t, res in zip(chunk, r.json()):
                recs = res.get("data") or []
                out[t] = recs[0] if recs else None
        except Exception as exc:
            print(f"  ! OpenFIGI {type(exc).__name__}: {exc}", file=sys.stderr)
            for t in chunk:
                out[t] = None
        if i + 10 < len(tickers):
            time.sleep(sleep)  # unauthenticated rate limit is 25 requests/minute
    return out


# ---- source 2: Yahoo (legal name, currency, TER, fund size) ---------------
def yahoo_enrich(tickers, cache, refresh=False):
    import yfinance as yf
    for n, t in enumerate(tickers, 1):
        key = f"{t}.L"
        if not refresh and key in cache:
            continue
        try:
            info = yf.Ticker(key).info or {}
            cache[key] = {
                "longName": info.get("longName"),
                "shortName": info.get("shortName"),
                "quoteType": info.get("quoteType"),
                "currency": info.get("currency"),
                "exchange": info.get("exchange"),
                "fundFamily": info.get("fundFamily"),
                "netExpenseRatio": info.get("netExpenseRatio"),
                "totalAssets": info.get("totalAssets"),
                "yield": info.get("yield"),
            }
        except Exception as exc:
            cache[key] = {"_error": f"{type(exc).__name__}: {exc}"}
        if n % 10 == 0:
            print(f"  yahoo {n}/{len(tickers)}")
            _save_cache(cache)
    _save_cache(cache)
    return cache


def _save_cache(cache):
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)


def resolve_wht_domicile(fund_family):
    """Return (domicile, confidence, us_div_wht_rate).

    Three states, not two. Ireland yields the 15 per cent treaty rate.
    Luxembourg yields 30 per cent and we KNOW it. Silence yields 30 per cent
    because that is the conservative direction, but it is a different state --
    the number happens to match Luxembourg's while resting on nothing, so the
    UI must flag it for verification rather than assert it.

    The asymmetry is deliberate: defaulting to 30 understates a line that turns
    out to be Irish, which shows a fund as worse than it is. Defaulting to 15
    would overstate, which is the direction that misleads someone into a swap.
    """
    if fund_family:
        if any(p.search(fund_family) for p in IE_PATTERNS):
            return "IE", "name_string", 0.15
        if any(p.search(fund_family) for p in LU_PATTERNS):
            return "LU", "name_string", 0.30
    return "unverified", "none", 0.30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore the Yahoo cache")
    args = ap.parse_args()

    seed = load("ucits_seed.json")
    index_map = load("index_map.json")
    known_idx = set(index_map["indices"])

    cands = seed["candidates"]
    tickers = [c["t"] for c in cands]
    print(f"candidates: {len(tickers)}")

    # every seed row must point at a real index key -- a typo here would
    # silently orphan a line from the match engine
    bad_idx = sorted({c["idx"] for c in cands} - known_idx)
    if bad_idx:
        sys.exit(f"FATAL: seed references unknown index keys: {bad_idx}")

    print("validating listings via OpenFIGI ...")
    figi = figi_validate(tickers)
    print(f"  resolved on LN: {sum(1 for v in figi.values() if v)}/{len(tickers)}")

    cache = {}
    if os.path.exists(CACHE) and not args.refresh:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    print("enriching via Yahoo ...")
    cache = yahoo_enrich(tickers, cache, refresh=args.refresh)

    funds, warnings = [], []
    for c in cands:
        t = c["t"]
        y = cache.get(f"{t}.L", {})
        f = figi.get(t)

        if not f:
            warnings.append({"ticker": t, "drop": "not listed on LN per OpenFIGI"})
            continue
        name = y.get("longName") or y.get("shortName")
        if not name:
            warnings.append({"ticker": t, "drop": "no legal name from Yahoo"})
            continue
        # ETCs are debt securities, not funds, and Yahoo classifies them as
        # EQUITY. Rejecting them on quoteType would silently drop gold -- which
        # matters, because the mainstream Singapore gold routes (SGX's GSD/O87
        # and the US-listed GLD) are exactly the holdings a reader is trying to
        # get out of. Let them through, but never as "safe": their situs turns
        # on the trust structure, not on UCITS status, and is left unresolved.
        want = "EQUITY" if c["structure"] == "etc" else "ETF"
        if y.get("quoteType") != want:
            warnings.append({"ticker": t, "drop": f"quoteType={y.get('quoteType')!r}, expected {want}"})
            continue

        # --- situs: structural, from UCITS status -------------------------
        is_ucits = bool(re.search(r"\bUCITS\b", name)) and c["structure"] == "ucits_etf"
        if c["structure"] == "ucits_etf" and not is_ucits:
            # seed claimed UCITS but the legal name does not say so -- do not
            # guess, and do not ship a situs verdict we cannot stand behind
            warnings.append({"ticker": t, "drop": f"seed says UCITS but legal name lacks it: {name!r}"})
            continue

        situs = "non_us" if is_ucits else "unresolved"

        dom, dom_conf, wht = resolve_wht_domicile(y.get("fundFamily"))

        ccy = y.get("currency")
        ov = seed.get("curated_overrides", {}).get(t, {})
        ter = ov.get("ter", y.get("netExpenseRatio"))
        ter_src = ov.get("ter_src") if "ter" in ov else (
            "yahoo" if y.get("netExpenseRatio") is not None else None)
        rec = {
            "ticker": t,
            "yahoo": f"{t}.L",
            "name": name,
            "index_key": c["idx"],
            "index_label": index_map["indices"][c["idx"]]["label"],
            "index_family": index_map["indices"][c["idx"]]["family"],
            "issuer_hint": c["iss"],
            "fund_family": y.get("fundFamily"),
            "structure": c["structure"],
            "income": "Accumulating" if c["dist"] == "acc" else "Distributing",
            "exchange": "LSE",
            "ccy": ccy,
            # GBp lines quote in pence. Comparing a GBp line against a USD line
            # on raw price returns yields pure FX noise that reads as tracking
            # failure -- the match engine must convert or refuse.
            "ccy_is_pence": ccy == "GBp",
            "ter": ter,
            "ter_src": ter_src,
            "aum_usd": y.get("totalAssets"),
            "yield": y.get("yield"),
            "is_ucits": is_ucits,
            "situs": situs,
            "estate_tax_exposed": False if situs == "non_us" else None,
            "wht_domicile": dom,
            "wht_domicile_conf": dom_conf,
            "us_div_wht_rate": wht,
            "figi": f.get("figi"),
            "figi_name": f.get("name"),
        }
        if rec["ter"] is None:
            warnings.append({"ticker": t, "flag": "no TER from Yahoo -- needs issuer verification"})
        if situs == "unresolved":
            warnings.append({"ticker": t, "flag": "ETC, not a UCITS fund -- situs unresolved, do not present as safe"})
        if dom_conf == "none":
            warnings.append({"ticker": t, "flag": "Irish domicile unverified -- using conservative 30% withholding"})
        funds.append(rec)

    by_index = {}
    for f in funds:
        by_index.setdefault(f["index_key"], []).append(f["ticker"])

    out = {
        "_meta": {
            "purpose": "BUILT universe of validated non-US-situs candidate lines. Do not hand-edit -- edit data/ucits_seed.json and rebuild.",
            "built": BUILD_DATE,
            "builder": "scripts/build_ucits_universe.py",
            "sources": {
                "listing_existence": "OpenFIGI v3 mapping, exchCode LN",
                "name_ccy_ter_aum": "Yahoo Finance quote info",
            },
            "refresh_cadence": "Static snapshot. The UCITS universe is near-static -- expense ratios move about once a year and launches trickle. This is deliberately NOT wired into the weekly price Action; refresh manually at the quarterly review.",
            "situs_basis": "UCITS status, read from the fund's legal name. A fund cannot be both UCITS-authorised and US-domiciled, so this settles the estate-tax verdict structurally. ETCs are not UCITS funds and are marked situs=unresolved.",
            "withholding_basis": "Irish domicile from the fund-family string only. Unverified lines carry 30 per cent, the conservative direction, and MUST be rendered with a verify flag rather than asserted.",
            "counts": {
                "candidates": len(cands),
                "validated": len(funds),
                "dropped": sum(1 for w in warnings if "drop" in w),
                "indices_covered": len(by_index),
                "ter_missing": sum(1 for f in funds if f["ter"] is None),
                "domicile_unverified": sum(1 for f in funds if f["wht_domicile_conf"] == "none"),
                "situs_unresolved": sum(1 for f in funds if f["situs"] != "non_us"),
            },
        },
        "funds": sorted(funds, key=lambda r: (r["index_key"], r["ticker"])),
        "by_index": by_index,
        "warnings": warnings,
    }

    path = os.path.join(DATA, "ucits_universe.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    m = out["_meta"]["counts"]
    print(f"\nvalidated {m['validated']}/{m['candidates']}  "
          f"(dropped {m['dropped']})  across {m['indices_covered']} indices")
    print(f"  TER missing        : {m['ter_missing']}")
    print(f"  domicile unverified: {m['domicile_unverified']}")
    print(f"  situs unresolved   : {m['situs_unresolved']}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
