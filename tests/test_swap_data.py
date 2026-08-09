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


def test_index_provider_matches_the_us_fund_name():
    """The same check, on the other side of every pair.

    It only ever ran on the UCITS side. Running it on the US side found ITOT --
    iShares Core S&P Total U.S. Stock Market, mapped to MSCI USA IMI -- which
    had been sitting there through every previous pass. A guard that covers one
    side of a comparison is half a guard: the pair is only as sound as the
    weaker attribution, and this tool's whole claim is that the two sides track
    the same index.
    """
    us = load("us_situs_map.json")
    idx = load("index_map.json")["indices"]
    bad = []
    for e in us["etfs"]:
        nm = (e.get("name") or "").upper()
        if not nm:
            continue
        provider = idx[e["index_key"]]["provider"]
        if not PROVIDER_TOKENS.get(provider):
            continue
        present = {p for p, toks in PROVIDER_TOKENS.items() if any(tk in nm for tk in toks)}
        if present and provider not in present:
            bad.append(f"{e['ticker']}: mapped to {provider} ({e['index_key']}) "
                       f"but its name names {sorted(present)} -- {nm!r}")
    assert not bad, "US-side index provider contradicts fund name:\n  " + "\n  ".join(bad)


# A fund's name states the universe it covers. Two indices from the same
# provider, measuring the same factor over different universes, are not the
# same index -- and the provider check cannot tell them apart.
SCOPE_TOKENS = {
    "usa": ("USA", "U.S.", " US ", "US "),
    "world": ("WORLD",),
    "emerging": ("EMERGING", "EM "),
    "eafe": ("EAFE",),
}


def _scope(text):
    t = (text or "").upper()
    return {s for s, toks in SCOPE_TOKENS.items() if any(k in t for k in toks)}


INDEX_STOPWORDS = {
    "ETF", "FUND", "INDEX", "SHARES", "TRUST", "CORE", "THE", "OF", "AND",
    "US", "USA", "U", "S", "STOCK", "MARKET", "TOTAL", "SELECT", "SECTOR",
    "VANGUARD", "ISHARES", "SPDR", "INVESCO", "SCHWAB", "PROSHARES", "VANECK",
    "STATE", "STREET", "PORTFOLIO", "BOND", "YEAR", "IV", "INC",
}

# The count of US mappings resting on nothing but assertion. It may fall. It may
# NOT rise: a new holding arrives unverified, and this makes someone say so out
# loud rather than adding it quietly to a pile nobody is counting.
#
# It started at 22 and every one has since been checked against the issuer, so
# it is zero. That is not a state to defend at any cost -- adding a holding and
# verifying it later is fine -- but the ceiling has to be raised deliberately,
# in a commit that says which mapping is unverified and why.
MAX_UNVERIFIED_INDEX_MAPPINGS = 0


def _idx_tokens(text):
    up = (text or "").upper().replace("&", "AND")
    return {w for w in re.split(r"[^A-Z0-9]+", up) if w and w not in INDEX_STOPWORDS}


def test_every_index_mapping_records_where_it_came_from():
    """Four of these mappings were wrong and none of them said what it rested on.

    ITOT, MTUM, QUAL and VLUE each named a different index than they track. VWO
    sat on MSCI for thirteen years after moving to FTSE. VTI still said CRSP
    after Vanguard moved it to Morningstar and renamed the fund. Three surfaced
    through realised returns rather than any check, and the fourth only because
    someone asked for it to be verified.

    What they had in common is that nothing recorded their provenance, so a
    mapping verified against a factsheet and one typed from memory were
    indistinguishable. This does not require every mapping to be verified --
    most are not -- only that each states which it is.
    """
    us = load("us_situs_map.json")
    missing = [e["ticker"] for e in us["etfs"] if not (e.get("index_src") or "").strip()]
    assert not missing, f"index mapping with no recorded source: {missing}"


def test_a_fund_name_attribution_is_actually_evidenced_by_the_name():
    """"fund name" is a claim about the name, so it is re-checkable -- and it is
    re-checked here rather than trusted, because the builder that writes it and
    the label it points at can drift apart independently."""
    us = load("us_situs_map.json")
    idx = load("index_map.json")["indices"]
    bad = []
    for e in us["etfs"]:
        if e.get("index_src") != "fund name":
            continue
        want = _idx_tokens(idx[e["index_key"]]["label"])
        if not want or not want <= _idx_tokens(e.get("name")):
            bad.append(f"{e['ticker']}: claims the name evidences {e['index_key']} but "
                       f"{sorted(want - _idx_tokens(e.get('name')))} is absent from {e.get('name')!r}")
    assert not bad, "attribution claims the name and the name does not say it:\n  " + "\n  ".join(bad)


