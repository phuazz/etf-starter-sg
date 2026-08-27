"""Guard tests for the crypto access route.

Run: python -m pytest tests/ -q

The crypto rows are unusual on this site in two ways, and both are load-bearing:

  1. They are UNPRICED. Every other fund carries a forward return, an expected
     volatility and an Efficiency Score derived from cma.json. Crypto has none,
     because no defensible long-run capital market assumption for it exists.
     The failure mode is not a crash — it is someone adding a "crypto" entry to
     cma.json to stop a lookup returning undefined, at which point an invented
     number silently acquires the authority of every other figure on the page.

  2. Their US-situs answer is a THIRD state. The rest of the site treats
     estate_tax_exposed as a boolean whose negation means "safe". A US-listed
     spot bitcoin trust is neither: the issuer's own 10-K declines to say
     whether the shares or the bitcoin behind them are US-situs. Collapsing
     that back to a boolean prints a green "Safe" against an instrument nobody
     has cleared, which is the single worst thing this site could do.

Ranked by how bad the failure is:

  1. Claiming a crypto wrapper is estate-tax safe.        (catastrophic, silent)
  2. Attaching a forward return or score to crypto.       (silent, corrosive)
  3. Comparing costs measured on different bases.         (visible, misleading)
  4. A cost or ticker with no stated provenance.          (visible, recoverable)
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def crypto():
    return load("crypto_access.json")


@pytest.fixture(scope="module")
def universe():
    return load("etf_universe.json")


@pytest.fixture(scope="module")
def cma():
    return load("cma.json")


@pytest.fixture(scope="module")
def crypto_funds(universe):
    rows = [f for f in universe["funds"] if f.get("is_crypto")]
    assert rows, "no crypto rows in the built universe — did the pipeline skip them?"
    return rows


# ---- 1. the safety claim -------------------------------------------------

def test_no_crypto_row_is_ever_estate_tax_exposed(crypto_funds):
    """'Exposed' is a positive claim, and this page cannot make it.

    The US trusts are unresolved, not confirmed exposed. Marking one exposed
    would also drag in the red "+30% withholding" note, which is wrong twice
    over: none of these products distributes anything to withhold on.
    """
    for f in crypto_funds:
        assert f["estate_tax_exposed"] is False, f"{f['ticker']} claims estate-tax exposure"


def test_us_and_eu_wrappers_are_marked_situs_unresolved(crypto_funds):
    """The two wrappers whose situs nobody has settled must say so.

    A US grantor trust is looked through to the coin, and no authority states
    where a coin sits. A European ETP is a debt security, and this site already
    refuses to call gold ETCs situs-safe for exactly that reason.
    """
    for f in crypto_funds:
        if f["crypto_route"] in ("us", "eu"):
            assert f["situs_unresolved"] is True, (
                f"{f['ticker']} ({f['crypto_route']}) is not flagged situs-unresolved")


def test_hk_wrappers_are_funds_and_outside_us_situs(crypto_funds):
    """The recommended route has to actually be the clean one.

    If a HK row ever came back unresolved the route recommendation on the Learn
    tab would be contradicted by its own data.
    """
    hk = [f for f in crypto_funds if f["crypto_route"] == "hk"]
    assert hk, "no HK crypto rows — the recommended route vanished"
    for f in hk:
        assert f["situs_unresolved"] is False, f"{f['ticker']} HK row marked unresolved"
        assert f["domicile"] == "HK", f"{f['ticker']} HK row is domiciled {f['domicile']}"


def test_no_crypto_row_counts_toward_estate_tax_safe(crypto_funds):
    """Mirrors the situsSafe() predicate in the template.

    The KPI on the Funds tab counts "estate-tax safe" funds. Before the third
    state existed that count was !estate_tax_exposed, which would have silently
    absorbed every unresolved wrapper into the safe tally.
    """
    for f in crypto_funds:
        safe = not f["estate_tax_exposed"] and not f["situs_unresolved"]
        if f["crypto_route"] in ("us", "eu"):
            assert not safe, f"{f['ticker']} would be counted estate-tax safe"


# ---- 2. the unpriced boundary --------------------------------------------

def test_crypto_is_absent_from_cma(cma):
    """The structural guarantee behind every blank column.

    Build and Expected returns both iterate cma.asset_classes, so crypto's
    absence there — not a filter in the UI — is what keeps it out of both.
    """
    assert "crypto" not in cma["asset_classes"], (
        "cma.json now carries a 'crypto' class: the Build tab and Expected returns "
        "would pick it up, and the number behind it cannot be defended")


def test_crypto_is_absent_from_model_portfolios():
    mp = load("model_portfolios.json")
    for name, prof in mp["profiles"].items():
        assert "crypto" not in prof["weights"], f"profile '{name}' allocates to crypto"


def test_crypto_rows_carry_no_forward_return_or_score(crypto_funds):
    """A blank must stay blank. Each of these fields, if populated, shows on the
    page as a figure with the same visual authority as a researched one."""
    blank = ("gross_expected_return_pct", "net_expected_return_pct",
             "cost_drag_total_pct", "est_wht_drag_pct", "exp_vol",
             "starter_score", "score_parts")
    for f in crypto_funds:
        for k in blank:
            assert f.get(k) is None, f"{f['ticker']} has {k}={f[k]!r}, expected None"
        assert f.get("no_forward_return") is True


def test_crypto_rows_are_not_flagged_core(crypto_funds):
    """is_core drives the "Recommended core building blocks" strip on the Funds
    tab. Nothing here is a core building block."""
    for f in crypto_funds:
        assert f["is_core"] is False, f"{f['ticker']} is flagged as a core holding"


# ---- 3. like-for-like ----------------------------------------------------

def test_every_cost_states_its_basis(crypto):
    """A management fee, an estimated ongoing charges figure and an audited one
    are three different measurements. Printing them in one column without the
    basis invites a comparison that has not been made — this set spans 0.15% to
    1.72% on four different bases."""
    for p in crypto["products"]:
        if p.get("cost") is not None:
            assert p.get("cost_basis"), f"{p['ticker']} has a cost with no stated basis"


def test_cost_figures_carry_a_confidence_and_a_source(crypto):
    for p in crypto["products"]:
        assert p.get("src"), f"{p['ticker']} has no provenance note"
        if p.get("cost") is not None:
            assert p.get("ter_conf") in ("high", "med"), (
                f"{p['ticker']} cost carries confidence {p.get('ter_conf')!r}")


def test_unverified_costs_are_blank_rather_than_guessed(crypto):
    """Where no issuer figure could be reached, the field is null and the src
    says why. A plausible aggregator number would rank in the cost column
    against figures read from filings."""
    for p in crypto["products"]:
        if p.get("cost") is None:
            assert p.get("cost_basis") is None and p.get("ter_conf") is None, (
                f"{p['ticker']} has no cost but carries a basis or confidence")


# ---- 4. identity ---------------------------------------------------------

def test_no_ticker_collides_with_the_rest_of_the_universe(universe):
    """A real near-miss, not a hypothetical.

    A mis-extracted filing reported the Harvest fund's counters as 3066/3067/3068.
    3067 is already the iShares Hang Seng TECH ETF in curated.json, and two funds
    under one ticker would have shown one fund's cost beside the other's name.
    The true codes, read from the filing, are 3439 and 9439.
    """
    seen = {}
    for f in universe["funds"]:
        assert f["ticker"] not in seen, (
            f"ticker {f['ticker']} used by both {seen.get(f['ticker'])} and {f['name']}")
        seen[f["ticker"]] = f["name"]


def test_hk_counters_follow_the_exchange_convention(crypto):
    """3xxx HKD / 9xxx USD / 83xxx RMB over one fund. The convention is what
    exposed the bad extraction above, so it is worth pinning."""
    for p in crypto["products"]:
        if p["route"] != "hk":
            continue
        assert p["ticker"].startswith("3"), f"{p['ticker']} is not a 3xxx HKD counter"
        if p.get("usd_counter"):
            assert p["usd_counter"] == "9" + p["ticker"][1:], (
                f"{p['ticker']}: USD counter {p['usd_counter']} breaks the 9xxx convention")
        if p.get("rmb_counter"):
            assert p["rmb_counter"] == "8" + p["ticker"], (
                f"{p['ticker']}: RMB counter {p['rmb_counter']} breaks the 83xxx convention")


def test_us_listed_rows_carry_an_explicit_yahoo_symbol(crypto):
    """Without one the pipeline's venue rules fall through to '.SI' and quietly
    fetch a Singapore listing that does not exist, dropping the fund with a
    'no data' line rather than an error."""
    for p in crypto["products"]:
        if p["route"] in ("us", "eu"):
            assert p.get("yahoo"), f"{p['ticker']} has no explicit yahoo symbol"


def test_learn_tab_cost_range_matches_the_data(crypto):
    """Pin the prose to the numbers it describes.

    The Learn tab quotes a headline range for the Hong Kong route in two places.
    It was written as 0.85-1.72% and went stale the moment a ChinaAMC ongoing
    charges figure of 1.99% was read from the filing -- the page would have kept
    asserting a ceiling nearly a third below the real one, in prose no build step
    looks at. Only ongoing-charges figures count toward the range: a bare
    management fee is a different measurement and must not set either bound.
    """
    ocf = [p["cost"] for p in crypto["products"]
           if p["route"] == "hk" and p.get("cost") is not None
           and (p.get("cost_basis") or "").startswith("ongoing charges")]
    assert ocf, "no HK ongoing-charges figures to build a range from"
    lo, hi = f"{min(ocf):.2f}", f"{max(ocf):.2f}"

    tpl = os.path.join(ROOT, "template.html")
    with open(tpl, encoding="utf-8") as fh:
        html = fh.read()

    for phrase in (f"{lo}&ndash;{hi}%", f"{lo}% to {hi}% a year"):
        assert phrase in html, (
            f"template.html does not carry the current HK cost range: expected "
            f"{phrase!r}. The published ongoing-charges figures now span "
            f"{lo}%-{hi}%; update the Learn tab prose to match.")


def test_no_product_is_called_the_most_expensive_in_prose(crypto):
    """A superlative is a claim about the whole set, so it breaks whenever the set
    changes. Harvest was described as the most expensive HK crypto ETF here; the
    ChinaAMC funds then came in above it. Rank claims belong in the rendered
    table, which sorts itself, not in prose that cannot."""
    for p in crypto["products"]:
        w = (p.get("stale_source_warning") or "").lower()
        assert "it is the most expensive" not in w, (
            f"{p['ticker']} carries an unqualified 'most expensive' claim")


def test_as_at_date_is_present(crypto):
    """Every figure here is a point-in-time reading of a document that gets
    reissued. Fees on this route have already moved once by 3x."""
    assert crypto["_meta"].get("as_at"), "crypto_access.json has no as-at date"
