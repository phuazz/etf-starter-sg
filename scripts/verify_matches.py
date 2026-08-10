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

# --- Which window, and how its ends are measured ---------------------------
#
# The gap is ANNUALISED, so an error at either endpoint is divided by the window
# length. That makes short windows expensive: measured across this universe, the
# same pair regraded with the window ending one to four weeks earlier moves by
#
#     1y  0.90pp     avg(1y,3y)  0.55pp     2y  0.45pp     3y  0.31pp   5y 0.13pp
#
# against an A band only 0.50pp wide. A one-year window mostly measures which
# week the data happened to stop on, and averaging a 1y leg with a 3y one does
# not rescue it: the two windows SHARE their end date, so the noise is common
# rather than independent. Measured, not assumed -- the two move the same
# direction on 75% of end-date shifts, and the 1y leg moves 2.9x as far as the
# 3y leg, which is the 3x that dividing by a third of the time predicts.
#
# Three years is the shortest window that resolves inside the A band, and it is
# a better instrument than five, not merely a shorter one: it grades MORE pairs
# A (56 against 47) and MORE pairs fail (10 against 4) at the same time. Both
# at once is what a sharper measurement looks like. Fees get cut and replication
# changes; a five-year record blends a fund's present construction with its old
# one and hides both improvements and drift.
#
# Five years is still measured and travels with the result as context. Where the
# two disagree materially, that disagreement IS the signal -- something about
# the pair changed -- and it is surfaced rather than averaged away.
GRADE_YEARS = 3
CONTEXT_YEARS = 5
ENDPOINT_K = 8           # median of this many observations at each end
DRIFT_FLAG_PP = 1.5      # |3y - 5y| past this and the pair is flagged as changed

# Absolute annualised gap, after putting both sides on a Singapore-holder basis.
GRADE_A, GRADE_B, GRADE_C = 0.50, 1.50, 3.00

# --- Price-history breaks --------------------------------------------------
#
# A gap measured endpoint to endpoint assumes the series in between is ONE
# continuous record. Twice it is not, and both cases were found by looking at
# the shape of the divergence rather than its size:
#
#   QQQ  vs XNAS   2023-01-23   ratio 1.105 -> 1.376, still 1.369 three and a
#                               half years later. A step, then flat.
#   SOXX vs SEMI   2024-01-08   ratio 0.980 -> 0.766 and holds; 0.804 of the
#                               prior level, which is what an unadjusted 5:4
#                               share split looks like.
#
# Neither is a fund that tracks badly. XNAS follows QQQ almost exactly on both
# sides of its step -- strip the step and five-year divergence falls from +36.9%
# to +4.9%, about 0.96pp a year, roughly the accumulating-versus-distributing
# difference. Reporting these as "the index mapping is wrong or the funds
# replicate differently" named two causes and both were false.
#
# The opposite case also exists and needs opposite treatment:
#
#   URTH vs SWDA   2025-10-20   SWDA.L prints 12,837.81 between neighbours of
#                               9,370 and 9,818, then reverts.
#
# One bad print. It does not move an endpoint-to-endpoint gap, which is why
# SWDA still grades A correctly, but it wrecks the monthly correlation shown in
# the fit tooltip. That single week is why several honest pairs read 0.78-0.87
# where two funds on one index should read above 0.99.
#
# So the test is not how big the move is, it is whether the level HOLDS. A step
# that persists breaks the series; a spike that reverts is a bad tick and is
# dropped. Sigma is measured from the median absolute deviation, not the
# standard deviation -- an outlier this large inflates its own denominator and
# would hide from a plain z-score.
BREAK_SIGMA = 8.0        # how far a single period must stand out to be examined
BREAK_WIN = 8            # periods either side used to establish the level
STEP_MIN_PCT = 5.0       # a step must move the LEVEL by at least this
SPIKE_MIN_PCT = 10.0     # a tick must sit this far off its neighbours


def _mad_sigma(x):
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    return 1.4826 * mad if mad > 0 else float(np.std(x))


def _ratio(j):
    return (j["alt"] / j["alt"].iloc[0]) / (j["us"] / j["us"].iloc[0])