def test_unverified_index_mappings_do_not_multiply():
    """A ratchet, not a target. Lowering it means verifying one against the
    issuer and editing the number down; it must never be raised to make a new
    holding fit."""
    us = load("us_situs_map.json")
    unver = sorted(e["ticker"] for e in us["etfs"] if e.get("index_src") == "unverified")
    assert len(unver) <= MAX_UNVERIFIED_INDEX_MAPPINGS, (
        f"{len(unver)} unverified index mappings, ceiling is "
        f"{MAX_UNVERIFIED_INDEX_MAPPINGS}: {unver}. Verify the new one against the "
        f"issuer rather than raising the ceiling.")


def test_index_scope_matches_the_fund_name():
    """MSCI USA Momentum is not MSCI World Momentum.

    MTUM, QUAL and VLUE are iShares MSCI *USA* factor funds and were each mapped
    to the *World* index of the same factor, then badged tier-1 "EXACT INDEX"
    against a fund holding roughly 30 per cent non-US. Two were recommended.
    Same provider throughout, so the provider guard passed them; nothing looked
    at the universe. A five-year window averaged the divergence below the
    failure floor, and a three-year one did not -- which is how they surfaced.

    Only fires when the name and the label BOTH state a scope and the two
    disagree, so a fund whose name is silent about its universe is not guessed
    at. VWO is exactly that case: its stored name says only "Emerging Markets",
    which is true of both the FTSE and MSCI series, so this guard cannot see it
    and the realised-return evidence had to.
    """
    idx = load("index_map.json")["indices"]
    bad = []
    for fname, key in (("us_situs_map.json", "etfs"), ("ucits_universe.json", "funds")):
        d = load(fname)
        for f in d[key]:
            for field in ("name", "official_name"):
                s_name = _scope(f.get(field))
                s_index = _scope(idx[f["index_key"]]["label"])
                if s_name and s_index and not (s_name & s_index):
                    bad.append(f"{f['ticker']} ({fname}): {field} implies {sorted(s_name)} "
                               f"but index {f['index_key']} is {sorted(s_index)}")
                    break
    assert not bad, "index universe contradicts fund name:\n  " + "\n  ".join(bad)


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


def test_income_policy_is_backed_by_the_legal_name(ucits):
    """A hand-asserted distribution flag may not contradict the fund's own name.

    ucits_seed.json asserts one per line, and seven of those assertions were
    wrong: VAGP, IMEU, IDP6, IUAG, SUAG, IBTM and SHYU were all recorded
    Accumulating while their registered names say (Dist) or Income. The tool
    printed "Acc" against each of them.

    This is the same failure as test_single_name_situs_never_rests_on_yahoo_isin
    in a different field -- an asserted value allowed to outrank the security's
    own identity -- and the same remedy as
    test_ucits_status_is_backed_by_the_legal_name: where the name states a
    policy, the name decides.
    """
    bad = []
    for f in ucits["funds"]:
        nm = f"{f.get('name') or ''} {f.get('official_name') or ''}".upper()
        says_dist = re.search(r"\((?:DIST|INC)\)|\bDISTRIBUTING\b|\bINCOME\b|\bDIST\b", nm)
        says_acc = re.search(r"\bACC(?:UMULATING|UMULATION)?\b|\(ACC\)", nm)
        if says_dist and f["income"] != "Distributing":
            bad.append(f"{f['ticker']}: recorded {f['income']} but name says distributing -- {nm.strip()!r}")
        elif says_acc and not says_dist and f["income"] != "Accumulating":
            bad.append(f"{f['ticker']}: recorded {f['income']} but name says accumulating -- {nm.strip()!r}")
    assert not bad, "income policy contradicts the registered name:\n  " + "\n  ".join(bad)


