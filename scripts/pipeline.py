#!/usr/bin/env python3
"""
ETF Starter - Singapore : data pipeline (Session 1)

Reads the raw SGX ETF Screener export + curated overlay + CMA, and produces
data/etf_universe.json: a de-duplicated, domicile-aware, forward-return-enriched
ETF universe for the dashboard.

Pillars encoded here:
  - Efficiency:      TER + liquidity tier + total annual cost drag (TER + withholding).
  - Domicile/tax:    ISIN-derived domicile -> US-estate-tax exposure + dividend-withholding rate.
  - Forward return:  asset-class CMA gross return, netted of TER and withholding drag.

Build:  python scripts/pipeline.py     (run from project root)

No date arithmetic is performed; BUILD_DATE is stamped as a constant (session date,
ISO yyyy-mm-dd) to keep the build deterministic. If you re-download the SGX export,
update SOURCE_DOWNLOADED below.
"""
import csv, json, re, sys, os
from collections import defaultdict

BUILD_DATE = "2026-07-09"          # session date; not computed
SOURCE_DOWNLOADED = "2026-07-09"   # date the SGX screener CSV was exported

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

def load_json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)

# ---- domicile from ISIN --------------------------------------------------
ISIN_RE = re.compile(r"_en_([A-Z]{2}[A-Za-z0-9]{9}[0-9])_YES_")
DOMICILE_FROM_PREFIX = {"SG": "SG", "LU": "LU", "IE": "IE", "US": "US", "FR": "FR", "HK": "HK"}
SG_MANAGER_HINTS = ("Lion Global", "Amova", "CSOP", "UOB Asset", "Phillip", "CGS", "Nikko")

def derive_isin(docs):
    m = ISIN_RE.search(docs or "")
    return m.group(1) if m else ""

def resolve_domicile(isin, override, manager):
    """Returns (domicile, confidence). US always carries an ISIN, so a US-situs
    fund can never be silently mislabelled 'safe'."""
    if override:
        return override, "curated"
    if isin:
        return DOMICILE_FROM_PREFIX.get(isin[:2], isin[:2]), "isin"
    if any(h in (manager or "") for h in SG_MANAGER_HINTS):
        return "SG", "inferred"   # SG managers list SG-domiciled funds; SG is estate-tax-safe
    return "verify", "none"

# ---- asset-class inference (when not curated) ----------------------------
THEMATIC_KW = ("TECH", "EV ", "MOBILITY", "CLIMATE", "LOW CARBON", "CHINEXT", "STAR", "SEMICON")
def infer_class(row):
    ac = (row["Asset Class"] or "").strip().upper()
    geo = (row["Geographical Focus"] or "").strip()
    name = (row["Trading Name"] or "").upper()
    if ac in ("COMMODITIES",):
        return "gold" if "GOLD" in name or geo == "Gold" else "gold"
    if ac == "REITS":
        return "reits"
    if ac == "FIXED INCOME":
        if geo == "Singapore":
            return "sgd_bonds"
        return "asia_bonds" if geo in ("Asia", "China", "Asia Pacific") else "dev_bonds"
    # equities
    if any(k in name for k in THEMATIC_KW):
        return "thematic_equity"
    if geo == "Singapore":
        return "sg_equity"
    if geo == "USA":
        return "dev_equity"
    if geo == "Japan":
        return "dev_equity"
    return "em_asia_equity"   # China / India / Vietnam / Indonesia / EM / Asia / SE-Asia

# ---- share-class collapse ------------------------------------------------
CCY_TOKENS = ["us$d", "s$d", "us$a", "s$a", "sg$", "us$", "s$"]
def norm_name(name):
    s = (name or "").lower().strip()
    for tok in CCY_TOKENS:
        s = s.replace(tok, " ")
    s = re.sub(r"\b(usd|sgd|cny|cnh)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+[a-z]$", "", s).strip()   # drop trailing lone share-class letter
    return s

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def liquidity_tier(val_m):
    if val_m is None:
        return "unknown"
    if val_m >= 2:   return "high"
    if val_m >= 0.5: return "medium"
    if val_m >= 0.1: return "low"
    return "thin"

