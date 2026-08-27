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
import csv, json, re, sys, os, urllib.request, time, datetime
from collections import defaultdict

BUILD_DATE = "2026-07-09"          # session date; not computed
SOURCE_DOWNLOADED = "2026-07-09"   # date the SGX screener CSV was exported

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

def load_json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def load_json_opt(name):
    """Optional data file: a clone without it still builds, and the page says so."""
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
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
    recommendation. Returns (score, parts) or (None, None) when TER/return/vol are unknown.
    Components each 0-100:
      cost   : lower total annual drag (TER + withholding) is better
      tax    : estate-tax safety (US-domiciled penalised hard)
      return : RISK-ADJUSTED return (net expected return minus a volatility penalty, 0.10 x vol)
      liquidity : how easily traded
      breadth: broad diversified beta beats single-country / thematic bets
    """
    net = rec.get("net_expected_return_pct")
    drag = rec.get("cost_drag_total_pct")
    vol = rec.get("exp_vol")
    if net is None or drag is None or not vol:
        return None, None
    cost = _clamp(100 * (1.0 - drag) / (1.0 - 0.15))           # 0.15%->100, 1.0%->0
    tax = 25 if rec.get("estate_tax_exposed") else (60 if rec["domicile"] == "verify" else 100)
    util = net - 0.10 * vol                                   # risk penalty: -0.10 return-pts per 1% vol
    ret = _clamp(100 * (util - 2.5) / (5.5 - 2.5))            # penalised return 2.5%->0, 5.5%->100
    liq = _LIQ_SCORE.get(rec.get("liquidity_tier"), 55)
    ac = rec["asset_class"]
    seg = (rec.get("segment") or "").lower()
    breadth = _BREADTH_BASE.get(ac, 55)
    if ac == "em_asia_equity":
        if "emerging market" in seg:
            breadth = 78                                       # broad EM
        elif "asia ex" in seg or "asia pacific" in seg:
            breadth = 65                                       # broad regional
        else:
            breadth = 52                                       # single-country
    parts = {"cost": round(cost), "tax": round(tax), "return": round(ret),
             "liquidity": round(liq), "breadth": round(breadth)}
    total = sum(parts[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    return round(total), parts

# ---- crypto access route -------------------------------------------------
def build_crypto_records(crypto):
    """Crypto ETF access products, as FUND ROWS but deliberately OUTSIDE the
    scored universe.

    These records never pass through enrich(). That is the whole point: enrich()
    reads an asset class out of cma.json to attach a forward return, a
    volatility and an Efficiency Score, and there is no defensible long-run
    capital market assumption for bitcoin or ether. Routing crypto through it
    would have forced a number into cma.json to stop the lookup failing, and
    that invented number would then have propagated into the Build tab, the
    correlation matrix and every score on the site. Keeping the path separate
    makes the exclusion structural rather than a convention someone later
    "fixes" by adding the missing class.

    So every CMA-derived field is set to None explicitly rather than left
    absent: a missing key and a deliberate blank look identical in the UI, and
    only one of them survives a refactor.

    The third situs state is the other reason this cannot reuse the normal
    path. estate_tax_exposed is a boolean across the rest of the site, and its
    negation is read as "safe". A US-listed spot bitcoin trust is neither: the
    issuer's own 10-K says there is no guidance on whether the shares or the
    bitcoin behind them are US-situs. situs_unresolved carries that, and every
    place that claims "safe" has to require its absence.
    """
    if not crypto:
        return []
    routes = {r["key"]: r for r in crypto.get("routes", [])}
    out = []
    for p in crypto.get("products", []):
        route = routes.get(p["route"], {})
        unresolved = route.get("situs") == "unresolved"
        cost = p.get("cost")
        rec = {
            "ticker": p["ticker"],
            "name": p["name"],
            "ccy": p["ccy"],
            "exchange": p["exchange"],
            "isin": p.get("isin"),
            "domicile": p["domicile"],
            "domicile_conf": "curated",
            "asset_class": "crypto",
            "segment": p["underlying"],
            "benchmark": p.get("index"),
            "geo": route.get("label", ""),
            "fund_manager": p["issuer"],
            "income": "Accumulating",          # none of these distribute
            "mgmt_style": "PASSIVE",
            "cpf": "No",
            "ter": cost,
            "ter_conf": p.get("ter_conf"),
            "mgmt_fee": None,
            "yield": None,
            "val_m": None,
            "liquidity_tier": "unknown",
            "lot": p.get("lot"),
            "tr_1m": None, "tr_3m": None, "tr_1y": None, "ann_3y": None,
            "is_core": False,
            "share_classes": [{"ticker": p["ticker"], "ccy": p["ccy"], "val_m": None}],
            # --- crypto-specific -----------------------------------------
            "is_crypto": True,
            "crypto_route": p["route"],
            "route_label": route.get("label"),
            "wrapper": route.get("wrapper"),
            "structure": p.get("structure"),
            "custody": p.get("custody"),
            "usd_counter": p.get("usd_counter"),
            "rmb_counter": p.get("rmb_counter"),
            "cost_basis": p.get("cost_basis"),
            "src": p.get("src"),
            "stale_source_warning": p.get("stale_source_warning"),
            "yahoo": p.get("yahoo"),
            # --- situs ----------------------------------------------------
            # Never True here. "Exposed" is a positive claim this page cannot
            # make: the US products are unresolved, not confirmed exposed, and
            # the HK products are outside US situs. The UI must not print the
            # red "US estate tax + 30% withholding" note against either, since
            # none of them pays a dividend for withholding to bite on.
            "estate_tax_exposed": False,
            "situs_unresolved": unresolved,
            "situs_note": route.get("verdict"),
            # --- deliberately blank, not missing --------------------------
            "no_forward_return": True,
            "gross_expected_return_pct": None,
            "net_expected_return_pct": None,
            "cost_drag_total_pct": None,
            "est_wht_drag_pct": None,
            "us_div_wht_rate": None,
            "exp_vol": None,
            "return_basis": None,
            "effective_ter": cost,
            "ter_basis": None if cost is None else "published",
            "starter_score": None,
            "score_parts": None,
        }
        out.append(rec)
    return out


def check_crypto_exclusions(crypto_funds, cma, mp, warnings):
    """Guard the boundary this feature depends on.

    Cheap, and it fires at build time rather than in the browser. Every one of
    these has a plausible way of being broken later by someone doing something
    reasonable: adding a 'crypto' class to cma.json to make a chart work,
    dropping crypto into a model portfolio, or reusing enrich() for tidiness.
    """
    if "crypto" in cma.get("asset_classes", {}):
        warnings.append(
            "cma.json now carries a 'crypto' asset class. The crypto rows are "
            "meant to be unpriced; adding a CMA gives them a forward return and "
            "an Efficiency Score built on a number nobody can defend.")
    for k, prof in mp.get("profiles", {}).items():
        if "crypto" in prof.get("weights", {}):
            warnings.append(f"model_portfolios: profile '{k}' allocates to crypto — "
                            "the Build tab is not meant to reach these products.")
    for f in crypto_funds:
        if f.get("starter_score") is not None:
            warnings.append(f"{f['ticker']}: crypto row carries an Efficiency Score.")
        if f.get("net_expected_return_pct") is not None:
            warnings.append(f"{f['ticker']}: crypto row carries a forward return.")
        if f.get("estate_tax_exposed"):
            warnings.append(f"{f['ticker']}: crypto row claims estate-tax EXPOSED — "
                            "this page only claims unresolved or outside-US.")
        if f.get("ter") is not None and not f.get("cost_basis"):
            warnings.append(f"{f['ticker']}: cost figure with no stated basis — a "
                            "management fee and an ongoing charges figure are not "
                            "comparable and must not be shown as though they were.")


# ---- docs build ----------------------------------------------------------
def yahoo_symbol(f):
    """Yahoo ticker for a fund row.

    An explicit `yahoo` on the record always wins — the crypto rows carry one
    because their venues do not follow from any field already here (a US-listed
    trust takes a bare symbol, and IB1T is cross-listed on six European
    exchanges with no single obvious suffix). Before this existed the rule fell
    through to '.SI' for anything that was neither core nor HKEX, which would
    have quietly fetched a Singapore listing that does not exist and dropped the
    fund with a 'no data' line rather than an error.
    """
    if f.get("yahoo"):
        return f["yahoo"]
    if f.get("is_core"):
        return f["ticker"] + ".L"
    if f.get("exchange") == "HKEX":
        return f["ticker"].zfill(4) + ".HK"
    return f["ticker"] + ".SI"


def load_or_fetch_prices(funds):
    """Weekly ~6yr close history per fund from Yahoo, for the on-page price chart.
    Fetched only when --prices is passed or data/prices.json is missing (network + slow);
    otherwise the existing file is reused so normal builds stay fast and offline.
    Public market data, stored compactly (weekly closes) for an educational chart."""
    path = os.path.join(DATA, "prices.json")
    if "--prices" not in sys.argv and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    print("fetching weekly price history from Yahoo (slow; --prices) ...")
    out = {}
    last_ts = 0   # newest real bar timestamp seen (for an accurate, non-reconstructed "as of")
    for f in funds:
        sym = yahoo_symbol(f)
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + sym
               + "?range=6y&interval=1wk&events=div")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            d = json.load(urllib.request.urlopen(req, timeout=15))
            r = d["chart"]["result"][0]
            ts = r["timestamp"]
            cl = r["indicators"]["quote"][0]["close"]
            closes = [round(c, 3) if c is not None else None for c in cl]
            if sum(1 for c in closes if c is not None) < 20:
                continue
            entry = {"s": ts[0], "c": closes}
            # timestamp of the last bar that actually has a close, for the as-of date
            fund_last = 0
            for i in range(len(closes) - 1, -1, -1):
                if closes[i] is not None:
                    fund_last = ts[i]; last_ts = max(last_ts, ts[i]); break
            # trailing-12-month distributions (same currency as the closes) -> dv12,
            # used to derive a trailing yield where the SGX export / curated has none
            divs = (r.get("events") or {}).get("dividends") or {}
            if divs and fund_last:
                cutoff = fund_last - 365 * 86400   # epoch-seconds window, not calendar arithmetic
                dv12 = sum(v.get("amount", 0) for v in divs.values() if v.get("date", 0) >= cutoff)
                if dv12 > 0:
                    entry["dv12"] = round(dv12, 6)
            out[f["ticker"]] = entry
        except Exception as e:
            print(f"  {sym}: no data ({type(e).__name__})")
        time.sleep(0.15)
    if last_ts:
        # date of the most recent weekly close across the universe (UTC), stored explicitly so the
        # page never has to reconstruct dates from an assumed 604800s spacing (which drifts).
        out["asof"] = datetime.datetime.utcfromtimestamp(last_ts).date().isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    n_funds = sum(1 for k in out if k != "asof")
    print(f"  prices.json: {n_funds}/{len(funds)} funds, asof {out.get('asof','?')}, {os.path.getsize(path):,} bytes")
    return out


def apply_yahoo_yields(funds, prices):
    """Fill / refresh trailing dividend yields from Yahoo distribution events.

    yield = trailing-12m distributions (dv12) / last close, both in the fund's
    trading currency. Policy:
      - accumulating / non-paying funds have no events -> untouched (stay None);
      - no curated yield          -> use the computed figure (src 'yahoo_ttm');
      - curated agrees (<=1.5pp)  -> use the computed figure (fresher; refreshed
                                     weekly by the prices Action);
      - curated disagrees (>1.5pp)-> KEEP curated and flag for human review.
    """
    filled = replaced = 0
    for f in funds:
        p = prices.get(f["ticker"]) or {}
        dv = p.get("dv12")
        closes = p.get("c") or []
        last = next((c for c in reversed(closes) if c is not None), None)
        if not dv or not last:
            continue
        computed = round(100.0 * dv / last, 2)
        cur = f.get("yield")
        if cur is None:
            f["yield"] = computed
            f["yield_src"] = "yahoo_ttm"
            filled += 1
        elif abs(computed - cur) <= 1.5:
            f["yield"] = computed
            f["yield_src"] = "yahoo_ttm"
            replaced += 1
        else:
            print(f"  YIELD FLAG {f['ticker']:5} curated {cur}% vs computed {computed}% "
                  f"(>1.5pp apart) - keeping curated; review at next quarterly pass")
    print(f"  yields: {filled} filled + {replaced} refreshed from Yahoo trailing-12m distributions")


def _decomp_sentence(tpl, rep_share, idio_vol_pp, years):
    """Rebuild a single name's decomposition sentence from its own numbers.

    Kept beside slim_swap because the reconstruction and the check that it is
    lossless have to move together -- a template that silently drifts from the
    prose it replaces is the whole risk of doing this at all.
    """
    rep = int(round(rep_share * 100))
    return (tpl.replace("{rep}", str(rep))
               .replace("{idio}", str(100 - rep))
               .replace("{yrs}", str(years))
               .replace("{vol}", str(int(round(idio_vol_pp)))))


def slim_swap(d):
    """Project swap_map.json down to what the page actually renders.

    The repo file stays complete so the record is auditable. The shipped page
    does not need it: the same verdict_note is repeated on all 50 single names,
    the same ETC explanation on every gold row, and a prose `basis` string on
    every one of 112 pairs whose content the UI can reconstruct from the numbers
    beside it. Boilerplate moves into _meta once and the UI supplies it.

    Nothing that VARIES per record is dropped, and nothing is rounded here --
    this removes repetition, never evidence. Five further projections, each
    measured against the built file before being trusted:

      * verdict_detail  -- ONE template across all 50 names with three numbers
        varying. The template moves to _meta and the record keeps the numbers
        (replicable_share, idio_vol_pp; the 5-year window is constant at 260
        weeks). Reconstructed and compared string-for-string below: any name
        that does not rebuild byte-identically keeps its original prose.
      * sector_proxies  -- 16 distinct funds behind 100 references, and their
        attributes are identical at every reference. Becomes a _meta dictionary
        plus a ticker list. is_equivalent is False on all 100 and guarded by
        test_single_name_proxies_are_never_labelled_equivalent, so it is stated
        once rather than repeated.
      * alternative.caveat -- byte-identical to the parent ETF's caveat on all
        20 pairs that carry one, and the UI has always rendered the parent's.
        Pure duplication of a string that is still on screen.
      * alternative.estate_tax_exposed -- False on all 115, and enforced by
        test_no_alternative_is_itself_us_situs. Stated once in _meta.
      * unresolved_alternatives -- 4 distinct securities behind 8 references.
      * the alternatives themselves -- 115 records over 72 distinct securities,
        because the same Irish line is the answer for several US funds (VUAA
        appears six times). Twelve fields are identical at every appearance and
        move to a _meta.alts dictionary; `tier` and `verification_pending` DO
        vary by which fund is being swapped out of, so they stay on the record.
        Which fields are invariant is measured per build, not assumed.
      * kind, and single_name.verdict -- both reconstructable from where the
        record already sits: `kind` from which array it is in, and `verdict`
        from `situs`, exactly as test_single_names_never_claim_an_equivalent
        asserts. The UI puts both back at load.

    The repo file is not modified, so every guard in tests/ reads exactly the
    bytes it read before.
    """
    out = {"_meta": json.loads(json.dumps(d["_meta"])), "etfs": [], "single_names": []}
    m = out["_meta"]
    # The break detector's own description and its per-pair findings are audit
    # trail: the repo file keeps them in full, and the page never reads them --
    # each affected pair already states its own date and step size in the
    # reason the reader sees. Only the counts survive the projection.
    rvf = m.get("return_verification")
    if rvf:
        pb = rvf.get("price_breaks")
        if pb:
            rvf["price_breaks"] = {"steps": len(pb.get("steps_found") or []),
                                   "ticks": len(pb.get("ticks_dropped") or [])}
        # The method write-ups -- why tracking error is not the metric, the
        # Singapore-holder tax basis, the grade bands -- are the record of HOW
        # the numbers were made, and the guards read them from the repo file.
        # The page renders none of them; it reads window_years and nothing else.
        m["return_verification"] = {k: v for k, v in rvf.items()
                                    if k not in ("why_not_tracking_error", "tax_basis",
                                                 "grade_bands_abs_pp", "metric",
                                                 "why_this_window", "endpoint_rule")}
    # The sentence below hard-codes the window it describes, so every record
    # must agree on it. Asserted on the ROUNDED YEARS rather than the raw week
    # count: names regressed on a narrower industry index overlap it by 259
    # weeks where the broad sector gives 260, and both are "5 years" in the
    # prose. The first version compared raw weeks and stopped the build over a
    # difference the reader could never see.
    yrs_seen = {int(round(((n.get("decomposition") or {}).get("weeks") or 0) / 52))
                for n in d["single_names"]}
    assert len(yrs_seen) == 1, f"decomposition windows round to different years: {yrs_seen}"
    years = yrs_seen.pop()
    detail_tpl = (
        "About {rep} per cent of this position's return variance over the past "
        "{yrs} years came from the broad US market and its sector, which UCITS "
        "funds can buy. The other {idio} per cent was specific to the company "
        "and cannot be bought outside US situs at any price -- that residual, "
        "{vol} points of annualised volatility, is what a swap actually gives up.")
    m["boilerplate"] = {
        "single_name_verdict": next(
            (n["verdict_note"] for n in d["single_names"] if n["situs"] == "us"), ""),
        "foreign_incorporated_verdict": next(
            (n["verdict_note"] for n in d["single_names"] if n["situs"] != "us"), ""),
        "etc_why": next((u["why"] for e in d["etfs"]
                         for u in e["unresolved_alternatives"]), ""),
        "single_name_detail": detail_tpl,
        "decomp_years": years,
        # constants the UI re-applies per record; both are guard-enforced
        "proxy_is_equivalent": False,
        "alt_estate_tax_exposed": False,
    }
    m["proxies"] = {}
    m["unresolved"] = {}
    # Which alternative fields are the same everywhere that security appears.
    # Measured, because a field that starts invariant can stop being one and a
    # dictionary would then silently ship whichever copy it saw last.
    # aum_usd, isin and index_label are deliberately absent: all three are
    # shipped on every alternative today and rendered nowhere. The repo file
    # keeps them -- the ISIN in particular is what test_every_domicile_is_isin_
    # derived checks the domicile against -- but the page displays the domicile,
    # not the identifier behind it, and an alternative's index is now named in
    # the tier-2 caveat. The PARENT's aum_usd and index_label both stay: they
    # order the ten-swap table and print under "Tracks".
    hoist_candidates = ("name", "domicile", "ccy",
                        "ccy_is_pence", "income", "ter", "venue",
                        "venue_note", "srs_eligible", "hedge_ccy")
    seen_alt = {}
    varies = set()
    for e in d["etfs"]:
        for a in e["alternatives"]:
            prev = seen_alt.setdefault(a["ticker"], a)
            for f in hoist_candidates:
                if prev.get(f) != a.get(f):
                    varies.add(f)
    hoist = tuple(f for f in hoist_candidates if f not in varies)
    if varies:
        print(f"  swap: not hoisting {sorted(varies)} — differs between parents")
    m["alts"] = {t: {f: a[f] for f in hoist if f in a} for t, a in seen_alt.items()}
    # An alternative's own `tier` equals its parent's on all 115 pairs, so the
    # UI takes it from the parent. Asserted, because a pair that genuinely
    # matched at a different tier than its parent would be real evidence.
    mixed = [e["ticker"] for e in d["etfs"]
             if any(a.get("tier") != e["tier"] for a in e["alternatives"])]
    # `recommended` is not shipped: it is exactly the negation of "a reason was
    # given for setting this aside" on all 117 pairs, which is not a coincidence
    # -- the refusal floor writes one whenever it declines, and
    # test_every_alternative_carries_a_recommendation_decision requires it.
    # Checked here against the build rather than assumed, and carried if the
    # equivalence ever breaks, because a pair silently flipping to recommended
    # is the worst failure this file could cause.
    rec_derivable = all(a["recommended"] == (not a.get("not_recommended_because"))
                        for e in d["etfs"] for a in e["alternatives"])
    keep_alt = ("ticker", "not_recommended_because",
                "verification_pending") + (() if rec_derivable else ("recommended",)) + tuple(
                    f for f in hoist_candidates if f in varies)
    if mixed:
        keep_alt = keep_alt + ("tier",)
        print(f"  swap: {len(mixed)} parents have alternatives at a different tier — kept per pair")
    # The overlap window is the same 4.98 years on every graded pair, so it is
    # stated once. Only hoisted if it really is single-valued this build.
    # The five-year figure ships only where it DISAGREES with the graded window.
    # Where the two agree it adds a number per pair and tells the reader nothing
    # the grade has not; where they diverge it is the whole point, so drift_pp
    # gates its own context.
    keep_ver = ("grade", "gap_pp", "monthly_corr", "contradiction",
                "drift_pp", "long_history_unusable", "graded_since")
    # Most pairs share one window; the sector pairs graded since the March 2025
    # re-capping have a shorter one. The COMMON value is stated once and only
    # the exceptions carry their own. The first version hoisted only when EVERY
    # pair agreed, so a single short window put the field back on all 116.
    yrs_all = [a["verification"]["years"] for e in d["etfs"] for a in e["alternatives"]
               if (a.get("verification") or {}).get("years") is not None]
    common_years = max(set(yrs_all), key=yrs_all.count) if yrs_all else None
    if common_years is not None:
        m["boilerplate"]["ver_years"] = common_years
        if len(set(yrs_all)) > 1:
            print(f"  swap: {yrs_all.count(common_years)}/{len(yrs_all)} pairs on the "
                  f"{common_years}y window; the rest carry their own")
    # Only the total is rendered; the fee and withholding components are shipped
    # on all 116 pairs and displayed nowhere, and the footnote under the table
    # already says the total is the one plus the other. The repo file keeps the
    # split.
    keep_cost = ("net_annual_delta_pp",)
    # Tier-2 caveats repeat verbatim across holdings that share a reason: one
    # UCITS-capping explanation now sits on all ten sector ETFs, and the
    # total-market one on both VTI and ITOT. Interned by value; the UI reads
    # through an index the same way it does the proxy and unresolved tables.
    m["caveats"] = []
    cav_idx = {}

    def intern_caveat(text):
        if not text:
            return None
        if text not in cav_idx:
            cav_idx[text] = len(m["caveats"])
            m["caveats"].append(text)
        return cav_idx[text]

    # The no-verified-equivalent note is one sentence with the index label
    # dropped into it, repeated on every holding that reaches that verdict.
    # Templated and checked the same way the decomposition sentence is: rebuilt,
    # compared, and left verbatim on any record that does not reproduce.
    nv_tpl = ("Candidate funds tracking {label} were found, but none survived "
              "verification against realised returns. Presenting one anyway would be "
              "offering a match the evidence does not support.")
    m["boilerplate"]["no_verified_equivalent"] = nv_tpl
    nv_ok = nv_kept = 0
    for e in d["etfs"]:
        r = {k: e[k] for k in ("ticker", "name", "index_label", "tier",
                               "verdict", "verdict_note", "ter", "yield",
                               "aum_usd") if k in e}
        if r.get("verdict_note") and nv_tpl.replace("{label}", e["index_label"]) == r["verdict_note"]:
            del r["verdict_note"]
            nv_ok += 1
        elif r.get("verdict_note"):
            nv_kept += 1
        if e.get("caveat"):
            r["cav"] = intern_caveat(e["caveat"])
        if e.get("caveat_note"):
            r["cavn"] = intern_caveat(e["caveat_note"])
        r["alternatives"] = []
        for a in e["alternatives"]:
            # An alternative's caveat is dropped only where it repeats the
            # parent's word for word, which is every case in the current build.
            # If one ever diverges it is carried, because then it is evidence.
            if a.get("caveat") and a["caveat"] != e.get("caveat"):
                keep = keep_alt + ("caveat",)
            else:
                keep = keep_alt
            b = {k: a[k] for k in keep if k in a}
            v, c = a.get("verification", {}), a.get("cost", {})
            b["verification"] = {k: v[k] for k in keep_ver if v.get(k) is not None}
            if v.get("years") is not None and v["years"] != common_years:
                b["verification"]["years"] = v["years"]
            if v.get("drift_pp") is not None and v.get("gap_5y_pp") is not None:
                b["verification"]["gap_5y_pp"] = v["gap_5y_pp"]
            # The pre-change figure ships only where it says something the
            # current one does not. XLK against IUIT went +8.48 to -7.33; XLI
            # against SXLI went 0.15 to 0.26, and nobody needs to be told that.
            gb = v.get("gap_before_change_pp")
            # Same 1.5pp threshold verify_matches uses to flag a 3y/5y drift;
            # kept as a literal here rather than imported, since this module
            # does not otherwise depend on that one.
            if gb is not None and v.get("gap_pp") is not None and abs(gb - v["gap_pp"]) > 1.5:
                b["verification"]["gap_before_change_pp"] = gb
            b["cost"] = {k: c[k] for k in keep_cost if c.get(k) is not None}
            r["alternatives"].append(b)
        r["unresolved_alternatives"] = []
        for u in e.get("unresolved_alternatives", []):
            m["unresolved"].setdefault(
                u["ticker"], {"name": u["name"], "why": u.get("why")})
            r["unresolved_alternatives"].append(u["ticker"])
        out["etfs"].append(r)

    rebuilt = kept_prose = 0
    for n in d["single_names"]:
        r = {k: n[k] for k in ("ticker", "name", "sector", "situs",
                               "replicable_share") if k in n}
        dec = n.get("decomposition") or {}
        vol = dec.get("idio_ann_vol_pp")
        detail = n.get("verdict_detail")
        # Reconstruct, then compare against the prose actually built. The
        # template only replaces the sentence when it reproduces it exactly.
        if detail and vol is not None and n.get("replicable_share") is not None:
            if _decomp_sentence(detail_tpl, n["replicable_share"], vol, years) == detail:
                r["idio_vol_pp"] = vol
                rebuilt += 1
            else:
                r["verdict_detail"] = detail
                kept_prose += 1
        elif detail:
            r["verdict_detail"] = detail
            kept_prose += 1
        r["proxies"] = []
        for p in n.get("sector_proxies", []):
            prev = m["proxies"].get(p["ticker"])
            cur = {"name": p["name"], "index_label": p["index_label"],
                   "ter": p.get("ter")}
            # A dictionary is only lossless if every reference agrees. If two
            # ever disagree the projection would silently pick one, so stop.
            assert prev is None or prev == cur, (
                f"sector proxy {p['ticker']} differs between references")
            m["proxies"][p["ticker"]] = cur
            r["proxies"].append(p["ticker"])
        out["single_names"].append(r)
    print(f"  swap: {rebuilt} decomposition sentences templated"
          + (f", {kept_prose} kept verbatim (did not rebuild exactly)"
             if kept_prose else ", all rebuilt exactly"))
    return out


def build_docs(universe, cma, mp, prices):
    """Inline the data objects into template.html -> docs/index.html so
    the GitHub Pages build is self-contained (no runtime fetch needed)."""
    tpl_path = os.path.join(ROOT, "template.html")
    if not os.path.exists(tpl_path):
        print("  (no template.html yet — skipping docs build)")
        return
    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()
    # The swap map and mortality table are built by their own scripts on their
    # own cadence (the UCITS universe is near-static and deliberately not wired
    # into the weekly price Action). Both are optional so a clone without them
    # still builds -- the Swap tab then reports that the data is absent rather
    # than rendering an empty shell.
    payload = {"universe": universe, "cma": cma, "mp": mp, "prices": prices}
    for key, fname in (("swap", "swap_map.json"), ("mortality", "mortality_sg.json"),
                       ("crypto", "crypto_access.json")):
        p = os.path.join(DATA, fname)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                obj = json.load(fh)
            if key == "swap":
                obj = slim_swap(obj)
            payload[key] = obj
        else:
            print(f"  ! {fname} absent — Swap tab will report missing data")
    blob = json.dumps(payload, ensure_ascii=False)
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
    crypto = load_json_opt("crypto_access.json")
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
        # us_content is the US-source share of the fund's income, and it is the
        # right multiplier for a NON-US fund: an Irish UCITS suffers the treaty
        # rate on its US dividends only. It is the WRONG multiplier for a
        # US-domiciled fund. A US RIC distributing to a non-resident alien is
        # withheld at 30 per cent on the whole distribution, whatever the
        # underlying holdings are -- there is no look-through, and Singapore has
        # no treaty to reduce it.
        #
        # Corrected 2026-08-02. Previously S27 and D07, both 100 per cent US
        # index funds, were multiplied by dev_equity's 0.65 and showed a drag of
        # 0.351 against a true 0.54. The error ran AGAINST this dashboard's own
        # argument: it understated the cost of the US-domiciled route, making
        # the Irish-UCITS core look less advantageous than it is.
        eff_us_content = 1.0 if dom == "US" else (rec["us_content_override"] if rec.get("us_content_override") is not None else us_content.get(ac, 0))
        drag = round(yld_by_class.get(ac, 0) * eff_us_content * wr, 3)
        rec["est_wht_drag_pct"] = drag
        rec["gross_expected_return_pct"] = acinfo["ret"]
        rec["exp_vol"] = acinfo["vol"]          # asset-class-level expected volatility (% p.a.)
        rec["return_basis"] = acinfo["basis"]
        # TER used for cost + score. This is the curated value, which for a few new feeder funds
        # with no audited TER is itself a peer-informed estimate (ter_conf == "est"). The old
        # "mgmt fee + 0.10%" shortcut was dropped: feeder TERs run far above the fee (e.g. CXS
        # 0.50% fee -> 2.88% TER), so a small uplift badly understated their true cost.
        ter = rec.get("ter")
        rec["effective_ter"] = ter
        rec["ter_basis"] = None if ter is None else ("est" if rec.get("ter_conf") == "est" else "published")
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
            "mgmt_fee": ov.get("mgmt_fee"),
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
            "mgmt_style": "PASSIVE", "cpf": "No", "ter": c["ter"], "ter_conf": c["ter_conf"], "mgmt_fee": None,
            "yield": c["yield"], "val_m": None, "liquidity_tier": "high",
            "tr_1m": None, "tr_3m": None, "tr_1y": None, "ann_3y": None,
            "is_core": True, "share_classes": [{"ticker": c["ticker"], "ccy": c["ccy"], "val_m": None}],
        }
        funds.append(enrich(rec))

    # ---- add HK-listed satellites (HKEX; verified curated block) --------
    # HK-domiciled trackers: no US estate-tax exposure, no HK withholding on
    # distributions; ~10% China WHT already inside the funds (net indices).
    # Not SRS/CPFIS-eligible (SGX-only rule). us_content_override=0: their
    # income is China/HK-source, so the class-level US-content map must not apply.
    for c in curated.get("hk_listings", []):
        rec = {
            "ticker": c["ticker"], "name": c["name"], "ccy": c["ccy"], "exchange": "HKEX",
            "isin": c.get("isin"), "domicile": c["domicile"], "domicile_conf": "curated",
            "asset_class": c["asset_class"], "segment": c["segment"], "benchmark": c.get("benchmark"),
            "geo": c["segment"], "fund_manager": c["name"].split(" ")[0], "income": c["income"],
            "mgmt_style": "PASSIVE", "cpf": "No", "ter": c["ter"], "ter_conf": c.get("ter_conf"),
            "mgmt_fee": c.get("mgmt_fee"), "yield": c.get("yield"), "val_m": None,
            "liquidity_tier": c.get("liquidity_tier", "medium"), "lot": c.get("lot"),
            "tr_1m": None, "tr_3m": None, "tr_1y": None, "ann_3y": None,
            "us_content_override": 0,
            "is_core": False, "share_classes": [{"ticker": c["ticker"], "ccy": c["ccy"], "val_m": None}],
        }
        funds.append(enrich(rec))

    # second-pass merge: two primaries sharing a derived ISIN are the same fund that the
    # name-normaliser split (e.g. CFA "Asia REIT" vs COI "A_REIT", ISIN SG1DE9000003). Keep the
    # more liquid one as primary and absorb the other's share classes.
    by_isin, deduped = {}, []
    for f in funds:
        iso = f.get("isin")
        if iso and iso in by_isin:
            prim = by_isin[iso]
            if (f.get("val_m") or 0) > (prim.get("val_m") or 0):
                f["share_classes"] = f["share_classes"] + prim["share_classes"]
                deduped[deduped.index(prim)] = f
                by_isin[iso] = f
            else:
                prim["share_classes"] = prim["share_classes"] + f["share_classes"]
        else:
            deduped.append(f)
            if iso:
                by_isin[iso] = f
    funds = deduped

    # ---- crypto access rows (never enriched; see build_crypto_records) ----
    # Appended AFTER the ISIN de-duplication pass on purpose. That pass merges
    # two rows sharing an ISIN into one fund with several share classes, which
    # is right for a currency share class and wrong here: the HK crypto ETFs
    # publish ONE ISIN across their HKD, USD and RMB counters, so feeding them
    # through it would silently collapse distinct products.
    crypto_funds = build_crypto_records(crypto)
    if crypto_funds:
        check_crypto_exclusions(crypto_funds, cma, mp, warnings)
        clash = {f["ticker"] for f in funds} & {f["ticker"] for f in crypto_funds}
        if clash:
            # A real near-miss while building this: a mis-read filing reported
            # HK counters "3066/3067/3068", and 3067 is already the iShares Hang
            # Seng TECH ETF in curated.json. Two funds under one ticker would
            # have shown one fund's cost against the other's name.
            raise SystemExit(f"ticker collision between crypto rows and the "
                             f"existing universe: {sorted(clash)}")
        funds.extend(crypto_funds)
    elif crypto is None:
        print("  ! crypto_access.json absent — crypto route will report missing data")

    funds.sort(key=lambda r: (r["asset_class"], -(r["val_m"] or 0)))

    out = {
        "_meta": {
            "built": BUILD_DATE,
            "source_downloaded": SOURCE_DOWNLOADED,
            "n_funds": len(funds),
            "n_from_sgx": len(groups),
            "n_ucits_core": len(curated["ucits_core"]),
            "n_crypto_access": len(crypto_funds),
            "crypto_unpriced": True,   # crypto rows carry no CMA, by design
            "not_advice": True,
            "note": "Educational. Forward returns are synthesised house estimates, not forecasts. Domicile drives the tax verdict; where domicile_conf != 'isin'/'curated' treat with a verify flag.",
            "score_method": {
                "label": "Efficiency Score",
                "range": "0-100",
                "caveat": "A transparent efficiency composite for a long-term buy-and-hold starter — NOT a buy recommendation. Blank where TER is not yet source-verified.",
                "weights": SCORE_WEIGHTS,
                "components": {
                    "cost": "Lower total annual drag (TER + dividend withholding) scores higher.",
                    "tax": "US estate-tax safety; US-domiciled funds penalised hard.",
                    "return": "Risk-adjusted: net expected return minus a penalty for volatility (return - 0.10 x vol).",
                    "liquidity": "How easily the fund can be traded.",
                    "breadth": "Diversification: broad beta beats single-country or thematic bets."
                }
            },
        },
        "funds": funds,
        "warnings": warnings,
    }
    # ---- prices (weekly closes + trailing-12m distributions) ------------
    # Loaded before the universe is written so Yahoo-derived trailing yields
    # can fill the gaps the SGX export leaves empty (curated figures audited
    # against them; large disagreements keep curated and are flagged).
    prices = load_or_fetch_prices(funds)
    apply_yahoo_yields(funds, prices)

    with open(os.path.join(DATA, "etf_universe.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    # ---- build docs/index.html (inline data for GitHub Pages) -----------
    build_docs(out, cma, mp, prices)

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
    if crypto_funds:
        print("\ncrypto access rows (unpriced by design — no CMA, no score):")
        for f in crypto_funds:
            cost = "—" if f["ter"] is None else f"{f['ter']}% ({f['cost_basis']})"
            print(f"  {f['ticker']:5} {f['name'][:34]:34} {f['route_label']:16} "
                  f"situs={'unresolved' if f['situs_unresolved'] else 'outside US':11} {cost}")
        n_unres = sum(1 for f in crypto_funds if f["situs_unresolved"])
        print(f"  {n_unres}/{len(crypto_funds)} situs-unresolved; "
              f"{sum(1 for f in crypto_funds if f['ter'] is None)} with no verified cost figure")
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