def test_currency_hedging_is_recorded_and_name_backed(ucits):
    """A hedged share class is a different instrument, not a cheaper wrapper.

    Nothing recorded hedging at all until VAGP -- Vanguard Global Aggregate GBP
    Hedged -- was offered against BNDW and read as a 3.23pp tracking failure.
    It was sterling: +4.37% a year in its native currency plus 2.22% of GBP/USD
    strength. Its USD-hedged sibling VAGU sits 0.06pp from BNDW.

    Recorded from the fund's own name, never asserted, and every line that says
    "hedged" must yield a currency -- a hedge whose currency did not parse is a
    gap, not a fund without a hedge.
    """
    for f in ucits["funds"]:
        nm = f"{f.get('name') or ''} {f.get('official_name') or ''}".upper()
        if "HEDG" in nm:
            assert f.get("hedge_ccy"), f"{f['ticker']}: name says hedged, no hedge_ccy recorded"
            assert f["hedge_ccy"] in nm, f"{f['ticker']}: hedge_ccy {f['hedge_ccy']} not in its own name"
            assert f.get("hedge_src") == "legal name", f["ticker"]
        else:
            assert not f.get("hedge_ccy"), (
                f"{f['ticker']}: hedge_ccy {f.get('hedge_ccy')} asserted but the name does not say hedged")


def test_no_cross_currency_hedge_is_ever_recommended(swap):
    """Every figure in this tool is on a USD basis. A share class hedged to
    something else cannot be compared on that basis at all -- converting its
    price into USD re-adds the exposure it exists to remove -- so it may be
    listed and explained, never offered."""
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            h = a.get("hedge_ccy")
            if h and h != "USD":
                assert a["recommended"] is False, f"{e['ticker']}->{a['ticker']}: {h}-hedged and offered"
                assert a.get("not_recommended_because"), f"{e['ticker']}->{a['ticker']}"
                assert h in a["not_recommended_because"], (
                    f"{e['ticker']}->{a['ticker']}: set aside without naming the hedge currency")


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
    """Evidence AGAINST a match demotes it. Absence of evidence does not --
    see the next test."""
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            if a.get("verification", {}).get("grade") in ("fail", "no_data"):
                assert a["recommended"] is False, f"{e['ticker']}->{a['ticker']}"


def test_price_break_pairs_are_never_recommended(swap):
    """A stepped price series is not a graded pair.

    Where the record steps and holds -- XNAS +21.6% in a week in January 2023,
    SEMI -20.8% in January 2024, which is what an unadjusted 5:4 split looks
    like -- there are two records, and any gap taken across the join measures
    the join. The pair is listed with the step named and never offered.

    The reason must carry the date, because "not verifiable" on its own invites
    the reader to assume the fund is at fault. It is not: XNAS grades A on the
    side of its break that is intact.
    """
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            v = a.get("verification", {})
            if v.get("grade") != "break" and not v.get("price_break"):
                continue
            tag = f"{e['ticker']}->{a['ticker']}"
            assert v.get("grade") == "break", f"{tag}: price_break without the break grade"
            assert v.get("gap_pp") is None, f"{tag}: a gap was measured across a break"
            b = v.get("price_break") or {}
            assert b.get("date") and b.get("shift_pct"), f"{tag}: break with no date or size"
            assert a["recommended"] is False, f"{tag}: stepped series offered as a swap"
            assert b["date"] in (a.get("not_recommended_because") or ""), (
                f"{tag}: set aside without naming when the series breaks")


# Grades that MAY be offered. Anything else is refused by default.
RECOMMENDABLE_GRADES = {"A", "B", "C", "insufficient"}


def test_only_known_good_grades_can_be_recommended(swap):
    """The backstop, and the reason this test exists rather than another
    grade-specific one.

    Every refusal grade needed its own guard, and each was written after the
    grade was: `break` and `not_comparable` were both added to the refusal floor
    and shipped before anything stopped a later edit from recommending them.
    That ordering is the bug. This inverts the default -- a grade may be offered
    only if it appears above -- so the next grade added is refused until someone
    deliberately says otherwise, instead of being offered until someone notices.
    """
    seen = set()
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            g = a.get("verification", {}).get("grade")
            seen.add(g)
            if a["recommended"]:
                assert g in RECOMMENDABLE_GRADES, (
                    f"{e['ticker']}->{a['ticker']}: grade {g!r} is offered but is not on "
                    f"the recommendable list. If that is intended, add it there and say "
                    f"why; do not widen it to make this pass.")
    unknown = seen - RECOMMENDABLE_GRADES - {"fail", "no_data", "break", "not_comparable"}
    assert not unknown, f"grades nothing in this file has an opinion about: {sorted(unknown)}"


