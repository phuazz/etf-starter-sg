"""Guard tests for the domicile-swap data spine.

Run: python -m pytest tests/ -q

These gate the UI. The tool's whole purpose is to tell someone whether a
holding exposes their estate to US tax, so the failure that matters is not a
crash -- it is a confident wrong answer. Each test below pins one way that
could happen.

Ranked by how bad the failure is:

  1. Telling someone a holding is SAFE when it is not.   (catastrophic, silent)
  2. Offering a "match" that changes the portfolio.      (silent)
  3. Showing a wrong cost or withholding number.         (visible, recoverable)
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def idx():
    return load("index_map.json")


@pytest.fixture(scope="module")
def ucits():
    return load("ucits_universe.json")


@pytest.fixture(scope="module")
def us():
    return load("us_situs_map.json")


@pytest.fixture(scope="module")
def seed():
    return load("ucits_seed.json")


# --------------------------------------------------------------------------
# 1. The catastrophic field: never call something safe that is not
# --------------------------------------------------------------------------
def test_nothing_is_safe_without_ucits_status(ucits):
    """situs=non_us is only reachable through verified UCITS status."""
    offenders = [f["ticker"] for f in ucits["funds"]
                 if f["situs"] == "non_us" and not f["is_ucits"]]
    assert not offenders, f"marked non-US-situs without UCITS status: {offenders}"


def test_ucits_status_is_backed_by_the_legal_name(ucits):
    """is_ucits must be evidenced by the fund's own legal name, not asserted."""
    offenders = [f["ticker"] for f in ucits["funds"]
                 if f["is_ucits"] and not re.search(r"\bUCITS\b", f["name"])]
    assert not offenders, f"is_ucits set but legal name does not say UCITS: {offenders}"


def test_etcs_are_never_presented_as_safe(ucits):
    """ETCs are debt securities, not UCITS funds. Their situs turns on the
    trust structure and this tool does not ship a verdict on it."""
    for f in ucits["funds"]:
        if f["structure"] == "etc":
            assert f["is_ucits"] is False, f"{f['ticker']}: ETC flagged as UCITS"
            assert f["situs"] == "unresolved", f"{f['ticker']}: ETC situs must stay unresolved"
            assert f["estate_tax_exposed"] is None, (
                f"{f['ticker']}: ETC must not carry a True/False exposure verdict")


def test_us_side_is_uniformly_exposed_or_explicitly_not(us):
    for e in us["etfs"]:
        assert e["situs"] == "us" and e["estate_tax_exposed"] is True, e["ticker"]
    for n in us["single_names"]:
        if n["situs"] == "us":
            assert n["estate_tax_exposed"] is True, n["ticker"]
        else:
            # foreign-incorporated: no verdict either way, verify flag instead
            assert n["situs"] == "likely_non_us", n["ticker"]
            assert n["estate_tax_exposed"] is None, (
                f"{n['ticker']}: incorporation is a screen, not a verdict")


def test_no_ucits_fund_leaked_into_the_exposed_side(us):
    bad = [e["ticker"] for e in us["etfs"] if re.search(r"\bUCITS\b", e["name"])]
    assert not bad, f"UCITS funds listed as US-situs: {bad}"


# --------------------------------------------------------------------------
# 2. Regression: Yahoo's ISIN field is unreliable for US single names
# --------------------------------------------------------------------------
def test_single_name_situs_never_rests_on_yahoo_isin(us):
    """Observed 2026-08-02: Yahoo returned Canadian ISINs for Alphabet,
    Broadcom and Walmart, and an Argentine one for Philip Morris. Acting on
    that field silently dropped four of the most widely held US stocks in the
    world. It is recorded for reference and must never drive a verdict."""
    for n in us["single_names"]:
        assert "isin" not in n, f"{n['ticker']}: raw isin key implies it is trusted"
        assert "isin_yahoo_unverified" in n
        assert "isin" not in (n.get("situs_basis") or "").lower(), (
            f"{n['ticker']}: situs_basis cites ISIN, which is not trustworthy here")


def test_widely_held_us_names_survived_validation(us):
    """The names the tool would be embarrassing to be missing."""
    have = {n["ticker"] for n in us["single_names"]}
    for t in ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "WMT", "AVGO", "PM"):
        assert t in have, f"{t} dropped from the single-name map"


# --------------------------------------------------------------------------
# 3. Match integrity: no orphans, no silent rehoming
# --------------------------------------------------------------------------
def test_every_index_key_resolves(idx, ucits, us, seed):
    known = set(idx["indices"])
    for c in seed["candidates"]:
        assert c["idx"] in known, f"seed {c['t']} -> unknown index {c['idx']}"
    for f in ucits["funds"]:
        assert f["index_key"] in known, f["ticker"]
    for e in us["etfs"]:
        assert e["index_key"] in known, e["ticker"]


