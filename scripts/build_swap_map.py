#!/usr/bin/env python
"""Match US-situs holdings to non-US-situs alternatives.

    python scripts/build_swap_map.py

Reads  data/{index_map,ucits_universe,us_situs_map}.json
Writes data/swap_map.json

MATCHING IS ON INDEX IDENTITY, NOT CORRELATION. Every US large-cap equity ETF
correlates above 0.98 with every other one, so correlation cannot tell an
S&P 500 tracker from a Nasdaq-100 tracker. Tracking error, added separately,
verifies a match; it never discovers one.

  tier 1  same index. A lookup, not a judgement.
  tier 2  same family, or a near-family pair. Always carries the caveat naming
          what changes, verbatim from the registry.
  tier 3  no close equivalent. A valid and frequently correct answer -- SCHD
          and the CRSP total-market series have no UCITS tracking the same
          index, and saying so is more useful than offering the nearest thing.

Only lines whose situs is RESOLVED as non-US may be offered as alternatives.
Gold ETCs are listed separately under unresolved_alternatives: they are the
only London route to bullion, but they are debt securities whose situs does
not follow from UCITS status, so they are never presented as a safe swap.

--- The withholding model, and why it is not symmetric ---

A US-domiciled fund pays its holder a distribution, and the US withholds 30
per cent of THE WHOLE DISTRIBUTION from a Singapore resident -- there is no
US-Singapore treaty to reduce it, and the rate applies to the fund's non-US
income too.

An Irish UCITS suffers 15 per cent at FUND level on its US-source dividends
only, under the US-Ireland treaty, and Ireland withholds nothing further on
payments to a non-resident.

So the comparison is 30 per cent of everything against 15 per cent of the US
slice. For a pure US equity fund that is a 15-point gap; for a global fund it
is wider still, because the US-domiciled wrapper taxes income that never came
from the United States. Modelling both sides at "rate times US content" -- as
cma.json does -- understates the US-domiciled case.

This feature also diverges from cma.json on Luxembourg. That table records LU
at 0.15; Luxembourg UCITS generally do NOT obtain the treaty rate, because
Limitation-on-Benefits clauses leave most paying the full 30 per cent. Rate
here comes from the ISIN prefix: IE 0.15, everything else 0.30.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BUILD_DATE = "2026-08-02"

US_HOLDER_RATE = 0.30  # Singapore resident, no treaty, on the whole distribution


def load(n):
    with open(os.path.join(DATA, n), encoding="utf-8") as fh:
        return json.load(fh)


# SGX-listed, NON-US-domiciled lines that track an index a US-domiciled holding
# in this map also tracks. Mapped explicitly by ticker rather than by matching
# benchmark strings, so a mis-mapping is visible in review.
#
# The list is short by nature. SGX offers no S&P 500, no Nasdaq-100, no US total
# market and no US sector funds that are not themselves US-domiciled -- the SGX
# US-equity lines (S27, D07, GSD/O87) are the PROBLEM this tool exists to flag,
# not the solution. Where SGX does help is gold, and it helps a lot: a
# Singapore-domiciled FUND holding allocated bullion is a cleaner situs answer
# than either a US grantor trust or an Irish ETC, because it is neither a US
# vehicle nor a debt security.
#
# The other direction matters too: SGX lines are SRS-eligible, which no
# London-listed UCITS is. For an investor funding from SRS that can outweigh a
# fee difference, so it is surfaced rather than left implicit.
SGX_ALTERNATIVES = {
    "GLS": {"index_key": "gold_spot",
            "why": "Singapore-domiciled fund holding allocated physical gold vaulted in Singapore. Not a US vehicle and not a debt security, so the situs question that clouds both the US trust and the Irish ETCs does not arise.",
            "srs": True},
    "H1N": {"index_key": "msci_em",
            "why": "Tracks the same MSCI Emerging Markets index, listed on SGX.",
            "srs": True},
}


def sgx_pool(index_map):
    """Non-US-situs SGX lines, shaped like the UCITS records so they can be
    ranked and rendered alongside them."""
    p = os.path.join(DATA, "etf_universe.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        funds = {f["ticker"]: f for f in json.load(fh)["funds"]}
    out = []
    for tk, cfg in SGX_ALTERNATIVES.items():
        f = funds.get(tk)
        if not f:
            continue
        dom = f.get("domicile")
        if dom == "US":          # never offer a US-domiciled line as the fix
            continue
        idx = index_map["indices"][cfg["index_key"]]
        out.append({
            "ticker": tk, "yahoo": None,
            "name": f["name"], "official_name": f["name"],
            "isin": f.get("isin"),
            "index_key": cfg["index_key"], "index_label": idx["label"],
            "index_family": idx["family"],
            "issuer_hint": f.get("fund_manager"), "fund_family": f.get("fund_manager"),
            "structure": "sgx_fund", "income": f.get("income") or "Accumulating",
            "exchange": "SGX", "ccy": f.get("ccy"), "ccy_is_pence": False,
            "ter": f.get("ter"), "ter_src": f.get("ter_basis"),
            "aum_usd": None, "yield": f.get("yield"),
            "is_ucits": False,
            # Situs here rests on fund domicile, not UCITS status: a
            # Singapore-domiciled fund is not US-situs.
            "situs": "non_us" if dom and dom != "US" else "unresolved",
            "estate_tax_exposed": False,
            "wht_domicile": dom, "wht_domicile_conf": "curated_universe",
            "us_div_wht_rate": 0.15 if dom == "IE" else 0.30,
            "venue_note": cfg["why"],
            "srs_eligible": cfg.get("srs", False),
        })
    return out


def near_family_map(index_map):
    """family -> [(other_family, caveat)] , symmetric."""
    out = {}
    for p in index_map["near_families"]["pairs"]:
        out.setdefault(p["a"], []).append((p["b"], p["caveat"]))
        out.setdefault(p["b"], []).append((p["a"], p["caveat"]))
    return out


def cost_view(us, alt, index_map):
    """Annual cost comparison for one swap, in percentage points of NAV.

    Gross dividend yield is taken EMPIRICALLY from the US holding's trailing
    distribution rather than assumed, then grossed back up for the 30 per cent
    already withheld. Where Yahoo gives no yield the withholding comparison is
    reported as unavailable rather than filled with an assumption.
    """
    idx = index_map["indices"][alt["index_key"]]
    us_content = idx["us_content"]

    gross_yield = us.get("yield")
    out = {
        "ter_us": us.get("ter"),
        "ter_alt": alt.get("ter"),
        "ter_delta_pp": None,
        "wht_us_pp": None,
        "wht_alt_pp": None,
        "wht_saving_pp": None,
        "net_annual_delta_pp": None,
        "us_content": us_content,
        "alt_wht_rate": alt["us_div_wht_rate"],
        "basis": None,
    }
    if us.get("ter") is not None and alt.get("ter") is not None:
        out["ter_delta_pp"] = round(alt["ter"] - us["ter"], 4)

    if gross_yield is None:
        out["basis"] = "no trailing yield for the US line -- withholding comparison unavailable"
        return out

    # Yahoo's yield for a US-domiciled ETF is the GROSS distribution rate. A US
    # fund is a RIC: it receives US dividends untaxed and distributes in full,
    # and the 30 per cent is taken at the holder, not inside the fund. So this
    # figure is already gross and must NOT be grossed up again.
    gross = gross_yield * 100.0

    # US-domiciled: 30 per cent of the WHOLE distribution, no treaty for a
    # Singapore resident, including any non-US income the fund passes through.
    wht_us = gross * US_HOLDER_RATE
    # Irish UCITS: 15 per cent at fund level on the US-source slice only, and
    # Ireland withholds nothing further on payments to a non-resident.
    wht_alt = gross * us_content * alt["us_div_wht_rate"]
    out["wht_us_pp"] = round(wht_us, 4)
    out["wht_alt_pp"] = round(wht_alt, 4)
    out["wht_saving_pp"] = round(wht_us - wht_alt, 4)
    if out["ter_delta_pp"] is not None:
        # positive = the alternative is cheaper all-in, per year
        out["net_annual_delta_pp"] = round(out["wht_saving_pp"] - out["ter_delta_pp"], 4)
    out["basis"] = (f"US line's trailing gross distribution {gross:.2f}%; "
                    f"US-domiciled taxed at {US_HOLDER_RATE:.0%} of the whole distribution, "
                    f"alternative at {alt['us_div_wht_rate']:.0%} of the "
                    f"{us_content:.0%} US-source slice")
    return out


def rank(cands, index_map, us):
    """Cheapest all-in first. Explicit and inspectable -- no opaque score."""
    def key(a):
        c = cost_view(us, a, index_map)
        # all-in annual cost of holding the alternative; None TER sorts last
        ter = a.get("ter")
        wht = c["wht_alt_pp"] if c["wht_alt_pp"] is not None else 0.0
        return (ter is None, (ter or 0.0) + wht, -(a.get("aum_usd") or 0))
    return sorted(cands, key=key)


def main():
    index_map = load("index_map.json")
    ucits = load("ucits_universe.json")
    us_map = load("us_situs_map.json")

    nf = near_family_map(index_map)
    sgx = sgx_pool(index_map)
    pool = ucits["funds"] + sgx
    safe = [f for f in pool if f["situs"] == "non_us"]
    unresolved = [f for f in pool if f["situs"] != "non_us"]
    print(f"alternatives pool: {len(ucits['funds'])} UCITS + {len(sgx)} SGX")

    by_index, by_family = {}, {}
    for f in safe:
        by_index.setdefault(f["index_key"], []).append(f)
        by_family.setdefault(f["index_family"], []).append(f)
    unresolved_by_index = {}
    for f in unresolved:
        unresolved_by_index.setdefault(f["index_key"], []).append(f)

    def brief(a, us, tier, caveat=None):
        c = cost_view(us, a, index_map)
        return {
            "ticker": a["ticker"], "yahoo": a["yahoo"], "name": a["name"],
            "official_name": a.get("official_name"),
            "isin": a.get("isin"), "domicile": a["wht_domicile"],
            "index_key": a["index_key"], "index_label": a["index_label"],
            "ccy": a["ccy"], "ccy_is_pence": a["ccy_is_pence"],
            "income": a["income"], "ter": a.get("ter"), "ter_src": a.get("ter_src"),
            # Travels with the line because it changes what the fund IS, not
            # merely what it costs: a GBP-hedged global bond fund gives a
            # Singapore holder sterling rate exposure the US original never had.
            "hedge_ccy": a.get("hedge_ccy"),
            "aum_usd": a.get("aum_usd"),
            "estate_tax_exposed": False,
            "venue": a.get("exchange", "LSE"),
            "venue_note": a.get("venue_note"),
            "srs_eligible": a.get("srs_eligible", False),
            "tier": tier, "caveat": caveat,
            "cost": c,
        }

    results, stats = [], {"tier1": 0, "tier2": 0, "tier3": 0}

    for us in us_map["etfs"]:
        k, fam = us["index_key"], us["index_family"]
        tier, caveat, cands = None, None, []

        if by_index.get(k):
            tier, cands = 1, list(by_index[k])
        else:
            same_fam = [f for f in by_family.get(fam, []) if f["index_key"] != k]
            if same_fam:
                tier, cands = 2, same_fam
                # Name the index you would be moving TO. Saying only "a
                # different index" is the whole substance of the demotion left
                # out: MSCI USA Momentum against MSCI World Momentum is a
                # change of universe, and the reader can see that at once from
                # the two names but not from the generic wording.
                alt_labels = sorted({f["index_label"] for f in same_fam})
                caveat = (f"No UCITS fund tracks {us['index_label']}. The alternatives below "
                          f"track {' or '.join(alt_labels)} instead — a related index in the "
                          f"same family, not the same index.")
            else:
                for other_fam, cav in nf.get(fam, []):
                    if by_family.get(other_fam):
                        tier, cands, caveat = 2, list(by_family[other_fam]), cav
                        break

        if not cands:
            tier = 3
        stats[f"tier{tier}"] += 1

        rec = {
            "ticker": us["ticker"], "name": us["name"], "kind": "etf",
            "index_key": k, "index_label": us["index_label"],
            "ter": us.get("ter"), "yield": us.get("yield"),
            # Fund size stands in for "how widely held". It is the only
            # defensible ordering available -- the tool has no holdings data --
            # and it is what the summary table ranks by.
            "aum_usd": us.get("aum_usd"),
            "estate_tax_exposed": True,
            "tier": tier,
            "caveat": caveat,
            "alternatives": [brief(a, us, tier, caveat)
                             for a in rank(cands, index_map, us)[:5]],
            "unresolved_alternatives": [
                {"ticker": u["ticker"], "name": u["name"], "structure": u["structure"],
                 "isin": u.get("isin"), "situs": u["situs"],
                 "why": ("Exchange-traded commodity, not a UCITS fund. It is a debt "
                         "security issued by a special-purpose vehicle, so its situs "
                         "does not follow from UCITS status and is not resolved here.")}
                for u in unresolved_by_index.get(k, [])
            ],
        }
        if tier == 3:
            rec["verdict"] = "no_close_equivalent"
            rec["verdict_note"] = (
                f"No validated non-US-situs fund tracks {us['index_label']} or a "
                f"comparable index. Offering a loosely similar fund here would change "
                f"the exposure without saying so, which this tool does not do.")
        results.append(rec)

    # ---- single names: there is no equivalent, and there cannot be ----------
    name_results = []
    for n in us_map["single_names"]:
        k = n.get("sector_index_key")
        proxies = rank(by_index.get(k, []), index_map, n) if k else []
        name_results.append({
            "ticker": n["ticker"], "name": n["name"], "kind": "single_name",
            "sector": n["sector"], "situs": n["situs"],
            "estate_tax_exposed": n["estate_tax_exposed"],
            "verdict": n["verdict"],
            "verdict_note": (
                "UCITS diversification rules (5/10/40, relaxed to 20/35 for index "
                "trackers) structurally forbid a single-stock UCITS fund. No such "
                "fund exists to find. A sector line below buys the market-and-sector "
                "part of this position; the company-specific part cannot be bought "
                "outside US situs at any price, and that residual is the real trade."
                if n["situs"] == "us" else
                "This company is incorporated outside the United States, so the "
                "holding may already sit outside US situs. Incorporation, not the "
                "listing venue, is the legal test -- verify before relying on it."),
            "sector_proxies": [
                {"ticker": p["ticker"], "name": p["name"], "index_label": p["index_label"],
                 "ter": p.get("ter"), "domicile": p["wht_domicile"], "ccy": p["ccy"],
                 "is_equivalent": False,
                 "note": "Partial sector proxy, NOT an equivalent."}
                for p in proxies[:3]
            ],
            # populated by the tracking-error pass, never asserted here
            "replicable_share": None,
        })

    out = {
        "_meta": {
            "purpose": "BUILT swap map. Matches US-situs holdings to non-US-situs alternatives on index identity.",
            "built": BUILD_DATE,
            "builder": "scripts/build_swap_map.py",
            "matching_basis": "Index identity. Correlation is NOT used -- it cannot distinguish indices whose returns correlate above 0.98, which is all of US large cap.",
            "withholding_model": (
                "A US-domiciled fund's distribution to a Singapore resident is taxed at "
                "30 per cent on the WHOLE distribution (no treaty). An Irish UCITS suffers "
                "15 per cent at fund level on its US-source dividends only. The comparison "
                "is therefore asymmetric and favours the UCITS route by more than the "
                "headline rate gap for any fund holding non-US assets."),
            "cma_divergence": (
                "Does not use cma.json tax.us_div_withholding.by_domicile (LU recorded at "
                "0.15; Luxembourg UCITS generally pay 30 per cent because of Limitation-on-"
                "Benefits clauses). Rate comes from the ISIN prefix."),
            "counts": {
                "us_etfs": len(results),
                "tier1_exact_index": stats["tier1"],
                "tier2_related_index": stats["tier2"],
                "tier3_no_equivalent": stats["tier3"],
                "single_names": len(name_results),
                "safe_universe": len(safe),
                "unresolved_universe": len(unresolved),
            },
        },
        "etfs": results,
        "single_names": name_results,
    }

    path = os.path.join(DATA, "swap_map.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    c = out["_meta"]["counts"]
    print(f"ETFs matched: tier1 {c['tier1_exact_index']}  tier2 {c['tier2_related_index']}  "
          f"tier3 {c['tier3_no_equivalent']}  (of {c['us_etfs']})")
    print(f"single names: {c['single_names']} (all no-equivalent by construction)")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
