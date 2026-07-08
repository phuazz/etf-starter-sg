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
│   └── etf_universe.json    # BUILT — enriched, de-duplicated universe (pipeline output)
├── scripts/
│   └── pipeline.py          # CSV + curated + cma → etf_universe.json → docs/index.html
├── docs/index.html          # BUILT — GitHub Pages output
└── README.md
```

Build: `python scripts/pipeline.py`. Local preview: `npx serve .` (source) or `npx serve docs` (built).

## Status

- **Session 1 (in progress):** data layer — pipeline, domicile derivation, CMA + model portfolios.
- Sessions 2-4: Find/Cost tabs → Forward-return/Build tabs → Learn explainer + deploy + ledger.

## Known simplifications (the three ways this could mislead — read before trusting a number)

1. **Domicile** drives the tax verdict; ISIN-derived where possible, curated otherwise, ISIN shown
   with a "verify" flag where uncertain.
2. **Expected returns** are USD-basis long-run *estimates* mapped one-class-per-ETF; shown beside
   history; SGD-based investors face an additional FX consideration (~±1% p.a. unhedged) noted in-app.
3. **Netting** (return − TER − withholding drag) uses assumed underlying dividend yields; the
   withholding model is simplified to the dominant US-dividend case and flagged where it is not.

_Last updated: 2026-07-09._
