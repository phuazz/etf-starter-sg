# ETF Starter · Singapore

A core-satellite ETF selection and portfolio-building tool for entry-level Singapore investors.
Public educational dashboard (deploys to `phuazz.github.io`). **Not financial advice.**

## What it does

Helps a first-time investor answer four questions about exchange-traded funds accessible from Singapore:

1. **Find** — which ETFs give broad, cheap exposure to an asset class or region.
2. **Cost & efficiency** — total expense ratio, liquidity, and the often-invisible *tax drag*
   (dividend withholding) and *estate-tax exposure* that follow from a fund's domicile.
3. **Forward return** — a synthesised long-run expected return per asset class, shown against
   history, so choices are made on forward math rather than last year's winner.
4. **Build** — assemble a diversified portfolio from risk-profile presets, see the blended
   expected return / volatility / income, and a portfolio-level US-estate-tax read-out.
5. **Learn** — two plain-language explainers: the US estate-tax trap (with an interactive
   calculator), and "Cash, SRS or CPF-OA" (facts verified Jul 2026 against MOF/CPF/MOM pages).
   Plus, on the Start tab: goal-based illustrative starter plans with a compounding illustration,
   a first-purchase walkthrough, and the post-purchase habits guide. Funds can be compared
   side-by-side (up to 4) via the compare tray on the Find tab.
5. **Learn** — a plain-language US-estate-tax explainer with an **interactive calculator**:
   enter a US-situs value and see the estimated tax (USD and approx S$), the effective rate,
   and the Irish-UCITS US$0 contrast. It is an *illustration of the published IRC §2001(c)
   schedule*, not tax advice — the graduated brackets plus the US$13,000 unified credit are
   implemented once and shared by both the rate table and the calculator, so they cannot drift.

6. **Crypto access** — which wrapper to hold listed crypto in, and what each does to the
   US-situs position. Route recommendation only; no expected return, no allocation view,
   and deliberately absent from the Build tab. See "Crypto ETF access" below.

**Navigation** — five top-level tabs (Start · Funds · Build · Swap · Learn). Costs & tax and
Expected returns are views *inside* Funds, reached by the sub-nav at the top of that panel.
The three panels remain separate `#p-` divs, so `switchTab('cost')` calls and `#tab=forward`
deep links are unchanged; only the highlighted top-level tab is derived, via `TAB_GROUP`.
Five is the count that fits a 390px phone as an equal-column grid with nothing off-screen —
the previous seven-tab bar was 802px wide in a 358px track, hiding four tabs behind a
horizontal scroll with no affordance to say they were there.

Two smaller transparency features sit alongside these:

- **Where & how to buy** — the expandable per-fund panel carries a venue-derived access note
  (London/LSE routing for the Irish-UCITS core; SGX routing, CDP/SRS/CPF for SGX funds). It
  states the general SRS/CPF rule and flags "verify current eligibility" rather than asserting
  per-fund eligibility. General information, no affiliate links, no broker endorsement.
- **Trailing-return columns** — the Find table (Detailed view) shows 1Y / 3Y p.a. / 5Y p.a.
  *price* returns, coloured by sign. The default sort stays on the forward-looking Efficiency
  Score; sorting by any trailing column raises a dismissible "last year's winner is rarely next
  year's" banner with a one-click reset. Transparency matched to StashAway, incentive inverted.

## The central idea: core-satellite, domicile-aware

- **Core (global / US developed equity, aggregate bonds, gold):** buy **Irish-domiciled UCITS**
  ETFs (CSPX, VWRA, IWDA+EIMI, AGGG, SGLN — listed in London). Zero US estate-tax exposure and
  15% (not 30%) US dividend withholding under the US-Ireland treaty.
- **Satellites (Singapore, Asia, income, thematic):** buy **SGX-listed** funds — SGD-efficient,
  locally domiciled, no US-situs issue.
