"""Guard tests for the weekly price series behind the charts.

These exist because a chart shipped showing the Bosera bitcoin ETF falling from
HK$737 to HK$7 in a single week. Nothing of the sort happened: the fund split
roughly 10:1 in December 2024, Yahoo never adjusted for it, and — the part that
makes this dangerous — Yahoo reports NO split for the fund and returns an
adjusted close identical to its raw close. There is nothing in the feed that
says the series is broken. It has to be inferred from the shape.

The same defect was sitting undetected on SCY, an SGX fund unrelated to the
crypto work, so this is a property of the data source rather than of one fund.

The response is to start the series after the last level shift rather than throw
it away: the segment after the shift is internally consistent and can be charted
honestly, provided the page says the history is truncated and every derived
number is confined to the kept segment. Only when too little survives does the
series get withheld outright.

Ranked by how bad the failure is:

  1. Rendering a level shift as a real price move.        (catastrophic, silent)
  2. Computing a return across two scales.                (silent, looks precise)
  3. Charting a truncated series without saying so.       (silent, misleads on age)
  4. Withholding or truncating a series that was fine.    (visible, recoverable)
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pipeline import (  # noqa: E402
    detect_price_breaks, BREAK_HI, BREAK_LO, MIN_SEGMENT, CONFIRM_BARS)


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def prices():
    return load("prices.json")


@pytest.fixture(scope="module")
def series(prices):
    return {k: v for k, v in prices.items()
            if k != "asof" and isinstance(v, dict) and v.get("c")}


def usable(ser):
    """The segment after the last level shift.

    A truncated series records this as usable_from, because the page slices
    there. A withheld one does not — nothing is charted, so there is no slice —
    but its suspect record still carries the index, which is what proves the
    segment was too short to be worth keeping.
    """
    i = ser.get("usable_from")
    if i is None:
        i = (ser.get("suspect") or {}).get("at", 0)
    return ser["c"][i:]


# ---- 1. nothing broken renders -------------------------------------------

def test_no_break_survives_into_the_charted_segment(series):
    """The core guarantee, and the one that matters most.

    Whatever the page draws must be on a single scale. A step inside the kept
    segment is a cliff in the chart and a worst-drop stat reporting a crash that
    never happened.
    """
    for tk, ser in series.items():
        if ser.get("suspect"):
            continue                      # nothing is drawn at all
        left = detect_price_breaks(usable(ser))
        assert not left, (
            f"{tk} still has {len(left)} break(s) inside the segment the page "
            f"charts: {left}")


def test_every_broken_series_is_either_truncated_or_withheld(series):
    """A step must always produce one of the two outcomes. Neither is the bug."""
    for tk, ser in series.items():
        if detect_price_breaks(ser["c"]):
            assert ser.get("truncated") or ser.get("suspect"), (
                f"{tk} has a break but is neither truncated nor withheld — its "
                f"chart and trailing returns would render across the shift")


def test_truncation_and_withholding_are_mutually_exclusive(series):
    for tk, ser in series.items():
        assert not (ser.get("truncated") and ser.get("suspect")), (
            f"{tk} is marked both truncated and withheld")


def test_no_isolated_spikes_survive_the_build(series):
    """Bad ticks are dropped rather than withheld, because the level returning
    proves the bar was the anomaly."""
    for tk, ser in series.items():
        spikes = [b for b in detect_price_breaks(ser["c"]) if b["kind"] == "spike"]
        assert not spikes, f"{tk} still carries {len(spikes)} isolated bad tick(s)"


# ---- 2. the truncation is honest -----------------------------------------

def test_truncated_series_keep_enough_history_to_chart(series):
    """Below the floor the honest answer is no chart, not a short one. A chart
    drawn from a handful of bars invites a trend reading it cannot support."""
    for tk, ser in series.items():
        if ser.get("truncated"):
            kept = sum(1 for c in usable(ser) if c is not None)
            assert kept >= MIN_SEGMENT, (
                f"{tk} charts only {kept} bars, below the {MIN_SEGMENT} floor")


def test_withheld_series_really_were_too_short(series):
    """The other direction: withholding costs the reader a chart, so it has to
    be because nothing usable survived, not because truncation was skipped."""
    for tk, ser in series.items():
        if ser.get("suspect"):
            kept = sum(1 for c in usable(ser) if c is not None)
            assert kept < MIN_SEGMENT, (
                f"{tk} is withheld but {kept} usable bars survive — it should "
                f"have been truncated and charted instead")


def test_raw_history_is_kept_not_deleted(series):
    """Truncation is a view, not a deletion. The discarded segment is the
    evidence the shift happened and is what a proper repair works from."""
    for tk, ser in series.items():
        t = ser.get("truncated")
        if t:
            assert ser.get("usable_from"), f"{tk} truncated with no usable_from"
            assert len(ser["c"]) > ser["usable_from"], f"{tk} kept nothing"
            assert ser["usable_from"] == t["dropped"], (
                f"{tk}: usable_from and the recorded dropped count disagree")


def test_truncation_records_what_the_caption_needs(series):
    """The notice above the chart states how many weeks were dropped and by what
    factor. Missing fields there mean a caveat that renders half-written."""
    for tk, ser in series.items():
        t = ser.get("truncated")
        if t:
            for k in ("n", "ratio", "at", "kept", "dropped"):
                assert k in t, f"{tk} truncation record is missing {k!r}"


# ---- 3. the detector itself ----------------------------------------------

def test_detector_separates_a_spike_from_a_step():
    """The two need opposite handling, so the classification carries weight."""
    flat = [10.0] * 12
    spike = flat[:6] + [95.0] + flat[7:]
    step = [10.0] * 6 + [1.0] * 6

    got = detect_price_breaks(spike)
    assert [b["kind"] for b in got] == ["spike", "spike"], (
        f"a bar that returns to level should be a spike, got {got}")

    got = detect_price_breaks(step)
    assert [b["kind"] for b in got] == ["step"], (
        f"a level that shifts and stays should be a step, got {got}")


def test_detector_ignores_ordinary_market_moves():
    """A 30% week is brutal and real; crypto does it. The detector must not fire
    on it, or every crypto chart on the site disappears."""
    harsh = [100.0, 72.0, 95.0, 70.0, 90.0, 65.0, 88.0]
    assert not detect_price_breaks(harsh), "detector fired on a real market move"


def test_detector_thresholds_sit_outside_real_weekly_moves():
    """The bounds are the whole calibration. Stated here so a future tightening
    has to face the fact that halving in a week is a market event."""
    assert BREAK_HI >= 2.0 and BREAK_LO <= 0.5
    assert CONFIRM_BARS >= 2, (
        "a new level needs more than one observation to be a level")


def test_detector_survives_gaps_and_zeros():
    """Real series carry None holidays, and a zero close would divide by zero."""
    assert detect_price_breaks([]) == []
    assert detect_price_breaks([None, None]) == []
    assert detect_price_breaks([10.0, None, 10.2, None, 10.1]) == []
    detect_price_breaks([0.0, 10.0, 10.0])   # must not raise


# ---- 4. derived numbers ---------------------------------------------------

def test_no_computed_yield_spans_a_shift(series):
    """A yield divides trailing distributions by the LAST close. That is safe on
    a truncated series only when the shift is older than the 12 months being
    summed; otherwise numerator and denominator sit on different scales."""
    universe = load("etf_universe.json")
    by_tk = {f["ticker"]: f for f in universe["funds"]}
    for tk, ser in series.items():
        f = by_tk.get(tk)
        if not f or f.get("yield_src") != "yahoo_ttm":
            continue
        assert not ser.get("suspect"), f"{tk} took a yield from a withheld series"
        t = ser.get("truncated")
        if t:
            assert len(ser["c"]) - t["at"] > 52, (
                f"{tk} took a computed yield over a window containing its shift")


def test_the_universe_records_the_flags_for_the_page(series):
    """The UI reads these off the fund record, so they have to reach
    etf_universe.json and not just prices.json."""
    universe = load("etf_universe.json")
    by_tk = {f["ticker"]: f for f in universe["funds"]}
    for tk, ser in series.items():
        if tk not in by_tk:
            continue
        if ser.get("suspect"):
            assert by_tk[tk].get("price_suspect"), f"{tk} suspect flag not in universe"
        if ser.get("truncated"):
            assert by_tk[tk].get("price_truncated"), f"{tk} truncation not in universe"


# ---- 5. the escalation baseline ------------------------------------------

def test_baseline_matches_the_live_set_exactly(series):
    """data/price_withheld.json is what lets the unattended weekly run tell a NEW
    break from a known one. It is only useful while it is accurate, so drift is
    a failure in both directions: an unlisted break means the next scheduled run
    fails on something already known about, and a stale entry means the file is
    describing a fund that is fine."""
    baseline = {w["ticker"] for w in load("price_withheld.json")["withheld"]}
    live = {tk for tk, s in series.items() if s.get("suspect") or s.get("truncated")}
    missing, stale = sorted(live - baseline), sorted(baseline - live)
    assert not missing, (
        f"affected but not in data/price_withheld.json: {missing}. Add them with "
        f"what was found, or the scheduled run will fail on a known break.")
    assert not stale, (
        f"listed in data/price_withheld.json but no longer affected: {stale}. "
        f"Confirm the series was really repaired, then prune the entry.")