def test_near_family_pairs_reference_real_families(idx):
    fams = {v["family"] for v in idx["indices"].values()}
    for p in idx["near_families"]["pairs"]:
        assert p["a"] in fams, p["a"]
        assert p["b"] in fams, p["b"]
        assert p["caveat"].strip(), f"tier-2 pair {p['a']}/{p['b']} has no caveat"


def test_sector_proxies_point_at_covered_indices(us, ucits):
    """A sector proxy promising an exposure with no validated line behind it
    is a match the tool cannot honour."""
    covered = set(ucits["by_index"])
    for n in us["single_names"]:
        k = n.get("sector_index_key")
        if k is not None:
            assert k in covered, f"{n['ticker']}: sector proxy {k} has no validated UCITS line"


def test_single_names_never_claim_an_equivalent(us):
    """UCITS diversification (5/10/40, 20/35 for index trackers) structurally
    forbids a single-stock UCITS fund. No single name may ever carry a verdict
    implying one exists."""
    ok = {"no_ucits_equivalent_possible", "may_already_be_outside_us_situs"}
    for n in us["single_names"]:
        assert n["verdict"] in ok, f"{n['ticker']}: unexpected verdict {n['verdict']!r}"
        assert n["replicable_share"] is None, (
            f"{n['ticker']}: decomposition present without a regression having been run")


# --------------------------------------------------------------------------
# 4. Cost and withholding: conservative when unverified
# --------------------------------------------------------------------------
def test_unverified_domicile_never_gets_the_treaty_rate(ucits):
    """15 per cent is the Irish treaty rate. Handing it to a line we did not
    verify overstates the swap's benefit -- the direction that misleads
    someone into acting."""
    for f in ucits["funds"]:
        if f["wht_domicile_conf"] == "none":
            assert f["wht_domicile"] == "unverified", f["ticker"]
            assert f["us_div_wht_rate"] == 0.30, (
                f"{f['ticker']}: unverified domicile given {f['us_div_wht_rate']}")


def test_treaty_rate_only_for_ireland(ucits):
    for f in ucits["funds"]:
        if f["us_div_wht_rate"] == 0.15:
            assert f["wht_domicile"] == "IE", f["ticker"]
            assert f["wht_domicile_conf"] != "none", f["ticker"]


def test_us_side_carries_the_full_statutory_rate(us):
    """Singapore has no US tax treaty, so a Singapore resident holding a
    US-domiciled fund suffers the full 30 per cent on US dividends."""
    for e in us["etfs"]:
        assert e["us_div_wht_rate"] == 0.30, e["ticker"]


def test_ter_is_absent_rather_than_guessed(ucits):
    """A TER may be missing. It may not be invented -- a wrong one silently
    corrupts every cost comparison the tool makes."""
    for f in ucits["funds"]:
        if f["ter"] is None:
            assert f["ter_src"] is None, f"{f['ticker']}: null TER with a source attached"
        else:
            assert f["ter_src"], f"{f['ticker']}: TER {f['ter']} with no source"
            assert 0 <= f["ter"] <= 2.0, f"{f['ticker']}: implausible TER {f['ter']}"


# --------------------------------------------------------------------------
# 5. The currency trap
# --------------------------------------------------------------------------
def test_currency_recorded_and_pence_flagged(ucits):
    """Comparing a GBp line against a USD line on raw price returns produces
    pure FX noise that reads as tracking failure. The match engine cannot
    avoid that unless the basis is recorded per line."""
    for f in ucits["funds"]:
        assert f["ccy"], f"{f['ticker']}: no quote currency recorded"
        assert f["ccy_is_pence"] == (f["ccy"] == "GBp"), f["ticker"]


def test_pence_lines_actually_exist_so_the_guard_is_live(ucits):
    """If this ever hits zero the guard above has gone untested -- check the
    universe rather than deleting the test."""
    assert sum(1 for f in ucits["funds"] if f["ccy_is_pence"]) > 0


# --------------------------------------------------------------------------
# 6. Provenance
# --------------------------------------------------------------------------
def test_built_artefacts_are_dated_and_attributed(ucits, us):
    for d in (ucits, us):
        assert d["_meta"]["built"]
        assert d["_meta"]["builder"]


def test_distribution_policy_recorded_on_every_line(ucits):
    """Needed before any tracking-error computation: comparing a distributing
    line against an accumulating one on price returns produces a spurious
    drift exactly equal to the dividend yield."""
    for f in ucits["funds"]:
        assert f["income"] in ("Accumulating", "Distributing"), f["ticker"]
