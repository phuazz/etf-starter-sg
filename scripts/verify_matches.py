#!/usr/bin/env python
"""Verify each proposed swap against realised returns.

    python scripts/verify_matches.py [--years 5] [--refresh]

Annotates data/swap_map.json in place. This pass VERIFIES matches; it does not
discover them -- correlation cannot distinguish indices that co-move above 0.98,
which is all of US large cap.

--- Why this does NOT use tracking error ---

The obvious statistic, the annualised volatility of weekly return differences,
is unusable for this particular comparison and measurement proved it:

    SPY vs IVV    (US   / US )   corr 0.9998   TE 0.31pp
    VUAA vs SPXS  (LSE  / LSE)   corr 0.9978   TE 0.99pp
    SPY vs VUAA   (US   / LSE)   corr 0.9285   TE 6.03pp

Same-venue pairs track tightly; cross-venue pairs blow out. Nothing about the
funds changed -- London closes at 16:30 local while New York closes at 21:00
London time, so two bars stamped with the same week cover offset windows. The
difference is a measurement artefact, and it does not wash out: at quarterly
frequency SPY vs VUAA still shows 3.0pp. Grading on it would fail every
cross-venue match, which is every match this tool makes.

What survives is the CUMULATIVE ANNUALISED RETURN GAP over the overlap window.
Timing noise inside a period cancels over the window; only the real divergence
in compounded outcome remains.

--- Why the raw gap still is not apples to apples ---

Yahoo's adjusted series reinvests dividends GROSS. For a US-domiciled fund that
silently assumes a holder who suffers no withholding -- true for an American,
false for the Singapore investor this tool is built for, who loses 30 per cent
of every distribution with no treaty to reduce it. An Irish UCITS, by contrast,
has already borne its 15 per cent inside NAV, so its series is genuinely net.

Comparing the two raw therefore flatters the US line by roughly 30 per cent of
its dividend yield. Every gap below is put on a Singapore-holder basis first by
deducting that from the US side. Without this the tool would report the UCITS
"lagging" on precisely the holdings where it wins after tax.
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, ".swap_prices.pkl")

MIN_MONTHS = 36          # three years of overlap before a gap is reported
US_HOLDER_RATE = 0.30

# Absolute annualised gap, after putting both sides on a Singapore-holder basis.
GRADE_A, GRADE_B, GRADE_C = 0.50, 1.50, 3.00


def fetch(symbols, years, refresh):
    import yfinance as yf
    px = pd.DataFrame()
    if os.path.exists(CACHE) and not refresh:
        px = pd.read_pickle(CACHE)
    missing = [s for s in symbols if s not in px.columns]
    if missing:
        print(f"  fetching {len(missing)} series ...")
        # one at a time for the stragglers: a batch download silently drops
        # symbols (CSPX.L among them on the first run) and a silently missing
        # series becomes a silently ungraded pair
        got, failed = {}, []
        for i, s in enumerate(missing, 1):
            for attempt in range(3):
                try:
                    h = yf.Ticker(s).history(period=f"{years}y", interval="1wk",
                                             auto_adjust=True)
                    if len(h):
                        # yfinance stamps a weekly bar in the EXCHANGE's local
                        # timezone. In UTC a London bar lands at 23:00 the
                        # previous day and a New York bar at 04:00 -- the same
                        # trading week, timestamps a day apart, so an inner join
                        # returns ZERO overlapping rows and every pair silently
                        # grades "insufficient". Rounding to the nearest day
                        # collapses both onto the true week start.
                        idx = pd.to_datetime(h.index, utc=True).tz_localize(None).round("D")
                        got[s] = pd.Series(h["Close"].to_numpy(), index=idx)
                        break
                except Exception:
                    pass
            else:
                failed.append(s)
            if i % 25 == 0:
                print(f"    {i}/{len(missing)}")
        if failed:
            print(f"  ! no history after 3 attempts: {failed}")
        if got:
            add = pd.DataFrame(got)
            add = add[~add.index.duplicated(keep="last")].sort_index()
            px = add if px.empty else px.join(add, how="outer")
        px.to_pickle(CACHE)
    return px


def to_usd(s, ccy, fx):
    if ccy == "USD":
        return s
    if ccy in ("GBP", "GBp"):
        v = s / 100.0 if ccy == "GBp" else s
        return v * fx["GBPUSD=X"].reindex(v.index).ffill()
    if ccy == "EUR":
        return s * fx["EURUSD=X"].reindex(s.index).ffill()
    return None


def ann(series):
    yrs = (series.index[-1] - series.index[0]).days / 365.25
    if yrs <= 0:
        return None, 0.0
    return (series.iloc[-1] / series.iloc[0]) ** (1 / yrs) - 1, yrs


def compare(s_us, s_alt, us_gross_yield):
    j = pd.concat([s_us.rename("us"), s_alt.rename("alt")], axis=1,
                  join="inner").dropna()
    months = len(j.resample("ME").last().dropna())
    if months < MIN_MONTHS:
        return {"grade": "insufficient", "months": months, "gap_pp": None,
                "basis": f"only {months} overlapping months, need {MIN_MONTHS}"}

    a_us, yrs = ann(j["us"])
    a_alt, _ = ann(j["alt"])
    if a_us is None:
        return {"grade": "insufficient", "months": months, "gap_pp": None,
                "basis": "degenerate window"}

    # Put the US line on a Singapore-holder basis: Yahoo reinvests its
    # distributions gross, but a Singapore resident never receives them gross.
    drag = (us_gross_yield or 0.0) * US_HOLDER_RATE
    a_us_net = a_us - drag

    gap = (a_alt - a_us_net) * 100
    m = j.resample("ME").last().pct_change().dropna()
    corr = float(np.corrcoef(m["us"], m["alt"])[0, 1]) if len(m) > 2 else None

    g = abs(gap)
    grade = ("A" if g <= GRADE_A else "B" if g <= GRADE_B
             else "C" if g <= GRADE_C else "fail")
    return {
        "grade": grade,
        "months": months,
        "years": round(yrs, 2),
        "us_ann_gross_pp": round(a_us * 100, 3),
        "us_holder_wht_drag_pp": round(drag * 100, 3),
        "us_ann_after_wht_pp": round(a_us_net * 100, 3),
        "alt_ann_pp": round(a_alt * 100, 3),
        "gap_pp": round(gap, 3),
        "monthly_corr": None if corr is None else round(corr, 4),
        "basis": (f"annualised total return over {yrs:.1f}y of overlap, USD; US line "
                  f"reduced by {drag * 100:.2f}pp for the 30% a Singapore holder "
                  f"suffers on distributions. Positive gap = alternative ahead."),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    p = os.path.join(DATA, "swap_map.json")
    swap = json.load(open(p, encoding="utf-8"))
    ucits = {f["ticker"]: f for f in json.load(
        open(os.path.join(DATA, "ucits_universe.json"), encoding="utf-8"))["funds"]}
    us_meta = {e["ticker"]: e for e in json.load(
        open(os.path.join(DATA, "us_situs_map.json"), encoding="utf-8"))["etfs"]}

    us_t = sorted({e["ticker"] for e in swap["etfs"]})
    alt_t = sorted({a["ticker"] for e in swap["etfs"] for a in e["alternatives"]})
    px = fetch(us_t + [f"{t}.L" for t in alt_t] + ["GBPUSD=X", "EURUSD=X"],
               args.years, args.refresh)
    print(f"  cache holds {px.shape[1]} series")

    fx = px[[c for c in ("GBPUSD=X", "EURUSD=X") if c in px]]
    usd = {}
    for t in us_t:
        if t in px:
            usd[t] = px[t].dropna()
    for t in alt_t:
        c = f"{t}.L"
        if c in px:
            s = to_usd(px[c].dropna(), ucits[t]["ccy"], fx)
            if s is not None and len(s.dropna()):
                usd[t] = s.dropna()

    graded, dist, flagged = 0, {}, []
    for e in swap["etfs"]:
        s_us = usd.get(e["ticker"])
        gy = (us_meta.get(e["ticker"], {}) or {}).get("yield")
        for a in e["alternatives"]:
            s_alt = usd.get(a["ticker"])
            if s_us is None or s_alt is None:
                a["verification"] = {"grade": "no_data",
                                     "basis": "price history unavailable for one side"}
                dist["no_data"] = dist.get("no_data", 0) + 1
                continue
            v = compare(s_us, s_alt, gy)
            # Threshold is the C floor, not the B floor. Sector indices from
            # different providers legitimately differ by one to two points a
            # year -- S&P 500 Information Technology is not MSCI US IMI
            # Information Technology. Flagging those as broken would demote
            # genuinely useful swaps. Only a divergence past the C floor
            # indicates the mapping itself is wrong.
            if a["tier"] == 1 and v.get("gap_pp") is not None and abs(v["gap_pp"]) > GRADE_C:
                v["contradiction"] = (
                    f"Claimed as the same index yet the compounded outcomes differ by "
                    f"{v['gap_pp']:+.2f}pp a year. Either the index mapping is wrong or "
                    f"the funds replicate differently. Treat as unverified.")
                flagged.append((e["ticker"], a["ticker"], v["gap_pp"]))
            a["verification"] = v
            dist[v["grade"]] = dist.get(v["grade"], 0) + 1
            graded += 1

        # --- refusal floor -------------------------------------------------
        # An alternative is only offered if the realised record corroborates
        # it. Everything else stays in the payload, marked and reasoned, so a
        # reader can see what was considered and why it was set aside -- but it
        # is never presented as a swap.
        for a in e["alternatives"]:
            v = a.get("verification", {})
            if v.get("contradiction"):
                a["recommended"] = False
                a["not_recommended_because"] = "index mapping contradicted by realised returns"
            elif v.get("grade") in ("fail",):
                a["recommended"] = False
                a["not_recommended_because"] = (
                    f"compounded outcome differs by {v.get('gap_pp'):+.2f}pp a year, "
                    f"beyond the {GRADE_C}pp floor")
            elif v.get("grade") in ("no_data", "insufficient"):
                a["recommended"] = False
                a["not_recommended_because"] = f"not verifiable: {v.get('basis')}"
            else:
                a["recommended"] = True

        if not any(a.get("recommended") for a in e["alternatives"]) and e["tier"] != 3:
            e["verdict"] = "no_verified_equivalent"
            e["verdict_note"] = (
                f"Candidate funds tracking {e['index_label']} were found, but none "
                f"survived verification against realised returns. Presenting one anyway "
                f"would be offering a match the evidence does not support.")

    swap["_meta"]["return_verification"] = {
        "run": "2026-08-02",
        "window_years": args.years,
        "metric": "cumulative annualised total-return gap over the overlap window",
        "why_not_tracking_error": (
            "Volatility of return differences is unusable across venues. Measured: "
            "SPY vs IVV (both US) 0.31pp, VUAA vs SPXS (both London) 0.99pp, but SPY vs "
            "VUAA (across venues) 6.03pp -- an artefact of London closing 4.5 hours "
            "before New York, not a difference between the funds. It persists at "
            "quarterly frequency (3.0pp), so grading on it would fail every match."),
        "tax_basis": (
            "Yahoo reinvests distributions gross, which assumes a holder who suffers no "
            "withholding. The US line is reduced by 30 per cent of its gross yield to put "
            "it on a Singapore-holder basis before the gap is taken."),
        "grade_bands_abs_pp": {"A": f"<= {GRADE_A}", "B": f"<= {GRADE_B}",
                               "C": f"<= {GRADE_C}", "fail": f"> {GRADE_C}"},
        "pairs_graded": graded,
        "grade_distribution": dist,
        "tier1_contradictions": len(flagged),
    }

    with open(p, "w", encoding="utf-8") as fh:
        json.dump(swap, fh, indent=1)

    print(f"\ngraded {graded} pairs: {dist}")
    print(f"tier-1 contradictions: {len(flagged)}")
    for u, a, g in sorted(flagged, key=lambda x: -abs(x[2]))[:12]:
        print(f"  !! {u} -> {a}: {g:+.2f}pp pa")


if __name__ == "__main__":
    main()