def test_unverifiable_is_shown_but_labelled_not_hidden(swap):
    """A fund listed months ago cannot have a multi-year record. Demoting it
    for that would have buried the Singapore-domiciled gold fund, which is the
    cleanest situs answer available for bullion -- neither a US vehicle nor a
    debt security. It is shown, with the gap in evidence named."""
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            if a.get("verification", {}).get("grade") == "insufficient":
                assert a["recommended"] is True, f"{e['ticker']}->{a['ticker']}"
                assert a.get("verification_pending"), (
                    f"{e['ticker']}->{a['ticker']}: unverified but not labelled")
                assert "not yet knowable" in a["verification_pending"]


def test_sgx_alternatives_are_non_us_and_carry_their_venue(swap):
    """SGX lines enter the pool on fund domicile, not UCITS status. The venue
    must travel with them: it drives the Yahoo suffix, and it is the reason
    they can be SRS-eligible when no London-listed UCITS is."""
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            assert a.get("venue") in ("LSE", "SGX"), f"{a['ticker']}: venue {a.get('venue')!r}"
            assert a["estate_tax_exposed"] is False, a["ticker"]
            if a["venue"] == "SGX":
                assert a["domicile"] != "US", f"{a['ticker']}: US-domiciled offered as the fix"
                assert a.get("venue_note"), f"{a['ticker']}: SGX line with no explanation"


def test_capped_variant_sectors_are_never_tier_one():
    """A sector whose UCITS counterparts track capped variants is not an exact
    index match.

    The US Select Sector indices cap 25/50; the UCITS lines cap 35/20 -- iShares
    as "S&P 500 Capped 35/20 <Sector>", SPDR as "S&P <Sector> Select Sector
    Daily Capped 35/20", both verified against the issuers, and both changed
    from 25/20 on 24 March 2025 inside the graded window. Ten sector keys were
    badged EXACT INDEX against funds that cannot legally hold the same weights.
    """
    swap = load("swap_map.json")
    idx = load("index_map.json")["indices"]
    bad = [e["ticker"] for e in swap["etfs"]
           if e["tier"] == 1 and idx[e["index_key"]].get("ucits_tracks_capped_variant")]
    assert not bad, f"claimed an exact index match on a capped-variant sector: {bad}"


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


def test_gold_etcs_are_never_offered_as_a_safe_swap(swap):
    """Gold ETCs are debt securities issued by a special-purpose vehicle, and
    their situs does not follow from UCITS status. They may be listed for
    completeness, never as a resolved-safe swap.

    This is narrower than it first looks. Gold DOES now have a real answer --
    the Singapore-domiciled physical gold fund, which is a fund rather than a
    debt security and so carries no unresolved situs. The invariant is about
    STRUCTURE, not about the asset: anything offered as a swap must have
    resolved non-US situs, and every ETC must stay on the unresolved list."""
    etc_tickers = set()
    for e in swap["etfs"]:
        for u in e.get("unresolved_alternatives", []):
            etc_tickers.add(u["ticker"])
            assert u["situs"] == "unresolved", u["ticker"]
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            assert a["ticker"] not in etc_tickers, (
                f"{e['ticker']}->{a['ticker']}: a security listed elsewhere as "
                f"situs-unresolved is being offered as a swap")
            assert a["estate_tax_exposed"] is False, a["ticker"]


def test_gold_has_a_fund_answer_not_only_etcs(swap):
    """Regression on the reason the SGX pool was added: bullion previously
    returned tier-3 'no equivalent' while a Singapore-domiciled physical gold
    FUND existed in the repo's own universe and was never considered."""
    gold = [e for e in swap["etfs"] if e["index_key"] == "gold_spot"]
    assert gold, "no gold holdings in the map"
    for e in gold:
        offered = [a for a in e["alternatives"] if a["recommended"]]
        assert offered, f"{e['ticker']}: no fund alternative offered for bullion"
        for a in offered:
            assert a["domicile"] != "US", a["ticker"]