def find_step(ratio):
    """A discontinuity that HOLDS: the level before and the level after differ.

    Sigma alone cannot decide this. Two bond funds on one index track so closely
    that the median absolute deviation is minuscule, and a 0.1% wobble measures
    50 sigma -- the first cut of this flagged eight pairs, six of them moves of
    0.0% to 0.2%. The size floor is what makes it mean something; sigma only
    picks which periods are worth examining.

    Classification is on the two LEVELS, never on the jump itself. A reverting
    tick also produces a huge single-period move, and the difference between the
    two is entirely whether the level afterwards is somewhere new.
    """
    lr = np.log(ratio).diff().dropna()
    if len(lr) < 3 * BREAK_WIN:
        return None
    sigma = _mad_sigma(lr.to_numpy())
    if not sigma or not np.isfinite(sigma):
        return None
    z = (lr / sigma).abs()
    best = None
    for ts in z[z > BREAK_SIGMA].index:
        if abs(np.expm1(lr.loc[ts])) * 100 < STEP_MIN_PCT:
            continue
        i = ratio.index.get_loc(ts)
        before, after = ratio.iloc[max(0, i - BREAK_WIN):i], ratio.iloc[i + 1:i + 1 + BREAK_WIN]
        if len(before) < 3 or len(after) < 3:
            continue
        held = (after.median() / before.median() - 1) * 100
        if abs(held) < STEP_MIN_PCT:
            continue                      # level came back: a tick, not a step
        step = {"date": str(ts.date()), "shift_pct": round(held, 2),
                "sigma": round(float(z.loc[ts]), 1),
                "before": round(float(before.median()), 4),
                "after": round(float(after.median()), 4)}
        if best is None or abs(step["shift_pct"]) > abs(best["shift_pct"]):
            best = step
    return best


def find_spikes(ratio):
    """Single observations sitting far off their own neighbourhood, which come
    straight back. Measured against a centred rolling median so a genuine trend
    cannot register, and floored at 10% because cross-venue closing times
    legitimately move a ratio a few per cent."""
    med = ratio.rolling(2 * BREAK_WIN + 1, center=True, min_periods=5).median()
    dev = np.log(ratio / med).dropna()
    if len(dev) < 3 * BREAK_WIN:
        return []
    sigma = _mad_sigma(dev.to_numpy())
    if not sigma or not np.isfinite(sigma):
        return []
    hit = dev[(dev / sigma).abs() > BREAK_SIGMA]
    return sorted(ts for ts in hit.index
                  if abs(np.expm1(dev.loc[ts])) * 100 >= SPIKE_MIN_PCT)


def find_breaks(j):
    """Returns (spike timestamps to drop, persistent step or None).

    Works on the pair RATIO, never on either series alone, so a real market move
    -- which both sides share -- cannot register as either.
    """
    ratio = _ratio(j)
    step = find_step(ratio)
    if step:
        return [], step               # a stepped series is not worth despiking
    return find_spikes(ratio), None


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
    """Convert a quoted series to USD, or return None if the currency is not
    handled.

    An unhandled currency returns None, the series never reaches the
    comparison, and the pair grades "no_data" -- which reads as a legitimate
    refusal rather than as the bug it is. That happened: adding SGX lines
    introduced SGD, which had no branch here, and the Singapore gold fund
    dropped out looking unverifiable. Any currency added to the alternatives
    pool must be added here too, and main() now asserts that.
    """
    if ccy == "USD":
        return s
    if ccy in ("GBP", "GBp"):
        v = s / 100.0 if ccy == "GBp" else s
        return v * fx["GBPUSD=X"].reindex(v.index).ffill()
    if ccy == "EUR":
        return s * fx["EURUSD=X"].reindex(s.index).ffill()
    if ccy == "SGD":
        return s * fx["SGDUSD=X"].reindex(s.index).ffill()
    return None


HANDLED_CCY = {"USD", "GBP", "GBp", "EUR", "SGD"}
FX_SYMBOLS = ["GBPUSD=X", "EURUSD=X", "SGDUSD=X"]


