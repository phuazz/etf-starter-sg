#!/usr/bin/env python
"""Validate the US-situs side of the swap map.

    python scripts/build_us_situs_map.py [--refresh]

Writes data/us_situs_map.json -- the holdings a Singapore investor is trying to
move OUT of, each carrying the join key that finds its non-US-situs alternative.

Two populations, deliberately handled differently:

  ETFs        join on index_key. A US-domiciled index fund has, in most cases,
              a UCITS fund tracking the same index. This is a lookup.

  SINGLE      join on SECTOR only, and the answer is never "here is the
  NAMES       equivalent". UCITS diversification rules (5/10/40, relaxed to
              20/35 for index trackers) structurally forbid a single-stock
              UCITS fund. No amount of searching produces one. What the tool
              can return is a decomposition -- the share of the position's
              variance that is market and sector, which a UCITS basket can
              buy, against the share that is name-specific and cannot be
              bought without US situs at any price.

Situs determination is the mirror of the UCITS side. A US ISIN prefix settles
it outright. Where Yahoo returns no ISIN, a US-listed non-UCITS fund or a
US-incorporated company is treated as exposed -- and note the asymmetry: on
this side an error points towards "you are exposed", which prompts an
unnecessary look rather than a false sense of safety.

ADRs are excluded rather than guessed. An ADR over a foreign company is not
straightforwardly US-situs and the treatment is contested; shipping a verdict
on them would be inventing certainty the source material does not support.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, ".us_situs_cache.json")
BUILD_DATE = "2026-08-02"

# Yahoo sector -> canonical sector index key in data/index_map.json. A sector
# only appears here if the validated UCITS universe actually contains a line
# for it; the builder checks this and refuses to start otherwise. Names in an
# uncovered sector fall through to "no sector proxy" rather than being quietly
# rehomed into an adjacent one.
SECTOR_TO_INDEX = {
    "Communication Services": "us_comm_services_sector",
    "Technology": "us_tech_sector",
    "Healthcare": "us_healthcare_sector",
    "Financial Services": "us_financials_sector",
    "Consumer Cyclical": "us_consumer_disc_sector",
    "Consumer Defensive": "us_consumer_staples_sector",
    "Energy": "us_energy_sector",
    "Industrials": "us_industrials_sector",
    "Utilities": "us_utilities_sector",
    "Basic Materials": "us_materials_sector",
    "Real Estate": "us_reits",
}

# US-domiciled ETFs commonly held from Singapore, with the index each tracks.
US_ETFS = [
    ("SPY", "sp500"), ("VOO", "sp500"), ("IVV", "sp500"), ("SPLG", "sp500"),
    ("QQQ", "nasdaq100"), ("QQQM", "nasdaq100"),
    # ITOT is the S&P total-market fund, not the MSCI one. Its own name says so,
    # and the provider check that would have caught it ran on the UCITS side only.
    ("VTI", "crsp_us_total"), ("ITOT", "sp_total_market"),
    ("VT", "ftse_all_world"), ("ACWI", "msci_acwi"),
    # VWO -- VERIFIED against Vanguard's own fund profile, 2026-08-06. It tracks
    # the FTSE Emerging Markets All Cap China A Inclusion Index (4,852
    # constituents), not the MSCI series it carried here for years. The issuer's
    # benchmark history also explains how that survived so long: VWO really did
    # track MSCI Emerging Markets, until 9 January 2013.
    #
    # The all-cap key is not pedantry. Vanguard's UCITS line (VFEG/VFEM) tracks
    # the FTSE Emerging INDEX -- "large and mid-sized company stocks", 2,289
    # constituents, per the factsheet for IE00B3VVMM84 dated 30 June 2026.
    # Pointing both at one key produced a tier-1 EXACT INDEX badge across a
    # ~2,500-company small-cap gap: the same failure as MTUM, on the size axis
    # instead of the geography one, and introduced while fixing the provider.
    # They are separate keys in near families now, so the swap is still offered
    # and the difference is named.
    ("VEA", "ftse_dev_exus"), ("VWO", "ftse_emerging_all_cap"), ("IEMG", "msci_em_imi"),
    ("EFA", "msci_eafe"), ("URTH", "msci_world"),
    ("IWM", "russell2000"), ("IJR", "sp_smallcap600"),
    ("SCHD", "dj_us_dividend_100"), ("VYM", "ftse_high_div_yield"),
    ("VIG", "sp_us_dividend_growers"), ("NOBL", "sp_us_dividend_aristocrats"),
    ("XLK", "us_tech_sector"), ("VGT", "us_tech_sector"),
    ("XLV", "us_healthcare_sector"), ("XLF", "us_financials_sector"),
    ("XLE", "us_energy_sector"), ("XLY", "us_consumer_disc_sector"),
    ("XLP", "us_consumer_staples_sector"), ("XLI", "us_industrials_sector"),
    ("XLU", "us_utilities_sector"), ("XLB", "us_materials_sector"),
    ("SOXX", "ice_semiconductor"), ("SMH", "mvis_us_semis"),
    ("VNQ", "us_reits"),
    ("GLD", "gold_spot"), ("IAU", "gold_spot"),
    ("AGG", "us_aggregate_bond"), ("BND", "us_aggregate_bond"),
    ("BNDW", "global_aggregate_bond"),
    ("TLT", "us_treasury_long"), ("IEF", "us_treasury_7_10"),
    ("SHY", "us_treasury_1_3"), ("TIP", "us_tips"),
    ("LQD", "usd_corporate_ig"), ("HYG", "usd_corporate_hy"),
    # All three are iShares MSCI *USA* factor funds and were mapped to the
    # *World* index of the same factor -- a tier-1 "exact index match" against a
    # fund holding roughly 30% non-US. Same provider, so the provider check
    # could not see it; nothing checked the universe. It also fed the wrong
    # us_content (0.6-0.7 instead of 1.0) into the withholding figure.
    ("MTUM", "usa_momentum"), ("QUAL", "usa_quality"), ("VLUE", "usa_value"),
]

# Widely held US-incorporated single names. Sector comes from Yahoo, not from
# this list -- the list only decides who is in scope.
US_NAMES = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "LLY",
    "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "PG", "JNJ", "ABBV", "WMT",
    "NFLX", "AMD", "CRM", "ORCL", "KO", "PEP", "TMO", "MRK", "ADBE", "CSCO",
    "ACN", "MCD", "ABT", "INTC", "QCOM", "DIS", "TXN", "PFE", "PM", "IBM",
    "GE", "CAT", "BA", "NKE", "UBER", "PLTR", "MU", "INTU", "NOW", "GS",
]


def fetch(tickers, cache, refresh):
    import yfinance as yf
    for n, t in enumerate(tickers, 1):
        if not refresh and t in cache:
            continue
        try:
            T = yf.Ticker(t)
            info = T.info or {}
            try:
                isin = T.isin
            except Exception:
                isin = None
            cache[t] = {
                "longName": info.get("longName") or info.get("shortName"),
                "quoteType": info.get("quoteType"),
                "currency": info.get("currency"),
                "exchange": info.get("exchange"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "country": info.get("country"),
                "netExpenseRatio": info.get("netExpenseRatio"),
                "totalAssets": info.get("totalAssets"),
                "yield": info.get("yield"),
                "isin": None if isin in ("-", "") else isin,
            }
        except Exception as exc:
            cache[t] = {"_error": f"{type(exc).__name__}: {exc}"}
        if n % 15 == 0:
            print(f"  {n}/{len(tickers)}")
            _save(cache)
    _save(cache)
    return cache


def _save(cache):
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    with open(os.path.join(DATA, "index_map.json"), encoding="utf-8") as fh:
        index_map = json.load(fh)
    known = set(index_map["indices"])

    bad = sorted({k for _, k in US_ETFS} - known)
    if bad:
        sys.exit(f"FATAL: US ETF list references unknown index keys: {bad}")

    # A sector mapping that points at an index with no validated UCITS line
    # would promise a proxy the tool cannot deliver. Drop those mappings so the
    # name honestly reports "no sector proxy" instead.
    upath = os.path.join(DATA, "ucits_universe.json")
    if not os.path.exists(upath):
        sys.exit("FATAL: build the UCITS universe first (scripts/build_ucits_universe.py)")
    with open(upath, encoding="utf-8") as fh:
        covered = set(json.load(fh)["by_index"])
    for sector, idx in list(SECTOR_TO_INDEX.items()):
        if idx not in covered:
            print(f"  ! sector {sector!r} -> {idx} has no validated UCITS line; no proxy will be offered")
            del SECTOR_TO_INDEX[sector]

    cache = {}
    if os.path.exists(CACHE) and not args.refresh:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)

    all_t = [t for t, _ in US_ETFS] + US_NAMES
    print(f"fetching {len(all_t)} US lines ...")
    cache = fetch(all_t, cache, args.refresh)

    etfs, names, warnings = [], [], []

    for t, idx in US_ETFS:
        y = cache.get(t, {})
        nm = y.get("longName")
        if not nm or y.get("quoteType") != "ETF":
            warnings.append({"ticker": t, "drop": f"quoteType={y.get('quoteType')!r} name={nm!r}"})
            continue
        if re.search(r"\bUCITS\b", nm):
            warnings.append({"ticker": t, "drop": "legal name says UCITS -- not a US-situs line"})
            continue
        isin = y.get("isin")
        if isin and isin.startswith("US"):
            basis = "isin"
        elif isin:
            warnings.append({"ticker": t, "drop": f"non-US ISIN {isin} -- not US-situs, remove from list"})
            continue
        else:
            basis = "us_listed_non_ucits"
        etfs.append({
            "ticker": t, "name": nm, "kind": "etf", "index_key": idx,
            "index_label": index_map["indices"][idx]["label"],
            "index_family": index_map["indices"][idx]["family"],
            "ccy": y.get("currency"), "ter": y.get("netExpenseRatio"),
            "aum_usd": y.get("totalAssets"), "yield": y.get("yield"),
            "isin": isin, "situs": "us", "situs_basis": basis,
            "estate_tax_exposed": True,
            # A Singapore resident holding a US-domiciled fund suffers the full
            # statutory 30 per cent on US dividends -- there is no US-Singapore
            # treaty rate to reduce it. This is the number the UCITS side gets
            # compared against.
            "us_div_wht_rate": 0.30,
        })
        if basis != "isin":
            warnings.append({"ticker": t, "flag": "US domicile inferred from listing, no ISIN returned"})

    for t in US_NAMES:
        y = cache.get(t, {})
        nm = y.get("longName")
        if not nm or y.get("quoteType") != "EQUITY":
            warnings.append({"ticker": t, "drop": f"quoteType={y.get('quoteType')!r} name={nm!r}"})
            continue
        # Yahoo's ISIN field is NOT trustworthy for US single names -- it
        # returns the identifier of a same-ticker listing on another exchange.
        # Observed 2026-08-02: Alphabet, Broadcom and Walmart all came back
        # with Canadian ISINs and Philip Morris with an Argentine one. It is
        # recorded for reference and never acted on. Incorporation country is
        # the screen instead.
        isin = y.get("isin")
        isin_suspect = bool(isin and not isin.startswith("US"))

        ctry = y.get("country")
        if ctry and ctry != "United States":
            # Not an error and not a drop -- this is one of the more useful
            # things the tool can tell someone. A US-listed, foreign-incorporated
            # company (Accenture, Medtronic, Linde) is NOT US-situs, so a holder
            # worrying about estate tax on it may have nothing to worry about.
            # Incorporation, not Yahoo's address field, is the legal test, so it
            # ships as "likely" with a verify flag rather than as a verdict.
            names.append({
                "ticker": t, "name": nm, "kind": "single_name",
                "sector": y.get("sector"), "industry": y.get("industry"),
                "sector_index_key": SECTOR_TO_INDEX.get(y.get("sector")),
                "sector_index_label": None,
                "ccy": y.get("currency"),
                "isin_yahoo_unverified": isin,
                "situs": "likely_non_us",
                "situs_basis": f"incorporation country per Yahoo: {ctry}",
                "estate_tax_exposed": None,
                "us_div_wht_rate": None,
                "replicable_share": None,
                "verdict": "may_already_be_outside_us_situs",
            })
            warnings.append({"ticker": t, "flag": f"country={ctry!r} -- US-listed but foreign-incorporated, likely NOT US-situs; verify incorporation before relying on this"})
            continue

        sec = y.get("sector")
        sidx = SECTOR_TO_INDEX.get(sec)
        names.append({
            "ticker": t, "name": nm, "kind": "single_name",
            "sector": sec, "industry": y.get("industry"),
            "sector_index_key": sidx,
            "sector_index_label": index_map["indices"][sidx]["label"] if sidx else None,
            "ccy": y.get("currency"),
            "isin_yahoo_unverified": isin,
            "isin_suspect": isin_suspect,
            "situs": "us", "situs_basis": "us_incorporated_per_country_field",
            "estate_tax_exposed": True,
            "us_div_wht_rate": 0.30,
            # Set by the match engine in Session 2 from an actual regression.
            # Never populated here -- a decomposition asserted without running
            # one is exactly the kind of confident wrong number this tool exists
            # to argue against.
            "replicable_share": None,
            "verdict": "no_ucits_equivalent_possible",
        })
        if not sidx:
            warnings.append({"ticker": t, "flag": f"sector {sec!r} has no validated UCITS sector line -- no proxy offered"})

    out = {
        "_meta": {
            "purpose": "BUILT map of US-situs holdings and the join key that finds their non-US-situs alternative. Do not hand-edit -- edit the lists in scripts/build_us_situs_map.py and rebuild.",
            "built": BUILD_DATE,
            "builder": "scripts/build_us_situs_map.py",
            "source": "Yahoo Finance quote info (name, type, sector, country, ISIN, expense ratio)",
            "single_name_rule": "UCITS diversification (5/10/40, relaxed to 20/35 for index trackers) structurally forbids a single-stock UCITS fund. Every single name therefore carries verdict=no_ucits_equivalent_possible. The tool must never present a sector line as an equivalent -- only as a partial proxy, with the unreplicable residual stated.",
            "adr_rule": "Names whose Yahoo country is not the United States are excluded, not guessed. ADR situs treatment is contested and this tool does not ship contested verdicts.",
            "withholding_rule": "Singapore has no US tax treaty, so a Singapore resident holding a US-domiciled fund suffers the full 30 per cent statutory rate on US dividends. An Irish UCITS fund holding the same US equities suffers 15 per cent at fund level under the US-Ireland treaty. That gap runs in the SAME direction as the estate-tax saving -- for US equity exposure the swap is not a trade-off.",
            "counts": {
                "etfs": len(etfs),
                "single_names": len(names),
                "dropped": sum(1 for w in warnings if "drop" in w),
                "names_without_sector_proxy": sum(1 for n in names if not n["sector_index_key"]),
                "etfs_situs_inferred": sum(1 for e in etfs if e["situs_basis"] != "isin"),
            },
        },
        "etfs": sorted(etfs, key=lambda r: r["ticker"]),
        "single_names": sorted(names, key=lambda r: r["ticker"]),
        "warnings": warnings,
    }

    path = os.path.join(DATA, "us_situs_map.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    m = out["_meta"]["counts"]
    print(f"\nETFs {m['etfs']}  single names {m['single_names']}  dropped {m['dropped']}")
    print(f"  situs inferred (no ISIN): {m['etfs_situs_inferred']}")
    print(f"  names with no sector proxy: {m['names_without_sector_proxy']}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