- The tool flags where the mainstream SGX route is *tax-inefficient*: the SGX-listed S&P 500 (S27),
  DJIA (D07) and gold (GSD/O87) are **US-domiciled** — full US estate-tax exposure and 30% withholding.

## Data sources

- **Universe:** SGX ETF Screener export (`data/sgx_etf_screener.csv`, downloaded 2026-07-09).
  Note: the SGX export ships **empty TER and Yield columns** — TERs are supplied by a curated,
  source-flagged overlay (`data/curated.json`).
- **Dividend yields:** trailing 12-month distributions from Yahoo Finance (fetched with the price
  history, `dv12` in `prices.json`) divided by the latest close — refreshed by the weekly Action.
  Curated figures are the fallback; where a curated and a computed figure disagree by more than
  1.5pp the pipeline keeps the curated value and prints a `YIELD FLAG` for the quarterly review.
- **Domicile:** derived from the ISIN embedded in each fund's document URL (64/91 automatic);
  the remainder curated against issuer factsheets.
- **Forward returns:** `data/cma.json` — synthesised house estimates informed by published
  long-run capital market assumptions from major asset managers (2025-26 vintage). **Not** a
  reproduction of any single provider's proprietary table, and **not** a forecast.
- **Estate-tax schedule:** Financial Horse, "Die holding US Stocks and pay 40% tax?" (3 Jul 2026),
  cross-checked to IRC §2001(c). Educational; not tax advice.

## Architecture (vault dashboard convention)

```
etf-starter-sg/
├── template.html            # source (styled per C:\dev\design.md)
├── data/
│   ├── sgx_etf_screener.csv # raw SGX export (input)
│   ├── curated.json         # TER / yield / domicile overlay + UCITS core (source-flagged)
│   ├── cma.json             # asset-class forward returns, vols, correlations, tax model
│   ├── crypto_access.json   # crypto ETF access routes — UNPRICED by design, no CMA
│   ├── model_portfolios.json# risk-profile presets (buyable building blocks)
│   ├── etf_universe.json    # BUILT — enriched, de-duplicated universe (pipeline output)
│   └── prices.json          # BUILT — compact weekly close history per fund (for the on-page chart)
├── scripts/
│   └── pipeline.py          # CSV + curated + cma → etf_universe.json (+ prices) → docs/index.html
├── docs/index.html          # BUILT — GitHub Pages output (all data, incl. prices, inlined)
└── README.md
```

Build: `python scripts/pipeline.py` (fast; reuses the cached `prices.json`).
Refresh chart prices: `python scripts/pipeline.py --prices` — re-fetches ~6yr weekly closes per fund from Yahoo (slow, network) and rewrites `prices.json`. The fetch records an exact `asof` (real last-bar date) into `prices.json`; the page shows it as "week of &lt;date&gt;" so freshness is never guessed. The on-page price chart is self-rendered SVG (price line + 10/40-week moving averages, the latter hidden on windows under ~2 years) from this data, so it loads instantly. Local preview: `npx serve .` (source) or `npx serve docs` (built).

Automated refresh: `.github/workflows/refresh-prices.yml` re-runs `--prices` on weekdays (22:10 UTC, after the SGX/LSE/US closes), rebuilds `docs/index.html`, and commits the result — so the charts and the `asof` date stay current without manual runs. Yahoo's current-week weekly bar already carries the latest daily close, so the series stays uniform-weekly (no distortion to the 1M/3M/YTD/1Y/3Y stats) while the last point stays a day fresh. Switch to a weekly cadence by changing the cron to `0 2 * * 6`. The job aborts (does not commit) if fewer than 50 funds return data, so a transient fetch failure cannot wipe the charts.

## Known simplifications (the three ways this could mislead — read before trusting a number)

1. **Domicile** drives the tax verdict; ISIN-derived where possible, curated otherwise, ISIN shown
   with a "verify" flag where uncertain.
