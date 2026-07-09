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

def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))

# Starter Score weights (must be kept in sync with the tooltip copy in template.html).
SCORE_WEIGHTS = {"cost": 0.25, "tax": 0.20, "return": 0.25, "liquidity": 0.15, "breadth": 0.15}
_LIQ_SCORE = {"high": 100, "medium": 70, "low": 40, "thin": 15, "unknown": 55}
_BREADTH_BASE = {"dev_equity": 95, "sg_equity": 72, "em_asia_equity": 70, "reits": 65,
                 "dev_bonds": 82, "sgd_bonds": 80, "asia_bonds": 62, "gold": 60,
                 "cash": 60, "thematic_equity": 30}

def starter_score(rec):
    """Transparent 0-100 efficiency composite for a long-term-hold starter — NOT a buy
    recommendation. Returns (score, parts) or (None, None) when TER/return are unknown.
    Components each 0-100:
      cost   : lower total annual drag (TER + withholding) is better
      tax    : estate-tax safety (US-domiciled penalised hard)
      return : higher net expected return
      liquidity : how easily traded
      breadth: broad diversified beta beats single-country / thematic bets
    """
    net = rec.get("net_expected_return_pct")
    drag = rec.get("cost_drag_total_pct")
    if net is None or drag is None:
        return None, None
    cost = _clamp(100 * (1.0 - drag) / (1.0 - 0.15))           # 0.15%->100, 1.0%->0
    tax = 25 if rec.get("estate_tax_exposed") else (60 if rec["domicile"] == "verify" else 100)
    ret = _clamp(100 * (net - 2.5) / (8.0 - 2.5))              # 2.5%->0, 8%->100
    liq = _LIQ_SCORE.get(rec.get("liquidity_tier"), 55)
    ac = rec["asset_class"]
    seg = (rec.get("segment") or "").lower()
    breadth = _BREADTH_BASE.get(ac, 55)
    if ac == "em_asia_equity":
        breadth = 78 if "emerging market" in seg else 52       # broad EM vs single-country
    parts = {"cost": round(cost), "tax": round(tax), "return": round(ret),
             "liquidity": round(liq), "breadth": round(breadth)}
    total = sum(parts[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    return round(total), parts

# ---- docs build ----------------------------------------------------------
def build_docs(universe, cma, mp):
    """Inline the three data objects into template.html -> docs/index.html so
    the GitHub Pages build is self-contained (no runtime fetch needed)."""
    tpl_path = os.path.join(ROOT, "template.html")
    if not os.path.exists(tpl_path):
        print("  (no template.html yet — skipping docs build)")
        return
    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps({"universe": universe, "cma": cma, "mp": mp}, ensure_ascii=False)
    needle = "window.__DATA__=null;"
    if needle not in html:
        print("  WARNING: data-boot sentinel not found in template.html; docs not built")
        return
    html = html.replace(needle, "window.__DATA__=" + blob + ";")
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    with open(os.path.join(docs, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  docs/index.html built ({len(html):,} bytes, data inlined)")


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
        score, parts = starter_score(rec)
        rec["starter_score"] = score
        rec["score_parts"] = parts
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
            "score_method": {
                "label": "Starter Score",
                "range": "0-100",
                "caveat": "A transparent efficiency composite for a long-term buy-and-hold starter — NOT a buy recommendation. Blank where TER is not yet source-verified.",
                "weights": SCORE_WEIGHTS,
                "components": {
                    "cost": "Lower total annual drag (TER + dividend withholding) scores higher.",
                    "tax": "US estate-tax safety; US-domiciled funds penalised hard.",
                    "return": "Higher net expected long-run return scores higher.",
                    "liquidity": "How easily the fund can be traded.",
                    "breadth": "Broad diversified beta beats single-country or thematic bets."
                }
            },
        },
        "funds": funds,
        "warnings": warnings,
    }
    with open(os.path.join(DATA, "etf_universe.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    # ---- build docs/index.html (inline data for GitHub Pages) -----------
    build_docs(out, cma, mp)

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
    print("\nStarter Score (top 8 of scored funds):")
    scored = sorted([f for f in funds if f.get("starter_score") is not None],
                    key=lambda f: -f["starter_score"])
    for f in scored[:8]:
        p = f["score_parts"]
        print(f"  {f['starter_score']:3}  {f['ticker']:5} {f['segment'][:18]:18} "
              f"[cost {p['cost']} tax {p['tax']} ret {p['return']} liq {p['liquidity']} brd {p['breadth']}]")
    print(f"  ({len(scored)}/{len(funds)} funds scored; rest lack a verified TER)")
    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("  -", w)
    else:
        print("\nno warnings.")

if __name__ == "__main__":
    main()