def ann(series, k=ENDPOINT_K):
    """Annualised return, taking each end as the median of k observations.

    A single closing price at each end hands the whole result to two particular
    days. That matters more here than usual: London and New York keep different
    holiday calendars, so a week where one venue is shut moves only one side of
    the pair. Taking a median at each end, and measuring the time base between
    the two MIDPOINTS so the span stays honest, cuts the endpoint sensitivity by
    roughly two thirds at every window length tested.
    """
    if len(series) < 2 * k + 8:
        k = 1                       # too short to trim; fall back to the closes
    if k == 1:
        v0, v1 = series.iloc[0], series.iloc[-1]
        t0, t1 = series.index[0], series.index[-1]
    else:
        v0, v1 = series.iloc[:k].median(), series.iloc[-k:].median()
        t0, t1 = series.index[:k][k // 2], series.index[-k:][k // 2]
    yrs = (t1 - t0).days / 365.25
    if yrs <= 0 or v0 <= 0:
        return None, 0.0
    return (v1 / v0) ** (1 / yrs) - 1, yrs


def tail(j, years):
    """The most recent `years` of a joined pair."""
    return j[j.index >= j.index[-1] - pd.Timedelta(days=int(365.25 * years))]


def _window_gap(seg, us_gross_yield):
    """Annualised gap over one window, or None if that window is unusable.

    Returns (gap_pp, detail, step). A step INSIDE the window makes it unusable:
    the series is two series there and any gap across it measures the join.
    """
    step = find_step(_ratio(seg)) if len(seg) > 3 * BREAK_WIN else None
    if step:
        return None, {}, step
    a_us, yrs = ann(seg["us"])
    a_alt, _ = ann(seg["alt"])
    if a_us is None or a_alt is None:
        return None, {}, None
    drag = (us_gross_yield or 0.0) * US_HOLDER_RATE
    a_us_net = a_us - drag
    return (a_alt - a_us_net) * 100, {
        "years": round(yrs, 2),
        "us_ann_gross_pp": round(a_us * 100, 3),
        "us_holder_wht_drag_pp": round(drag * 100, 3),
        "us_ann_after_wht_pp": round(a_us_net * 100, 3),
        "alt_ann_pp": round(a_alt * 100, 3),
    }, None


# NOTE ON THE GAP METRIC -- measured 2026-08-07, NOT yet fixed.
#
# `gap` is the ARITHMETIC difference of two annualised returns. It should be the
# geometric one: (1 + alt) / (1 + us) - 1. The two agree closely at ordinary
# return levels and come apart badly at high ones, because subtracting two large
# compounded rates is not the same as compounding their ratio.
#
# Measured on SMH against SMGB over the last 12 months: the US line annualised
# 130.6% and the UCITS line 171.2%, so this reports a 40.7pp gap. The pair's
# actual relative performance over that window is 1.150, about 17.7% a year --
# the reported figure is more than twice the real divergence.
#
# The three-year gaps this tool ships are distorted in the same direction but far
# less, since three-year annualised returns are smaller. It is recorded here
# rather than silently corrected: fixing it moves every gap and every grade in
# the tool, which is a decision to take deliberately.


def compare(s_us, s_alt, us_gross_yield):
    j = pd.concat([s_us.rename("us"), s_alt.rename("alt")], axis=1,
                  join="inner").dropna()
    months = len(j.resample("ME").last().dropna())
    if months < MIN_MONTHS:
        return {"grade": "insufficient", "months": months, "gap_pp": None,
                "basis": f"only {months} overlapping months, need {MIN_MONTHS}"}

    # Reverting ticks are dropped before anything is measured. A spike landing
    # on a window edge would otherwise walk straight into an endpoint median.
    transient, _ = find_breaks(j)
    dropped = []
    if transient:
        dropped = sorted(str(t.date()) for t in transient)
        j = j.drop(index=transient)

    grade_seg = tail(j, GRADE_YEARS)
    gap, detail, step = _window_gap(grade_seg, us_gross_yield)
    if step:
        # The step falls inside the window being graded, so there is no usable
        # record. Where a step sits only in the LONGER history the pair is still
        # graded, on the recent window, and the long context is withheld.
        return {"grade": "break", "months": months, "gap_pp": None,
                "price_break": step,
                "basis": (f"price history steps {step['shift_pct']:+.1f}% on "
                          f"{step['date']} ({step['sigma']} sigma) within the "
                          f"{GRADE_YEARS}-year window and holds the new level, so the "
                          f"record cannot be read as one continuous series. Not a "
                          f"statement about how the fund tracks.")}
    if gap is None:
        return {"grade": "insufficient", "months": months, "gap_pp": None,
                "basis": "degenerate window"}

    m = grade_seg.resample("ME").last().pct_change().dropna()
    corr = float(np.corrcoef(m["us"], m["alt"])[0, 1]) if len(m) > 2 else None
    g = abs(gap)
    grade = ("A" if g <= GRADE_A else "B" if g <= GRADE_B
             else "C" if g <= GRADE_C else "fail")
    out = {"grade": grade, "months": months, "gap_pp": round(gap, 3),
           "monthly_corr": None if corr is None else round(corr, 4), **detail}



    # The longer window as context, never as the grade.
    ctx, _, ctx_step = _window_gap(tail(j, CONTEXT_YEARS), us_gross_yield)
    if ctx is not None:
        out["gap_5y_pp"] = round(ctx, 3)
        drift = gap - ctx
        if abs(drift) > DRIFT_FLAG_PP:
            out["drift_pp"] = round(drift, 3)
    elif ctx_step:
        out["long_history_unusable"] = (
            f"Graded on {GRADE_YEARS} years. The longer record is not usable: it steps "
            f"{ctx_step['shift_pct']:+.1f}% on {ctx_step['date']} and holds the new "
            f"level, which is a break in the price series rather than anything the "
            f"fund did.")
    out["basis"] = (
        f"annualised total return over the most recent {detail['years']:.1f}y, USD, with "
        f"each end taken as the median of {ENDPOINT_K} weeks; US line reduced by "
        f"{detail['us_holder_wht_drag_pp']:.2f}pp for the 30% a Singapore holder suffers "
        f"on distributions. Positive gap = alternative ahead."
        + (f" Over {CONTEXT_YEARS} years the gap was {out['gap_5y_pp']:+.2f}pp."
           if "gap_5y_pp" in out else "")
        + (f" {len(dropped)} reverting price spike(s) removed ({', '.join(dropped)}); each "
           f"moved the pair ratio past {BREAK_SIGMA:.0f} sigma and came back, so it is a "
           f"bad print rather than a move either fund made." if dropped else ""))
    if dropped:
        out["outliers_dropped"] = dropped
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    p = os.path.join(DATA, "swap_map.json")
    swap = json.load(open(p, encoding="utf-8"))
    ucits = {f["ticker"]: f for f in json.load(
        open(os.path.join(DATA, "ucits_universe.json"), encoding="utf-8"))["funds"]}
    index_map = json.load(
        open(os.path.join(DATA, "index_map.json"), encoding="utf-8"))["indices"]
    us_meta = {e["ticker"]: e for e in json.load(
        open(os.path.join(DATA, "us_situs_map.json"), encoding="utf-8"))["etfs"]}

    us_t = sorted({e["ticker"] for e in swap["etfs"]})
    # Venue decides the Yahoo suffix: London lines are .L, Singapore lines .SI.
    # Getting this wrong returns no history at all, which the refusal floor
    # would read as "unverifiable" rather than as a bug.
    suffix = {}
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            suffix[a["ticker"]] = ".SI" if a.get("venue") == "SGX" else ".L"
    alt_t = sorted(suffix)

    # Fail loudly on an unhandled quote currency rather than letting it become
    # a silent "no_data" that reads like an honest refusal.
    seen_ccy = {a["ccy"] for e in swap["etfs"] for a in e["alternatives"] if a.get("ccy")}
    unhandled = sorted(seen_ccy - HANDLED_CCY)
    if unhandled:
        raise SystemExit(f"FATAL: no USD conversion for {unhandled} — add a branch "
                         f"to to_usd() and the rate to FX_SYMBOLS")

    px = fetch(us_t + [t + suffix[t] for t in alt_t] + FX_SYMBOLS,
               args.years, args.refresh)
    print(f"  cache holds {px.shape[1]} series")

    fx = px[[c for c in FX_SYMBOLS if c in px]]
    usd = {}
    for t in us_t:
        if t in px:
            usd[t] = px[t].dropna()
    alt_ccy = {a["ticker"]: a["ccy"] for e in swap["etfs"] for a in e["alternatives"]}
    for t in alt_t:
        c = t + suffix[t]
        if c in px:
            s = to_usd(px[c].dropna(), alt_ccy.get(t) or ucits.get(t, {}).get("ccy"), fx)
            if s is not None and len(s.dropna()):
                usd[t] = s.dropna()

    graded, dist, flagged, broken, cleaned, hedged = 0, {}, [], [], [], []
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
            # A share class hedged to something other than the comparison
            # currency cannot be graded here at all. to_usd() converts the
            # QUOTE currency, which is right for an unhedged fund -- same
            # underlying, expressed in dollars -- but for a hedged class it
            # re-adds the exact exposure the fund charges a fee to remove.
            # Measured on VAGP: +4.37% a year in its native sterling, +2.22%
            # of GBP/USD strength, and the tool read the +6.61% sum as a
            # tracking failure against BNDW's +4.64%. Its USD-hedged sibling
            # VAGU lands within 0.06pp. Nothing was wrong with the fund.
            if a.get("hedge_ccy") and a["hedge_ccy"] != "USD":
                a["verification"] = {
                    "grade": "not_comparable",
                    "hedge_ccy": a["hedge_ccy"],
                    "basis": (f"hedged to {a['hedge_ccy']}, and every figure here is on a "
                              f"USD basis. Converting its price into USD re-adds the "
                              f"currency exposure the fund exists to remove, so any gap "
                              f"measured that way is the {a['hedge_ccy']}/USD move rather "
                              f"than a difference between the funds.")}
                dist["not_comparable"] = dist.get("not_comparable", 0) + 1
                hedged.append((e["ticker"], a["ticker"], a["hedge_ccy"]))
                continue
            v = compare(s_us, s_alt, gy)
            # Threshold is the C floor, not the B floor. Sector indices from
            # different providers legitimately differ by one to two points a
            # year -- S&P 500 Information Technology is not MSCI US IMI
            # Information Technology. Flagging those as broken would demote
            # genuinely useful swaps. Only a divergence past the C floor
            # indicates the mapping itself is wrong.
            if v.get("price_break"):
                broken.append((e["ticker"], a["ticker"], v["price_break"]))
            if v.get("outliers_dropped"):
                cleaned.append((e["ticker"], a["ticker"], v["outliers_dropped"]))
            # A contradiction is only claimable where the series is continuous.
            # Where it is not, the divergence measures the join, and asserting
            # that the mapping or the replication is at fault names a cause the
            # evidence does not support.
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
            # This script annotates swap_map.json IN PLACE, so anything an
            # earlier run wrote survives unless this one overwrites it. The
            # refusal fields are set only on the branches that refuse, so a pair
            # that stops being refused kept its old reason: removing a demotion
            # rule left ten pairs marked recommended AND carrying the reason
            # they had been set aside. Cleared before deciding, so the decision
            # below is the only thing that can put them back.
            a.pop("not_recommended_because", None)
            a.pop("verification_pending", None)
            v = a.get("verification", {})
            if v.get("grade") == "not_comparable":
                a["recommended"] = False
                a["not_recommended_because"] = (
                    f"hedged to {v['hedge_ccy']}, not USD. That is a different holding "
                    f"rather than a worse one: it removes the currency risk of the "
                    f"underlying bonds and replaces it with {v['hedge_ccy']} interest-rate "
                    f"exposure, which is a decision to take deliberately and not a "
                    f"like-for-like swap for a Singapore holder.")
            elif v.get("price_break"):
                b = v["price_break"]
                a["recommended"] = False
                a["not_recommended_because"] = (
                    f"not verifiable: the price history steps {b['shift_pct']:+.1f}% on "
                    f"{b['date']} and stays there, so the record before and after that "
                    f"date cannot be compared as one series. This says nothing about "
                    f"how the fund tracks its index.")
            elif v.get("contradiction"):
                a["recommended"] = False
                a["not_recommended_because"] = "index mapping contradicted by realised returns"
            elif v.get("grade") in ("fail",):
                a["recommended"] = False
                a["not_recommended_because"] = (
                    f"compounded outcome differs by {v.get('gap_pp'):+.2f}pp a year, "
                    f"beyond the {GRADE_C}pp floor")
            elif v.get("grade") == "no_data":
                a["recommended"] = False
                a["not_recommended_because"] = f"not verifiable: {v.get('basis')}"
            elif v.get("grade") == "insufficient":
                # Absence of evidence is not evidence against. A fund listed
                # months ago cannot have a multi-year record, and demoting it
                # on that basis would bury the best available answer -- the
                # Singapore-domiciled gold fund is exactly this case. It is
                # shown, clearly marked unverified, with the track-record risk
                # named rather than hidden behind a silent omission.
                a["recommended"] = True
                a["verification_pending"] = (
                    "Too new to check against a meaningful record — "
                    f"{v.get('basis')}. The domicile and cost figures stand; how "
                    "closely it tracks is simply not yet knowable, and a young "
                    "fund also carries the usual small-size and closure risks.")
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
        "window_years": GRADE_YEARS,
        "context_years": CONTEXT_YEARS,
        "why_this_window": (
            f"Graded on the most recent {GRADE_YEARS} years, not the full history. Fees get "
            f"cut and replication methods change, so a five-year record blends a fund's "
            f"present construction with its old one. Three years is also the shortest "
            f"window that resolves inside the 0.50pp A band: regrading with the window "
            f"ending one to four weeks earlier moves a pair by 0.90pp at one year and "
            f"0.31pp at three. Averaging a one-year leg into it does not help, because "
            f"both windows share an end date and the noise is common rather than "
            f"independent -- the two move together on 75% of end-date shifts. Three years "
            f"grades more pairs A than five does (56 against 47) AND fails more (10 "
            f"against 4), which is a sharper instrument rather than a noisier one."),
        "endpoint_rule": (
            f"each end is the median of {ENDPOINT_K} weekly observations, with the time "
            f"base measured between the midpoints. US and London holiday calendars differ, "
            f"so a single closing price at each end hands the result to two particular "
            f"days; this cuts endpoint sensitivity by roughly two thirds."),
        "drift_flag_pp": DRIFT_FLAG_PP,
        "metric": "cumulative annualised total-return gap over the graded window",
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
        "price_breaks": {
            "detector": (f"a STEP is a single-period move past {BREAK_SIGMA:.0f} sigma in "
                         f"the pair ratio whose LEVEL, measured as the median of the "
                         f"{BREAK_WIN} periods either side, then differs by at least "
                         f"{STEP_MIN_PCT:.0f}%. A TICK is one observation more than "
                         f"{SPIKE_MIN_PCT:.0f}% off a centred rolling median that comes "
                         f"straight back. Sigma is taken from the median absolute "
                         f"deviation, since an outlier this large inflates a standard "
                         f"deviation enough to hide inside it."),
            "why": ("An endpoint-to-endpoint gap assumes one continuous series. A step "
                    "means it is two, and the gap then measures the join rather than the "
                    "funds. A reverting tick leaves the gap alone but corrupts the "
                    "correlation, so it is dropped. The size floors matter more than the "
                    "sigma: two bond funds on one index track closely enough that a 0.1% "
                    "wobble measures 50 sigma, and without a floor the detector called "
                    "six such wobbles breaks."),
            "steps_found": [{"us": u, "alt": a, **b} for u, a, b in broken],
            "ticks_dropped": [{"us": u, "alt": a, "dates": d} for u, a, d in cleaned],
        },
    }

    with open(p, "w", encoding="utf-8") as fh:
        json.dump(swap, fh, indent=1)

    print(f"\ngraded {graded} pairs: {dist}")
    print(f"tier-1 contradictions: {len(flagged)}")
    for u, a, g in sorted(flagged, key=lambda x: -abs(x[2]))[:12]:
        print(f"  !! {u} -> {a}: {g:+.2f}pp pa")
    # Nothing here is silent: every series the detector touched is named, so a
    # pair that stops being graded cannot slip past as a quiet refusal.
    print(f"price-history steps: {len(broken)}")
    for u, a, b in broken:
        print(f"  ## {u} -> {a}: level {b['before']} -> {b['after']} "
              f"({b['shift_pct']:+.1f}%) on {b['date']}, {b['sigma']} sigma — not graded")
    print(f"reverting ticks dropped: {len(cleaned)}")
    for u, a, d in cleaned:
        print(f"  ~~ {u} -> {a}: {', '.join(d)}")
    print(f"cross-currency hedges, not graded: {len(hedged)}")
    for u, a, h in hedged:
        print(f"  ++ {u} -> {a}: hedged to {h}, not comparable on a USD basis")


if __name__ == "__main__":
    main()
