#!/usr/bin/env python
"""Decompose each US single name into buyable and unbuyable exposure.

    python scripts/decompose_single_names.py [--years 5] [--refresh]

Annotates data/swap_map.json in place with replicable_share per single name.

THE QUESTION THIS ANSWERS. A reader holding Nvidia directly asks for "the UCITS
equivalent". There is none, and there cannot be: UCITS diversification rules
(5/10/40, relaxed to 20/35 for index trackers) structurally forbid a
single-stock UCITS fund. Answering "no" and stopping is correct but useless.

What can be answered is how much of the position a UCITS basket actually
reproduces. Decompose the stock's return variance into the part explained by
the broad US market and its sector -- both buyable through UCITS funds already
in this repo's universe -- and the residual, which is company-specific and
cannot be bought outside US situs at any price. That residual is the real
trade, and stating it is more honest than either refusing to answer or quietly
offering a sector fund as though it were the same thing.

METHOD. Weekly total returns, five years. For each name:

    sector_residual = sector_return  -  b * market_return          (step 1)
    stock_return    = a + B1*market_return + B2*sector_residual    (step 2)

Step 1 orthogonalises the sector against the market. Without it the two
regressors are collinear -- a US sector ETF is roughly 0.8 correlated with the
market -- and the individual coefficients become unstable and uninterpretable.
R-squared from step 2 is the share of variance a market-plus-sector
combination reproduces.

BOTH SIDES ARE US-LISTED ON PURPOSE. The market and sector proxies used in the
regression are the US ETFs, not their UCITS counterparts, because London closes
4.5 hours before New York and cross-venue return differences carry a timing
artefact large enough to swamp this measurement (see verify_matches.py, where
it measured 6.03pp on a pair that genuinely tracks). Same-venue data keeps the
decomposition clean. The UCITS line is named separately as the thing to
actually buy; it is not the thing being regressed.

WHAT R-SQUARED IS NOT. It is a variance share over one historical window, not a
promise about future co-movement, and it is not a hedge ratio. A high figure
does not make a sector fund a substitute -- it means the position's swings were
mostly not company-specific during this window. The concentration risk a single
name carries is precisely the residual.
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, ".name_prices.pkl")

MARKET = "SPY"
MIN_WEEKS = 104

# Yahoo INDUSTRY -> US-listed ETF, consulted before the sector table. Keep in
# step with INDUSTRY_TO_INDEX in build_us_situs_map.py: the regressor here and
# the fund named as the proxy there must track the same index, or the number
# describes one thing and the reader is pointed at another. SMH tracks MVIS US
# Listed Semiconductor 25, which is the index SMGB tracks.
#
# This narrows the regressor for the seven semiconductor names and RAISES every
# one of their replicable shares, because a semiconductor index explains a
# semiconductor company better than a broad technology one does:
#
#   MU 42.9 -> 54.3   TXN 36.9 -> 46.7   AMD 48.1 -> 56.0   INTC 24.9 -> 32.0
#   NVDA 63.5 -> 69.3   QCOM 50.1 -> 53.1   AVGO 55.3 -> 57.2
#
# Note the direction: it makes the unbuyable residual SMALLER, which flatters
# swapping. That is the trade accepted here in exchange for a more accurate
# co-movement figure, and it is why the self-inclusion caveat below matters more
# at this width, not less -- MVIS US Listed Semiconductor 25 is a 25-stock index
# and NVDA is one of its largest members, so a good part of that +5.8pp is NVDA
# explaining NVDA. A trial against the broader ICE Semiconductor index (SOXX)
# moved NVDA the other way, to 61.3, which is the same effect seen from the
# other side: the wider the index, the less of itself the stock is explaining.
INDUSTRY_ETF = {
    "Semiconductors": "SMH",
    "Semiconductor Equipment & Materials": "SMH",
}

# Yahoo sector -> US sector ETF used as the regression proxy. These are the
# US-listed lines, deliberately: see the module docstring.
SECTOR_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Real Estate": "VNQ",
    "Communication Services": "XLC",
}


def fetch(symbols, years, refresh):
    import yfinance as yf
    px = pd.DataFrame()
    if os.path.exists(CACHE) and not refresh:
        px = pd.read_pickle(CACHE)
    missing = [s for s in symbols if s not in px.columns]
    if not missing:
        return px
    print(f"  fetching {len(missing)} series ...")
    got, failed = {}, []
    for i, s in enumerate(missing, 1):
        for _ in range(3):
            try:
                h = yf.Ticker(s).history(period=f"{years}y", interval="1wk",
                                         auto_adjust=True)
                if len(h):
                    idx = pd.to_datetime(h.index, utc=True).tz_localize(None).round("D")
                    got[s] = pd.Series(h["Close"].to_numpy(), index=idx)
                    break
            except Exception:
                pass
        else:
            failed.append(s)
        if i % 20 == 0:
            print(f"    {i}/{len(missing)}")
    if failed:
        print(f"  ! no history: {failed}")
    if got:
        add = pd.DataFrame(got)
        add = add[~add.index.duplicated(keep="last")].sort_index()
        px = add if px.empty else px.join(add, how="outer")
        px.to_pickle(CACHE)
    return px


def decompose(r_stock, r_mkt, r_sec):
    cols = {"y": r_stock, "m": r_mkt}
    if r_sec is not None:
        cols["s"] = r_sec
    d = pd.concat(cols, axis=1, join="inner").dropna()
    if len(d) < MIN_WEEKS:
        return {"weeks": int(len(d)), "replicable_share": None,
                "basis": f"only {len(d)} overlapping weeks, need {MIN_WEEKS}"}

    y = d["y"].to_numpy()
    m = d["m"].to_numpy()
    X = [np.ones(len(d)), m]
    used_sector = False
    if "s" in d:
        s = d["s"].to_numpy()
        # step 1: orthogonalise sector against market, else the two regressors
        # are collinear and the coefficients become uninterpretable
        b = np.polyfit(m, s, 1)[0]
        X.append(s - b * m)
        used_sector = True
    X = np.column_stack(X)

    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    if r2 is None:
        return {"weeks": int(len(d)), "replicable_share": None,
                "basis": "degenerate series"}
    r2 = max(0.0, min(1.0, r2))
    return {
        "weeks": int(len(d)),
        "replicable_share": round(r2, 4),
        "idiosyncratic_share": round(1 - r2, 4),
        "market_beta": round(float(coef[1]), 3),
        "sector_beta_orthogonal": round(float(coef[2]), 3) if used_sector else None,
        "stock_ann_vol_pp": round(float(np.std(y, ddof=1) * np.sqrt(52) * 100), 2),
        "idio_ann_vol_pp": round(float(np.std(resid, ddof=1) * np.sqrt(52) * 100), 2),
        "used_sector": used_sector,
        "basis": (f"weekly total returns, {len(d)} weeks, regressed on {MARKET} "
                  f"plus market-orthogonalised sector. R-squared is a variance share "
                  f"over this window, not a hedge ratio and not a forecast. Both "
                  f"regressors HOLD the stock being decomposed, sometimes as one of "
                  f"their largest positions, so part of the share is the position "
                  f"explaining itself."),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    p = os.path.join(DATA, "swap_map.json")
    swap = json.load(open(p, encoding="utf-8"))
    names = swap["single_names"]

    tick = sorted({n["ticker"] for n in names})
    secs = sorted(set(SECTOR_ETF.values()) | set(INDUSTRY_ETF.values()))
    px = fetch(tick + secs + [MARKET], args.years, args.refresh)
    rets = {c: px[c].dropna().pct_change().dropna() for c in px.columns}

    r_mkt = rets.get(MARKET)
    if r_mkt is None:
        raise SystemExit(f"FATAL: no market series for {MARKET}")

    done, skipped = 0, 0
    for n in names:
        r_y = rets.get(n["ticker"])
        if r_y is None:
            n["decomposition"] = {"replicable_share": None,
                                  "basis": "no price history"}
            skipped += 1
            continue
        # industry first, mirroring proxy_index_key() in build_us_situs_map.py
        etf = INDUSTRY_ETF.get(n.get("industry")) or SECTOR_ETF.get(n.get("sector"))
        r_s = rets.get(etf) if etf else None
        dec = decompose(r_y, r_mkt, r_s)
        dec["market_proxy"] = MARKET
        dec["sector_proxy_used"] = etf
        n["decomposition"] = dec
        n["replicable_share"] = dec.get("replicable_share")
        if dec.get("replicable_share") is not None:
            n["verdict_detail"] = (
                f"About {dec['replicable_share'] * 100:.0f} per cent of this position's "
                # rounded, not floored: 259 weeks is 4.98 years and floor made it
                # say "4", so names regressed on a narrower index whose series
                # overlaps a week less described the same window differently
                f"return variance over the past {round(dec['weeks'] / 52)} years came from the "
                f"broad US market and its sector, which UCITS funds can buy. The other "
                f"{dec['idiosyncratic_share'] * 100:.0f} per cent was specific to the "
                f"company and cannot be bought outside US situs at any price -- that "
                f"residual, {dec['idio_ann_vol_pp']:.0f} points of annualised volatility, "
                f"is what a swap actually gives up.")
            done += 1
        else:
            skipped += 1

    vals = [n["replicable_share"] for n in names if n.get("replicable_share") is not None]
    swap["_meta"]["single_name_decomposition"] = {
        "run": "2026-08-02",
        "method": ("weekly total returns regressed on SPY plus a market-orthogonalised "
                   "US sector ETF; R-squared is the share a UCITS market-plus-sector "
                   "combination reproduces. Where Yahoo's INDUSTRY has a covered index "
                   "the narrower line is used -- semiconductor names are regressed on "
                   "SMH rather than XLK, matching the fund named as their proxy."),
        "self_inclusion": (
            "The regressors hold the stock being decomposed, sometimes as one of their "
            "largest positions, so part of every share below is the position explaining "
            "itself. It bites hardest exactly where the number matters most, on the "
            "mega-caps, and it grew when the semiconductor regressor narrowed: NVDA "
            "reads 69.3% against a 25-stock semiconductor index, 63.5% against broad "
            "technology and 61.3% against the wider ICE Semiconductor index. The stock "
            "did not become more replicable; the benchmark became more like the stock."),
        "why_us_listed_proxies": (
            "Both regressors are US-listed. Cross-venue return differences carry a "
            "close-time artefact large enough to swamp this measurement -- it measured "
            "6.03pp on a pair that genuinely tracks. The UCITS line is named separately "
            "as the thing to buy; it is not the thing being regressed."),
        "caveat": ("A variance share over one window, not a hedge ratio and not a "
                   "forecast. A high figure does not make a sector fund a substitute; "
                   "the concentration risk of a single name IS the residual."),
        "names_decomposed": done,
        "names_skipped": skipped,
        "median_replicable_share": round(float(np.median(vals)), 4) if vals else None,
        "min_replicable_share": round(min(vals), 4) if vals else None,
        "max_replicable_share": round(max(vals), 4) if vals else None,
    }

    with open(p, "w", encoding="utf-8") as fh:
        json.dump(swap, fh, indent=1)

    m = swap["_meta"]["single_name_decomposition"]
    print(f"\ndecomposed {done}, skipped {skipped}")
    print(f"  replicable share: median {m['median_replicable_share']}, "
          f"range {m['min_replicable_share']} to {m['max_replicable_share']}")
    ranked = sorted([n for n in names if n.get("replicable_share") is not None],
                    key=lambda n: n["replicable_share"])
    print("  most company-specific:")
    for n in ranked[:5]:
        print(f"    {n['ticker']:6s} {n['replicable_share'] * 100:4.0f}% replicable  "
              f"({n['decomposition']['idio_ann_vol_pp']:.0f}pp idio vol)")
    print("  least company-specific:")
    for n in ranked[-5:]:
        print(f"    {n['ticker']:6s} {n['replicable_share'] * 100:4.0f}% replicable  "
              f"({n['decomposition']['idio_ann_vol_pp']:.0f}pp idio vol)")


if __name__ == "__main__":
    main()
