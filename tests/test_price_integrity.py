"""Guard tests for the weekly price series behind the charts.

These exist because a chart shipped showing the Bosera bitcoin ETF falling from
HK$737 to HK$7 in a single week. Nothing of the sort happened: the fund split
roughly 10:1 in December 2024, Yahoo never adjusted for it, and — the part that
makes this dangerous — Yahoo reports NO split for the fund and returns an
adjusted close identical to its raw close. There is nothing in the feed that
says the series is broken. It has to be inferred from the shape.

The same defect was sitting undetected on SCY, an SGX fund unrelated to the
crypto work, so this is a property of the data source rather than of one fund.

Ranked by how bad the failure is:

  1. Rendering a level shift as a real price move.     (catastrophic, silent)
  2. Computing trailing returns across two scales.     (silent, looks precise)
  3. Deriving a yield from a mis-scaled last close.    (silent)
  4. Losing a usable chart to an over-eager detector.  (visible, recoverable)
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pipeline import detect_price_breaks, BREAK_HI, BREAK_LO  # noqa: E402


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


# ---- 1. nothing broken renders -------------------------------------------

def test_every_series_with_a_step_is_flagged_suspect(series):
    """The core guarantee. A step that is not flagged is a chart with a cliff in
    it, and a worst-drop stat reporting a crash that never happened."""
    for tk, ser in series.items():
        steps = [b for b in detect_price_breaks(ser["c"]) if b["kind"] == "step"]
        if steps:
            assert ser.get("suspect"), (
                f"{tk} has {len(steps)} unadjusted level shift(s) but is not "
                f"flagged suspect — its chart and trailing returns would render")


def test_flagged_series_really_do_have_a_break(series):
    """The other direction. Withholding a chart is a real cost, so the flag must
    not be set on a series that is fine."""
    for tk, ser in series.items():
        if ser.get("suspect"):
            assert detect_price_breaks(ser["c"]), (
                f"{tk} is flagged suspect but no break is detectable — the flag "
                f"is stale and a usable chart is being withheld for nothing")


def test_no_isolated_spikes_survive_the_build(series):
    """Bad ticks are dropped rather than withheld, because the level returning
    proves the bar was the anomaly. Any spike still present means the sanitiser
    did not run."""
    for tk, ser in series.items():
        spikes = [b for b in detect_price_breaks(ser["c"]) if b["kind"] == "spike"]
        assert not spikes, f"{tk} still carries {len(spikes)} isolated bad tick(s)"


# ---- 2. the detector itself ----------------------------------------------

def test_detector_separates_a_spike_from_a_step():
    """The two need opposite handling, so the classification carries weight.

    A spike returns to level and its bar can simply be dropped. A step does not,
    and no bar can be dropped to fix it.
    """
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


def test_detector_thresholds_sit_outside_real_weekly_moves(series):
    """The bounds are the whole calibration. Stated here so a future tightening
    has to face the fact that halving in a week is a market event, not a defect."""
    assert BREAK_HI >= 2.0 and BREAK_LO <= 0.5


def test_detector_survives_gaps_and_zeros():
    """Real series carry None holidays, and a zero close would divide by zero."""
    assert detect_price_breaks([]) == []
    assert detect_price_breaks([None, None]) == []
    assert detect_price_breaks([10.0, None, 10.2, None, 10.1]) == []
    detect_price_breaks([0.0, 10.0, 10.0])   # must not raise


# ---- 3. derived numbers ---------------------------------------------------

def test_suspect_series_do_not_seed_a_computed_yield(series):
    """A yield divides distributions by the LAST close. SCY's break is on its
    final bar, which is exactly that denominator."""
    universe = load("etf_universe.json")
    suspect = {tk for tk, s in series.items() if s.get("suspect")}
    for f in universe["funds"]:
        if f["ticker"] in suspect:
            assert f.get("yield_src") != "yahoo_ttm", (
                f"{f['ticker']} took a computed yield from a suspect series")


def test_the_universe_records_the_flag_for_the_page(series):
    """The UI reads price_suspect off the fund record to withhold the columns,
    so the flag has to reach etf_universe.json and not just prices.json."""
    universe = load("etf_universe.json")
    by_tk = {f["ticker"]: f for f in universe["funds"]}
    for tk, ser in series.items():
        if ser.get("suspect") and tk in by_tk:
            assert by_tk[tk].get("price_suspect"), (
                f"{tk} is suspect in prices.json but not in etf_universe.json")


def test_known_broken_funds_stay_withheld(series):
    """The four found when this was written. If one starts passing, the source
    has been fixed or the detector has been weakened — check which before
    deleting the name from this list."""
    for tk in ("3008", "3009", "3460", "SCY"):
        if tk in series:
            assert series[tk].get("suspect"), (
                f"{tk} is no longer withheld. Confirm the underlying series was "
                f"actually repaired before accepting this.")