def test_no_alternative_or_proxy_shares_a_ticker_with_a_us_situs_holding(swap):
    """A ticker that means two things cannot be an instruction.

    VanEck's semiconductor fund lists in London as both SMGB and SMH, and SMH is
    also the US-domiciled semiconductor ETF this tool moves people out of. The
    build offered "swap SMH for SMH" as a RECOMMENDED answer and put SMH on
    seven semiconductor names as a non-US-situs proxy. A reader who types that
    into a US broker buys the exposed fund -- the precise outcome the tool
    exists to prevent, reached by following its own advice.
    """
    us_tickers = {e["ticker"] for e in swap["etfs"]} | {n["ticker"] for n in swap["single_names"]}
    bad = []
    for e in swap["etfs"]:
        for a in e["alternatives"]:
            if a["ticker"] in us_tickers:
                bad.append(f"{e['ticker']} -> {a['ticker']}: offered a ticker that is also a US-situs holding")
    for n in swap["single_names"]:
        for p in n.get("sector_proxies", []):
            if p["ticker"] in us_tickers:
                bad.append(f"{n['ticker']} proxy {p['ticker']}: also a US-situs holding")
    assert not bad, "ambiguous ticker offered as the fix:\n  " + "\n  ".join(bad)


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


# --------------------------------------------------------------------------
# 8. Single-name decomposition
# --------------------------------------------------------------------------
def test_decomposition_shares_are_valid_and_complementary(swap):
    for n in swap["single_names"]:
        d = n.get("decomposition")
        assert d is not None, f"{n['ticker']}: no decomposition recorded"
        r = d.get("replicable_share")
        if r is None:
            assert d.get("basis"), f"{n['ticker']}: null share with no reason"
            continue
        assert 0.0 <= r <= 1.0, f"{n['ticker']}: share {r} out of range"
        assert abs((r + d["idiosyncratic_share"]) - 1.0) < 1e-6, n["ticker"]
        assert n["replicable_share"] == r, f"{n['ticker']}: top-level share out of sync"


def test_decomposition_never_upgrades_a_proxy_to_an_equivalent(swap):
    """A high replicable share is not permission to call a sector fund a
    substitute. The concentration risk of a single name IS the residual."""
    for n in swap["single_names"]:
        for p in n["sector_proxies"]:
            assert p["is_equivalent"] is False, n["ticker"]
        assert n["verdict"] in ("no_ucits_equivalent_possible",
                                "may_already_be_outside_us_situs"), n["ticker"]


def test_decomposition_states_the_unbuyable_residual(swap):
    for n in swap["single_names"]:
        if n.get("replicable_share") is not None:
            assert n.get("verdict_detail"), f"{n['ticker']}: share without an explanation"
            assert "cannot be bought" in n["verdict_detail"], n["ticker"]


def test_decomposition_used_same_venue_proxies(swap):
    """Cross-venue regressors would import the close-time artefact that
    measured 6.03pp on a pair that genuinely tracks."""
    meta = swap["_meta"]["single_name_decomposition"]
    assert meta["why_us_listed_proxies"]
    assert "hedge ratio" in meta["caveat"]
    for n in swap["single_names"]:
        d = n.get("decomposition") or {}
        if d.get("market_proxy"):
            assert not d["market_proxy"].endswith(".L"), n["ticker"]
        if d.get("sector_proxy_used"):
            assert not d["sector_proxy_used"].endswith(".L"), n["ticker"]


# --------------------------------------------------------------------------
# 9. Mortality data for the risk-scale panel
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mort():
    return load("mortality_sg.json")


def test_mortality_is_sourced_and_dated(mort):
    m = mort["_meta"]
    assert "SingStat" in m["source"]
    assert m["year"] and m["units"]
    assert m["what_this_is_not"], "the limits of this figure must travel with it"
    assert m["how_to_present"], "presentation rule must travel with the data"


def test_mortality_rates_are_plausible_and_rise_with_age(mort):
    for sex, bands in mort["rates"].items():
        keys = sorted((int(k) for k in bands), key=int)
        assert keys, sex
        for k in keys:
            r = bands[str(k)]["rate_per_1000"]
            assert 0 <= r < 500, f"{sex} age {k}: implausible rate {r}"
        # from age 30 upward mortality should be non-decreasing across bands
        adult = [bands[str(k)]["rate_per_1000"] for k in keys if k >= 30]
        assert adult == sorted(adult), f"{sex}: adult rates not monotonic: {adult}"


def test_mortality_bands_are_disjoint(mort):
    """The source also publishes cumulative '70 Years & Over' rows. Mixing
    those with the five-year bands would double-count."""
    for sex, bands in mort["rates"].items():
        for k, v in bands.items():
            assert " - " in v["band"], f"{sex}: cumulative band leaked in: {v['band']}"