2. **Expected returns** are USD-basis long-run *estimates* mapped one-class-per-ETF; shown beside
   history; SGD-based investors face an additional FX consideration (~±1% p.a. unhedged) noted in-app.
3. **Netting** (return − TER − withholding drag) uses assumed underlying dividend yields; the
   withholding model is simplified to the dominant US-dividend case and flagged where it is not.

## Swap a US holding (2026-08-02)

Enter a US-listed ticker; get the non-US-situs alternative, priced and checked.
Built as the compute-side companion to The Enough Point's US-estate-tax article,
which names no products by design — the article explains the mechanism, the tool
carries the specifics. Deep-linkable: `#swap=SPY` lands on a pre-filled answer.

**Matching is on index identity, never correlation.** Every US large-cap ETF
correlates above 0.98 with every other one, so correlation cannot distinguish an
S&P 500 tracker from a Nasdaq-100 tracker and would return confident wrong
answers. Three tiers: exact index (37 of 48), related index with the caveat
naming what changes (7), and no equivalent (4). Realised returns then *verify*
the match; any pair the record contradicts is set aside rather than shown.

**Single names get a decomposition, not an equivalent.** UCITS diversification
rules (5/10/40, relaxed to 20/35 for index trackers) structurally forbid a
single-stock UCITS fund, so none exists to find. Instead each name is regressed
on the market plus an orthogonalised sector: median replicable share is **0.42**,
so for the typical widely held US stock most of the position is company-specific
and cannot be bought outside US situs at any price. IBM 14%, Exxon 89%.

**Gold gets an honest non-answer.** Physical gold has no UCITS fund — only ETCs,
which are debt securities whose situs does not follow from UCITS status. They are
listed as unresolved, never as a safe swap.

**A risk-scale panel** shows the liability beside the annual expected cost, using
SingStat age-specific death rates, because quoting only the headline invites fear
and quoting only the expected value invites shrugging off a swap that costs
nothing.

### Build order

```
python scripts/verify_issuer_data.py       # LSE ISINs + Vanguard OCFs  (static)
python scripts/build_ucits_universe.py     # validated non-US-situs universe
python scripts/build_us_situs_map.py       # the exposed side
python scripts/build_swap_map.py           # tiered index matching
python scripts/verify_matches.py           # realised-return check + refusal floor
python scripts/decompose_single_names.py   # variance decomposition
python scripts/fetch_mortality.py          # SingStat death rates
python scripts/pipeline.py                 # inline everything -> docs/index.html
python -m pytest tests/ -q                 # 72 guards (57 swap + 15 crypto access)
```

The UCITS universe is near-static and deliberately **not** wired into the weekly
price Action — refresh it manually at the quarterly review. `pipeline.py` slims
the swap map before inlining (repeated boilerplate moves to `_meta` once), which
keeps `docs/index.html` under 500KB; the repo file stays complete for audit.

## Corrections (2026-08-02)

Three errors found while building the domicile-swap feature, all now fixed:

1. **VWRA expense ratio 0.22 → 0.14.** The curated figure had gone stale. Vanguard
   publishes 0.14 for both All-World share classes; the distributing line's SEDOL
   (B7NLLH2) matches the exchange record exactly, confirming product identity.
2. **Luxembourg (and French) withholding 0.15 → 0.30** in `cma.json`. LU and FR UCITS
   generally do *not* obtain the US treaty rate — Limitation-on-Benefits clauses leave
   most paying the full 30%, which is a principal reason Irish domicile dominates.
   Latent rather than live: every LU fund here has zero `us_content`, so the drag
   computed to zero either way.
3. **US-domiciled funds now use `us_content` = 1.0.** A US RIC distributing to a
   non-resident alien is withheld at 30% on the *whole* distribution — no look-through,
   and Singapore has no treaty. S27 and D07 are 100% US index funds and were being
   multiplied by `dev_equity`'s 0.65, showing 0.351 drag against a true 0.54. This error
   ran **against** the dashboard's own argument: it understated the cost of the
   US-domiciled route. CSPX-versus-S27 is +0.38%/yr net, not the +0.2% previously stated.

