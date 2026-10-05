"""Guard tests for fund inception dates behind the "Launched in the last N years" filter.

The filter is only honest if every fund can be placed. A fund is placed either by
a sourced inception date (curated.json "inception") or by Yahoo's first-trade date
when that date is older than the longest window: a fund cannot trade before it
exists, so first trade is an upper bound on inception. A fund with neither drops
out of the filtered list, and the page says so, but the build should never ship
in that state unnoticed.

Ways this goes silently wrong, ranked:

  1. A cross-listing date recorded as inception: an old overseas fund shows as new.
  2. A curated date for the wrong fund or share class: caught when it post-dates
     first trade, which is impossible for the right fund.
  3. A new fund added to the universe with no curated date: falls out of the filter.
"""
import datetime
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pipeline import (  # noqa: E402
    years_before, INCEPTION_WINDOW_YEARS, INCEPTION_FT_SLACK_DAYS)


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


# ---- date arithmetic (Python months are 1-indexed) -----------------------

@pytest.mark.parametrize("d, n, want", [
    (datetime.date(2026, 1, 1), 5, datetime.date(2021, 1, 1)),      # year boundary
    (datetime.date(2025, 12, 31), 5, datetime.date(2020, 12, 31)),  # year boundary
    (datetime.date(2026, 3, 1), 5, datetime.date(2021, 3, 1)),      # month boundary
    (datetime.date(2026, 4, 30), 1, datetime.date(2025, 4, 30)),    # month end
    (datetime.date(2024, 2, 29), 5, datetime.date(2019, 2, 28)),    # leap day, no 29 Feb target
    (datetime.date(2024, 2, 29), 4, datetime.date(2020, 2, 29)),    # leap day to leap year
])
def test_years_before(d, n, want):
    assert years_before(d, n) == want


# ---- data guards ----------------------------------------------------------

@pytest.fixture(scope="module")
def funds():
    return load("etf_universe.json")["funds"]


@pytest.fixture(scope="module")
def prices():
    return load("prices.json")


@pytest.fixture(scope="module")
def curated_incep():
    return load("curated.json").get("inception", {})


def ft_date(prices, tk):
    p = prices.get(tk)
    if not isinstance(p, dict) or p.get("ft") is None:
        return None
    return datetime.datetime.fromtimestamp(p["ft"], datetime.timezone.utc).date()


def test_every_fund_is_placed(funds, prices):
    cutoff = years_before(datetime.date.fromisoformat(prices["asof"]), INCEPTION_WINDOW_YEARS)
    unplaced = [f["ticker"] for f in funds
                if not f.get("incep")
                and not (f.get("incep_by") and f["incep_by"] < cutoff.isoformat())]
    assert not unplaced, (f"no sourced inception and no first trade before {cutoff}: {unplaced} "
                          f"- add them to curated.json 'inception'")


def test_curated_entries_are_sourced(curated_incep):
    for tk, c in curated_incep.items():
        if tk.startswith("_"):
            continue
        datetime.date.fromisoformat(c["date"])
        assert c.get("source") and c.get("url"), f"{tk}: inception needs a source and a url"


def test_curated_not_after_first_trade(curated_incep, prices):
    for tk, c in curated_incep.items():
        if tk.startswith("_"):
            continue
        ft = ft_date(prices, tk)
        if ft is None:
            continue
        gap = (datetime.date.fromisoformat(c["date"]) - ft).days
        assert gap <= INCEPTION_FT_SLACK_DAYS, (
            f"{tk}: inception {c['date']} is {gap} days after first trade {ft}")


def test_universe_carries_curated_dates(funds, curated_incep):
    by = {f["ticker"]: f for f in funds}
    for tk, c in curated_incep.items():
        if tk.startswith("_"):
            continue
        assert tk in by, f"{tk}: curated inception for a fund not in the universe"
        assert by[tk].get("incep") == c["date"], f"{tk}: pipeline did not apply the curated date"
