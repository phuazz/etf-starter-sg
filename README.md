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
5. **Learn** — a plain-language US-estate-tax explainer with an **interactive calculator**:
   enter a US-situs value and see the estimated tax (USD and approx S$), the effective rate,
   and the Irish-UCITS US$0 contrast. It is an *illustration of the published IRC §2001(c)
   schedule*, not tax advice — the graduated brackets plus the US$13,000 unified credit are
   implemented once and shared by both the rate table and the calculator, so they cannot drift.

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
  Note: the SGX export ships **empty TER and Yield columns** — these are supplied by a curated,
  source-flagged overlay (`data/curated.json`).
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

## Status

Feature-complete and **deployed** at https://phuazz.github.io/etf-starter-sg/. Latest additions
(2026-07-09): the interactive estate-tax calculator, the per-fund "Where & how to buy" note, and
the trailing-return columns with the anti-performance-chasing banner. No new curated data fields
were introduced — the buy note uses the general SRS/CPF rule with a verify flag, and the trailing
returns are computed client-side from the already-inlined `prices.json`.

_Last updated: 2026-07-09._