## Crypto ETF access (2026-08-27)

Which wrapper, in which jurisdiction, and what that does to the US-situs position.
The page recommends a **route**, never an allocation — the same kind of call the site
already makes in preferring an Irish UCITS over a US-domiciled tracker.

**Two routes do not exist, and both absences are structural.** There is no SGX-listed
spot crypto ETF (verified against this repo's own screener export: zero crypto rows;
MAS has not authorised one for retail offer). There is no crypto UCITS fund either —
crypto is not an eligible asset under the UCITS Directive and a single-asset fund fails
diversification regardless, so the Irish answer that solves US situs everywhere else on
this site is unavailable here.

**The three real routes, and the verdict on each.** Hong Kong: a fund, SFC-authorised,
outside US situs — the only route that is both. United States: a Delaware statutory
trust treated as a *grantor* trust, so the law looks through to the coin and the
question becomes "where is bitcoin?", which no authority answers. Europe: an ETP/ETN,
a debt security rather than a fund — set aside for the same reason this site already
declines to call gold ETCs situs-safe. The US and European wrappers are marked
**unresolved**, never safe and never exposed.

**A third situs state.** `estate_tax_exposed` was a boolean whose negation was read
everywhere as "safe". That is sound for a fund and wrong for a look-through trust, so
every safety claim now goes through `situsState()` / `situsSafe()` in the template —
the KPI count, the route filter, the compare table, the domicile badge. Adding a caller
that tests `!estate_tax_exposed` directly is how this breaks.

**Deliberately unpriced.** No forward return, no volatility, no Efficiency Score, and
nothing in `cma.json`, the Build tab or Expected returns. No defensible long-run CMA for
bitcoin exists; inventing one to fill a column would lend it the authority of every
other figure on the site. `build_crypto_records()` therefore bypasses `enrich()`
entirely and sets each CMA field to `None` explicitly, so the exclusion is structural
rather than a convention. `check_crypto_exclusions()` fails the build if a `crypto`
class appears in `cma.json` or a model portfolio.

**Costs are not on a common basis and are not presented as though they were.** An
audited ongoing charges figure, an estimated one, a bare management fee and a unified
sponsor fee measure different things; each figure prints with its basis and names the
filing it came from. Where nothing could be verified (ChinaAMC Ether) the field is blank
rather than filled with an aggregator's number.

Two things this pass caught that the secondary sources have wrong:

1. **Harvest Bitcoin Spot ETF is not the cheap one.** Its launch fee of 0.30% rose to
   0.90% on 24 Feb 2025 and its ongoing-charges cap was removed on 30 Apr 2025; the
   published OCF is **1.72%** (KFS, 30 Apr 2026). Much of the web still calls it the
   cheapest of the three.
2. **A ticker collision, caught before it shipped.** An automated read of the Harvest
   filing reported counters "3066/3067/3068" — breaking the HK convention (3xxx HKD /
   9xxx USD / 83xxx RMB) and colliding with 3067, already the iShares Hang Seng TECH
   ETF in `curated.json`. The real codes, read from the filing, are 3439 and 9439.
   `test_no_ticker_collides_with_the_rest_of_the_universe` now pins it.

Data lives in `data/crypto_access.json`, deliberately outside `curated.json` so the
boundary is visible. Guards: `tests/test_crypto_access.py` (15 tests).

## Status

Feature-complete and **deployed** at https://phuazz.github.io/etf-starter-sg/. Latest additions
(2026-07-09): the interactive estate-tax calculator, the per-fund "Where & how to buy" note, and
the trailing-return columns with the anti-performance-chasing banner. No new curated data fields
were introduced — the buy note uses the general SRS/CPF rule with a verify flag, and the trailing
returns are computed client-side from the already-inlined `prices.json`.

_Last updated: 2026-08-27._