# ---- main ----------------------------------------------------------------
def main():
    cma = load_json("cma.json")
    curated = load_json("curated.json")
    mp = load_json("model_portfolios.json")
    warnings = []

    # validate model-portfolio weights sum to 100
    for k, prof in mp["profiles"].items():
        tot = sum(prof["weights"].values())
        if tot != 100:
            warnings.append(f"model_portfolios: profile '{k}' weights sum to {tot}, expected 100")

    # validate correlation pairs reference known classes + build symmetric matrix
    classes = list(cma["asset_classes"].keys())
    corr = {a: {b: (1.0 if a == b else None) for b in classes} for a in classes}
    for a, b, r in cma["corr_pairs"]:
        if a not in classes or b not in classes:
            warnings.append(f"corr_pairs: unknown class in pair ({a},{b})")
            continue
        corr[a][b] = corr[b][a] = r
    defaulted = []
    for a in classes:
        for b in classes:
            if corr[a][b] is None:
                corr[a][b] = 0.30
                if a < b:
                    defaulted.append(f"{a}|{b}")
    if defaulted:
        warnings.append(f"corr defaults (0.30) applied to {len(defaulted)} pairs: {', '.join(defaulted)}")

    wht = cma["tax"]["us_div_withholding"]
    yld_by_class = wht["assumed_div_yield_by_class"]
    us_content = wht["us_content_by_class"]
    wht_by_dom = wht["by_domicile"]

    def enrich(rec):
        """rec has: ticker,name,ccy,exchange,domicile,domicile_conf,asset_class,
        segment,ter,ter_conf,yield,... -> adds tax + return fields."""
        ac = rec["asset_class"]
        acinfo = cma["asset_classes"].get(ac)
        if not acinfo:
            warnings.append(f"{rec['ticker']}: unknown asset_class '{ac}'")
            return rec
        dom = rec["domicile"]
        rec["estate_tax_exposed"] = (dom == "US")
        wr = wht_by_dom.get(dom, 0.30)
        rec["us_div_wht_rate"] = wr
        drag = round(yld_by_class.get(ac, 0) * us_content.get(ac, 0) * wr, 3)
        rec["est_wht_drag_pct"] = drag
        rec["gross_expected_return_pct"] = acinfo["ret"]
        rec["return_basis"] = acinfo["basis"]
        ter = rec.get("ter")
        if ter is None:
            rec["net_expected_return_pct"] = None
            rec["cost_drag_total_pct"] = None
        else:
            rec["cost_drag_total_pct"] = round(ter + drag, 3)
            rec["net_expected_return_pct"] = round(acinfo["ret"] - ter - drag, 2)
        return rec

    # ---- ingest SGX rows, grouped by normalised name --------------------
    groups = defaultdict(list)
    with open(os.path.join(DATA, "sgx_etf_screener.csv"), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not (row.get("Trading Code") or "").strip():
                continue
            groups[norm_name(row["Trading Name"])].append(row)

    funds = []
    for key, members in groups.items():
        # primary = most liquid listing (highest Val $M); tie-break prefer SGD
        members.sort(key=lambda r: (num(r["Val ($M)"]) or 0, r["CCY"] == "SGD"), reverse=True)
        p = members[0]
        code = p["Trading Code"].strip()
        ov = curated["sgx_overrides"].get(code, {})
        isin = derive_isin(p["Documents"])
        dom, dom_conf = resolve_domicile(isin, ov.get("domicile_override"), p["Fund Manager"])
        rec = {
            "ticker": code,
            "name": p["Trading Name"].strip(),
            "ccy": p["CCY"],
            "exchange": "SGX",
            "isin": isin or None,
            "domicile": dom,
            "domicile_conf": dom_conf,
            "asset_class": ov.get("asset_class") or infer_class(p),
            "segment": ov.get("segment") or p["Geographical Focus"],
            "benchmark": p["Underlying Benchmark"],
            "geo": p["Geographical Focus"],
            "fund_manager": p["Fund Manager"],
            "income": p["Income Treatment"],
            "mgmt_style": p["Management Style"],
            "cpf": p["CPF Eligibility"],
            "ter": ov.get("ter"),
            "ter_conf": ov.get("ter_conf"),
            "yield": ov.get("yield"),
            "val_m": round(num(p["Val ($M)"]), 4) if num(p["Val ($M)"]) is not None else None,
            "liquidity_tier": liquidity_tier(num(p["Val ($M)"])),
            "tr_1m": num(p["TR 1M (%)"]), "tr_3m": num(p["TR 3M (%)"]),
            "tr_1y": num(p["TR 1Y (%)"]), "ann_3y": num(p["Ann. TR 3Y (%)"]),
            "is_core": False,
            "share_classes": [{"ticker": m["Trading Code"].strip(), "ccy": m["CCY"],
                                "val_m": round(num(m["Val ($M)"]), 4) if num(m["Val ($M)"]) is not None else None}
                               for m in members],
        }
        funds.append(enrich(rec))

    # ---- add UCITS core (not on SGX) ------------------------------------
    for c in curated["ucits_core"]:
        rec = {
            "ticker": c["ticker"], "name": c["name"], "ccy": c["ccy"], "exchange": c["exchange"],
            "isin": None, "domicile": c["domicile"], "domicile_conf": "curated",
            "asset_class": c["asset_class"], "segment": c["segment"], "benchmark": c.get("benchmark"),
            "geo": c["segment"], "fund_manager": c["name"].split(" ")[0], "income": c["income"],
            "mgmt_style": "PASSIVE", "cpf": "No", "ter": c["ter"], "ter_conf": c["ter_conf"],
            "yield": c["yield"], "val_m": None, "liquidity_tier": "high",
            "tr_1m": None, "tr_3m": None, "tr_1y": None, "ann_3y": None,
            "is_core": True, "share_classes": [{"ticker": c["ticker"], "ccy": c["ccy"], "val_m": None}],
        }
        funds.append(enrich(rec))

    funds.sort(key=lambda r: (r["asset_class"], -(r["val_m"] or 0)))

    out = {
        "_meta": {
            "built": BUILD_DATE,
            "source_downloaded": SOURCE_DOWNLOADED,
            "n_funds": len(funds),
            "n_from_sgx": len(groups),
            "n_ucits_core": len(curated["ucits_core"]),
            "not_advice": True,
            "note": "Educational. Forward returns are synthesised house estimates, not forecasts. Domicile drives the tax verdict; where domicile_conf != 'isin'/'curated' treat with a verify flag.",
        },
        "funds": funds,
        "warnings": warnings,
    }
    with open(os.path.join(DATA, "etf_universe.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    # ---- verification summary (stdout) ----------------------------------
    from collections import Counter
    print(f"BUILD {BUILD_DATE}  ->  data/etf_universe.json")
    print(f"funds: {len(funds)}  (from {len(groups)} SGX groups + {len(curated['ucits_core'])} UCITS core)")
    print("\ndomicile x confidence:")
    dc = Counter((f["domicile"], f["domicile_conf"]) for f in funds)
    for (d, c), n in sorted(dc.items()):
        print(f"  {d:7} {c:9} {n}")
    print("\nESTATE-TAX EXPOSED (US-domiciled):")
    for f in funds:
        if f["estate_tax_exposed"]:
            print(f"  {f['ticker']:5} {f['name'][:34]:34} isin={f['isin']}")
    print("\nasset-class distribution:")
    for ac, n in Counter(f["asset_class"] for f in funds).most_common():
        print(f"  {ac:16} {n}")
    ter_missing = [f["ticker"] for f in funds if f["ter"] is None]
    print(f"\nTER coverage: {len(funds)-len(ter_missing)}/{len(funds)}  missing: {len(ter_missing)}")
    print("\nshare-class merges (>1 listing):")
    for f in funds:
        if len(f["share_classes"]) > 1:
            scs = ",".join(f"{s['ticker']}/{s['ccy']}" for s in f["share_classes"])
            print(f"  {f['ticker']:5} {f['name'][:30]:30} <- {scs}")
    print("\nnet expected return sample (core + key SGX):")
    for f in funds:
        if f["is_core"] or f["ticker"] in ("S27", "ES3", "CLR", "A35"):
            print(f"  {f['ticker']:5} {f['segment'][:20]:20} dom={f['domicile']:3} "
                  f"gross={f['gross_expected_return_pct']}% ter={f['ter']} "
                  f"whtdrag={f['est_wht_drag_pct']}% net={f['net_expected_return_pct']}% "
                  f"estate={'YES' if f['estate_tax_exposed'] else 'no'}")
    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("  -", w)
    else:
        print("\nno warnings.")

if __name__ == "__main__":
    main()
