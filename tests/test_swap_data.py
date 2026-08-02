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


PROVIDER_TOKENS = {
    "MSCI": ("MSCI",),
    "FTSE Russell": ("FTSE", "RUSSELL"),
    "S&P Dow Jones": ("S&P", "S AND P"),
    "Nasdaq": ("NASDAQ",),
    "Nikkei": ("NIKKEI",),
    "STOXX": ("STOXX",),
    "CRSP": ("CRSP",),
}


def test_index_provider_matches_the_official_fund_name():
    """The fund's official exchange name must not name a DIFFERENT index
    provider than the index it is mapped to.

    This guard exists because the first cut of the seed got six of these
    wrong: Vanguard's Europe, Japan and emerging-market lines track FTSE
    indices and were mapped to MSCI ones, and XDJP tracks the price-weighted
    Nikkei 225 and was mapped to MSCI Japan. Every one of those would have
    produced a confident tier-1 "exact index match" for a fund tracking a
    different index -- the exact failure this tool is supposed to prevent.
    """
    ucits = load("ucits_universe.json")
    idx = load("index_map.json")["indices"]
    bad = []
    for f in ucits["funds"]:
        nm = (f.get("official_name") or "").upper()
        if not nm:
            continue
        provider = idx[f["index_key"]]["provider"]
        expected = PROVIDER_TOKENS.get(provider)
        if not expected:          # "multiple", "LBMA" etc -- nothing to assert
            continue
        present = {p for p, toks in PROVIDER_TOKENS.items() if any(tk in nm for tk in toks)}
        if present and provider not in present:
            bad.append(f"{f['ticker']}: mapped to {provider} ({f['index_key']}) "
                       f"but official name names {sorted(present)} -- {nm!r}")
    assert not bad, "index provider contradicts official fund name:\n  " + "\n  ".join(bad)


# --------------------------------------------------------------------------
# 4. Cost and withholding: conservative when unverified
# --------------------------------------------------------------------------
def test_every_domicile_is_isin_derived(ucits):
    """The house rule. A fund-family string is an inference; an ISIN prefix is
    the fund's own registered identifier. Any line falling back to the string
    heuristic is a gap, not a result."""
    weak = [f["ticker"] for f in ucits["funds"]
            if f["wht_domicile_conf"] != "isin_prefix"]
    assert not weak, f"domicile not ISIN-derived: {weak}"


def test_treaty_rate_follows_irish_isin(ucits):
    for f in ucits["funds"]:
        if f["wht_domicile"] == "IE":
            assert f["us_div_wht_rate"] == 0.15, f["ticker"]
        else:
            assert f["us_div_wht_rate"] == 0.30, f"{f['ticker']}: {f['wht_domicile']}"



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


# --------------------------------------------------------------------------
# 7. The swap map and its refusal floor
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def swap():
    return load("swap_map.json")


def test_swap_index_keys_resolve(swap, idx):
    known = set(idx["indices"])
    for e in swap["etfs"]:
        assert e["index_key"] in known, e["ticker"]
        for a in e["alternatives"]:
            assert a["index_key"] in known, a["ticker"]


def test_every_alternative_carries_a_recommendation_decision(swap):
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            assert "recommended" in a, f"{e['ticker']}->{a['ticker']}"
            if not a["recommended"]:
                assert a.get("not_recommended_because"), (
                    f"{e['ticker']}->{a['ticker']}: set aside with no reason given")


def test_contradicted_pairs_are_never_recommended(swap):
    """A pair whose realised returns contradict its claimed index must not be
    offered. XNAS diverges from QQQ by 7.5pp a year while both are labelled
    Nasdaq-100; whatever the cause, it is not a verified swap."""
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            if a.get("verification", {}).get("contradiction"):
                assert a["recommended"] is False, f"{e['ticker']}->{a['ticker']}"


def test_failed_grades_are_never_recommended(swap):
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            if a.get("verification", {}).get("grade") in ("fail", "no_data", "insufficient"):
                assert a["recommended"] is False, f"{e['ticker']}->{a['ticker']}"


def test_tier2_matches_always_carry_a_caveat(swap):
    """An unexplained tier-2 match is the failure where someone swaps a
    total-market holding for a large-cap one and is never told."""
    for e in swap["etfs"]:
        if e["tier"] == 2:
            assert e.get("caveat"), f"{e['ticker']}: tier-2 with no caveat"


def test_tier3_states_no_equivalent_rather_than_reaching(swap):
    for e in swap["etfs"]:
        if e["tier"] == 3:
            assert e.get("verdict") == "no_close_equivalent", e["ticker"]
            assert not e["alternatives"], f"{e['ticker']}: tier-3 yet offering alternatives"


def test_gold_is_offered_only_as_unresolved(swap):
    """Gold ETCs are the only London route to bullion but are debt securities
    whose situs does not follow from UCITS status. They may be listed, never
    as a resolved-safe swap."""
    for e in swap["etfs"]:
        if e["index_key"] == "gold_spot":
            assert not e["alternatives"], f"{e['ticker']}: ETC offered as a safe match"
            for u in e["unresolved_alternatives"]:
                assert u["situs"] == "unresolved", u["ticker"]


def test_no_alternative_is_itself_us_situs(swap):
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            assert a["estate_tax_exposed"] is False, f"{e['ticker']}->{a['ticker']}"


def test_single_name_proxies_are_never_labelled_equivalent(swap):
    for n in swap["single_names"]:
        for p in n["sector_proxies"]:
            assert p["is_equivalent"] is False, f"{n['ticker']}->{p['ticker']}"


def test_verification_is_on_a_singapore_holder_basis(swap):
    """Yahoo reinvests distributions gross. Comparing raw would flatter the US
    line by roughly 30 per cent of its yield and report the UCITS lagging on
    exactly the holdings where it wins after tax."""
    meta = swap["_meta"]["return_verification"]
    assert "Singapore-holder basis" in meta["tax_basis"]
    seen = False
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            v = a.get("verification", {})
            if v.get("gap_pp") is not None:
                assert "us_holder_wht_drag_pp" in v, f"{e['ticker']}->{a['ticker']}"
                seen = True
    assert seen, "no graded pairs -- the verification pass did not run"


def test_tracking_error_is_not_used_as_the_metric(swap):
    """Regression guard. Volatility of return differences measured 6.03pp for
    SPY against VUAA purely because London closes before New York, while two
    US lines on the same index measured 0.31pp. Grading on it would fail every
    cross-venue match, which is every match this tool makes."""
    meta = swap["_meta"]["return_verification"]
    assert "annualised" in meta["metric"].lower()
    assert meta.get("why_not_tracking_error")
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            assert "te_pp" not in a.get("verification", {}), (
                f"{e['ticker']}->{a['ticker']}: tracking error reintroduced")
