#!/usr/bin/env python3
"""Escalate a NEW withheld price series to a human.

Run: python scripts/check_withheld.py            (after the build)

sanitise_prices() already withholds any series carrying an unadjusted level
shift, so the page is correct whether three funds are broken or thirty. That is
exactly the problem for an unattended run: every log looks the same, and a fifth
fund developing a step would withhold itself perfectly and silently.

This compares the live flags in prices.json against the baseline in
data/price_withheld.json:

  new break      -> exit 1. The scheduled workflow goes red and GitHub notifies
                    the repository owner. Deliberately run AFTER the commit
                    step: a newly withheld fund means the page is behaving
                    correctly and should still ship. The failure is a message,
                    not a rollback.
  recovered      -> exit 0 with a notice. Good news must never block the
                    nightly. tests/test_price_integrity.py requires an exact
                    match, so the next local test run forces the prune.
  unchanged      -> exit 0, quiet.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def live_affected(prices):
    """Both outcomes count. A truncated series is still a fund whose history the
    data source has corrupted — it is charted from after the shift rather than
    withheld, but it is no less a break and no less in need of a real repair."""
    return {tk: ("withheld" if ser.get("suspect") else "truncated")
            for tk, ser in prices.items()
            if tk != "asof" and isinstance(ser, dict)
            and (ser.get("suspect") or ser.get("truncated"))}


def main():
    prices = load("prices.json")
    baseline = load("price_withheld.json")
    known = {w["ticker"] for w in baseline["withheld"]}
    live = live_affected(prices)

    new = sorted(set(live) - known)
    gone = sorted(known - set(live))

    kinds = ", ".join(f"{tk} ({k})" for tk, k in sorted(live.items()))
    print(f"price series affected: {len(live)}  (baseline {len(known)})")
    if live:
        print(f"  {kinds}")

    if gone:
        print("\nRECOVERED — no longer affected:")
        for tk in gone:
            print(f"  {tk}")
        print("  Prune these from data/price_withheld.json once you have confirmed\n"
              "  the series was really repaired; the test suite requires an exact match.")

    if not new:
        print("\nNo new breaks.")
        return 0

    names = {f["ticker"]: f["name"] for f in load("etf_universe.json")["funds"]}
    print("\n" + "=" * 70)
    print("NEW price-series break — a fund's history has been cut without anyone")
    print("being told")
    print("=" * 70)
    for tk in new:
        ser = prices[tk]
        info = ser.get("suspect") or ser.get("truncated") or {}
        print(f"\n  {tk}  {names.get(tk, '?')}   [{live[tk]}]")
        print(f"    {info.get('n', '?')} unexplained level shift(s), "
              f"largest ratio {info.get('ratio', '?')}")
        if live[tk] == "truncated":
            print(f"    {info.get('dropped', '?')} earlier week(s) excluded, "
                  f"{info.get('kept', '?')} charted")
        else:
            print(f"    only {info.get('kept', '?')} bar(s) survive — nothing charted")
    print(
        "\nThe page is already behaving correctly. A truncated fund charts only\n"
        "the segment after its shift and says so above the chart; a withheld one\n"
        "shows no chart and says why. Nothing wrong is being displayed.\n"
        "\nWhat is needed is a judgement this run cannot make: whether the fund\n"
        "genuinely split, and whether the series can be repaired against issuer\n"
        "or exchange data. Once checked, either repair it or add the ticker to\n"
        "data/price_withheld.json with what was found.\n"
        "\nFailing deliberately so this reaches a person.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
