# Quarterly data-integrity review — 2026-10-01 (Thursday)

Scheduled task `etf-starter-quarterly-review`, Q4 2026 run. Verify-and-propose only: no data file or
template was changed. Research by five parallel agents, figures re-read from retrieved text (PDF
factsheets parsed directly, not through a summariser). Every date below is flagged for owner
confirmation before any change is made.

**Bottom line.** CMAs are within tolerance on current starting yields; no CMA change proposed. One
new manager vintage (BlackRock, data 30 Jun 2026) and a partial Vanguard update are out, but J.P.
Morgan's 2027 LTCMA is not, and the two new equity sources disagree in opposite directions, so the
vintage stays `2026-H1` until JPM lands (expected around 20 October). All 12 TERs checked agree with
`curated.json`. Three YIELD FLAGs resolve in favour of the computed figure (MMS, JK8, ESG). Three new
SGX codes (B95/B96, Q50), no delistings; four Xtrackers UCITS list 13 Oct 2026. Learn-tab SRS/CPF
facts confirmed; one precision edit proposed.

Weekly price-refresh Action: green (daily runs 29 Sep, 30 Sep, 1 Oct 2026 all `success`).

---

## Part 1 — Yield-anchored CMA sanity

| Anchor | Current value | As at | Source | Confidence |
|---|---|---|---|---|
| 3M compounded SORA | 1.231% | 29 Sep 2026 | housingloansg.com SORA chart; nexusmortgage.sg (1.23%) | two secondary; MAS API blocked |
| SGS 2Y | 1.98% | 30 Sep 2026 | investing.com; tradingeconomics.com | two-source (possibly same feed) |
| SGS 10Y | 2.47% | 30 Sep 2026 | as above | two-source (possibly same feed) |
| MBH weighted-avg YTM | 2.87% (dur 5.84, avg A) | 31 Aug 2026 | Amova MBH factsheet PDF | single, primary |
| A35 weighted-avg YTM | 2.40% (dur 8.31, AAA) | 31 Aug 2026 | Amova ABF Singapore Bond factsheet PDF | single, primary |
| AGGU yield to worst | 4.07% (dur 6.01), unhedged-basis portfolio yield | 31 Aug 2026 | iShares AGGU factsheet PDF | single, primary |
| AGG | YTM 5.53%; 30-day SEC 5.02%; 12m trailing 4.18% | 29 Sep 2026 | ishares.com/us/products/239458 | single, primary — YTM looks anomalous, see below |
| ES3 distribution yield | 3.09% | 30 Sep 2026 | SSGA ES3 product page | single, primary |
| Asia USD credit (N6M, JACI) | YTM 5.54% (dur 4.76) | 31 Aug 2026 | iShares N6M factsheet PDF | single, primary |
| Asia USD IG cross-check (Global X) | index YTM 4.85% | date unclear (footnote says 30 Apr 2026) | globalxetfs.com.hk | range check only |

MAS tightened twice in 2026 (S$NEER slope "increase slightly" 14 Apr, "increase very slightly" 27 Jul),
so the low SORA is not an easing cycle.

| Class | CMA | Starting-yield read | Gap | Verdict |
|---|---|---|---|---|
| cash (SGD, long-run) | 1.8 | 3M SORA 1.23 | −0.6 | Within design: a long-run average above today's rate, rationale in `informed_by` holds. No change. |
| sgd_bonds | 2.5 | A35 2.40 / MBH 2.87 / 10Y SGS 2.47 → blend ~2.5–2.7 | ≤0.2 | Within tolerance. No change. Yields up 30–50bp since July's anchors (A35 2.08, MBH 2.74, 10Y 2.14). |
| dev_bonds | 4.5 | AGGU YTW 4.07 unhedged (hedged higher by the USD carry); AGG SEC 5.02 | ±0.5 | Within tolerance. **Watch:** AGG's 5.53% YTM sits 135bp over its trailing yield and is single-source; if US aggregate yields really are ~5.0–5.5, the class is ~0.5pp light. Re-test against JPM 2027's US aggregate figure. |
| sg_equity (yield block) | 3.2 in building block | ES3 3.09 | −0.1 | Within tolerance. No change. |
| asia_bonds | 5.8 | JACI (N6M) 5.54; IG-only 4.85 | −0.3 | Within tolerance (JACI includes HY; our class is a blend). No change. |

## Part 2 — Manager-vintage check

| Provider | New vintage? | Detail |
|---|---|---|
| J.P. Morgan LTCMA | **No** | Page still shows the 2026 (30th) edition, data 30 Sep 2025. The 2026 edition published 20 Oct 2025, so 2027 is likely within weeks. |
| BlackRock CMA | **Yes** — published 11 Aug 2026, data 30 Jun 2026 | Workbook (no registration): blackrock.com/blk-inst-c-assets/images/tools/blackrock-investment-institute/cma/blackrock-capital-market-assumptions.xlsx. 10-yr USD: MSCI World 8.64, ACWI 9.02, US large cap 8.97, EM 8.80, US agg 4.88, global agg hedged 4.51, USD cash 3.66; no gold row. Return basis (geometric vs arithmetic) not stated in the workbook — **unverified, check before any like-for-like use.** |
| Vanguard VCMM | **Partial** — 30 Jun 2026 run, page updated 22 Jul 2026 | corporate.vanguard.com vemo-return-forecasts: US equity 4.2–6.2 (was 4.9–6.9), DM ex-US 4.5–6.5 (was 5.4–7.4), EM 2–4 (was 3.6–5.6). Bonds: text only ("increased modestly" US agg; global "little changed"). Cash and commodities not found. |
| WGC GLTER | **No** | Still the 17 Oct 2024 paper (5.2%). |

Read: on developed equity our 6.8 now sits between Vanguard (midpoint ~5.5) and BlackRock (8.6), with
JPM 2026 at 7.0. On EM, our 7.4 sits between Vanguard (2–4) and BlackRock (8.8); the providers have
moved further apart, not together. BlackRock's March World figure in our `informed_by` (8.3) moved
+0.3pp; Vanguard's ranges moved −0.7 to −1.6pp. Updating one leg would widen the disagreement
rather than resolve it. **Recommendation: hold all numbers and the `2026-H1` vintage; do an ad-hoc
pass after JPM 2027 publishes (check around 20–31 Oct 2026), then refresh `informed_by` with all three
vintages together.** Bonds and gold: no new evidence of a structural gap.

## Part 3 — SGX universe diff

Headless retrieval **succeeded** via SGX's public securities API
(`api.sgx.com/securities/v1.1`, pulled 1 Oct 2026 ~10:04 SGT): 94 ETF counters versus 91 in the frozen
CSV (vintage 2026-07-09). **No manual CSV re-download is needed for the diff.** The API carries no TER,
manager or listing date, so fee changes cannot be diffed from it (the CSV's TER column is empty anyway).

- **Delistings:** none — all 91 CSV codes still listed.
- **Name changes:** none since July (the Nikko AM → Amova rebrand was already in the CSV).
- **New codes (3):**
  1. **B95 / B96 — Amova Asia Credit Index ETF** (SGD-hedged / USD classes). Singapore VCC; tracks
     Bloomberg Asia ex-Japan USD Credit Index; semi-annual distributions; mgmt fee 0.20%, TER 0.35%
     (**single source**, Amova issuer page). Listing date unconfirmed (admitted 17 Jul 2026 per a search
     snippet; issuer page says 1 Oct 2026; API shows no trade yet). Candidate for `asia_bonds`;
     needs a second TER source and a pipeline rebuild before adding.
  2. **Q50 — CGS Fullgoal Singapore Next 50 Active ETF** (SGD). Listed 3 Sep 2026; Singapore-domiciled;
     active, benchmark iEdge Singapore Next 50; mgmt fee 0.65% (max 1.50%); **no published TER**.
     **This is a CGS International product.** Given the owner's CGSI advisory engagement through Q3
     2026, adding it to a public Personal-context tool is an owner call (disclosure/conflict), not a
     data call. Not proposed for addition.
- **Announced, not yet trading — four Irish-domiciled Xtrackers UCITS, SGD counters, 13 Oct 2026
  (Tuesday):** XUS S&P 500 (TER 0.03%), EUS S&P 500 Equal Weight (0.15%), XND Nasdaq-100 (0.20%), XWR
  MSCI World (0.12%). Codes and TERs agree across two **secondary** sources (Growbeansprout, Dollars and
  Sense); **not yet checked against the DWS prospectus**. The MAS-lodged PHS for the Equal Weight fund
  confirms the 13 Oct 2026 listing date but leaves the stock code blank. The README's listing-day
  follow-up list already covers these (confirm codes, add `curated.json` rows, map S27 → XUS in the swap
  tab, move Learn text to present tense).

## Part 4 — TER spot-check

All twelve agree with `curated.json`. Issuer figure plus justETF (UCITS) or a second SGX source:

| Fund | Recorded | Issuer | Second | Verdict |
|---|---|---|---|---|
| CSPX | 0.07 | 0.07 (iShares factsheet, data 3 Sep 2026) | 0.07 | OK |
| VUAA | 0.07 | not retrieved (wrong Vanguard product ID) | 0.07 | OK on one source; issuer recheck next quarter |
| VWRA | 0.14 | 0.14 (Vanguard UK) | 0.14 | OK |
| IWDA | 0.20 | 0.20 | 0.20 | OK |
| EIMI | 0.18 | 0.18 | 0.18 | OK |
| AGGU | 0.10 | 0.10 | 0.10 | OK |
| SGLN | 0.12 | 0.12 | 0.12 | OK |
| ES3 | 0.28 | 0.28, now the **FY Jun-2026** audited figure (SSGA factsheet 31 Aug 2026) | investkaki 0.28 | OK; refresh the note |
| G3B | 0.24 | 0.24 audited FY Jun-2025; mgmt fee 0.09 from 1 Oct 2025 | POEMS 0.25 (cap) | OK; FY Jun-2026 TER not yet published, should fall after the fee cut |
| HST | 0.56 | 0.56 audited FYE Dec-2025 (Lion Global) | stockanalysis 0.58 (likely FY2024) | OK |
| A93 | 0.60 (med, cap) | "capped at 0.60%" | DollarsAndSense 0.60 | OK; already labelled as a cap |
| ESG | 0.45 | 0.45 audited FYE Dec-2025 | stockanalysis 0.45 | OK; cap reportedly lapsed Apr-2026 (unverified), FY2026 TER may rise — recheck in Q1 2027 |

The three most-traded SGX satellites (by the CSV's traded value, ex ES3/G3B) were HST, A93 and ESG.

**YIELD FLAG adjudication** (pipeline run today printed three flags):

| Fund | Curated | Computed | Evidence | Proposal |
|---|---|---|---|---|
| MMS | 3.0 | 1.3 | Phillip 7-day annualised 1.2331% (25 Sep 2026); 12m distributions 1.316 on NAV ~103.4 = 1.27% | Adopt computed; curated 3.0 is a 2023–24 SORA-era figure |
| JK8 | 0.0 | 2.36 | Fund (now UOBAM FTSE China A50 Index ETF) states annual distributions around December; paid 0.0558 ex 18/19 Dec 2025 (POEMS + stockanalysis) | Adopt computed; note it rests on one annual payment |
| ESG | 3.0 | 5.05 | 12m distributions 0.0719 on price 1.43 ≈ 5.0%; calendar 2025 0.0813; ~5% for two years (stockanalysis; issuer page shows no history) | Adopt ~5.0, labelled trailing and possibly paid partly from capital |

## Part 5 — SRS / CPF-OA facts (Learn tab, `template.html` lines 1002–1029)

| Fact | Status | Primary source |
|---|---|---|
| SRS caps S$15,300 / S$35,700, within S$80,000 relief cap | Confirmed | MOF SRS page (upd. 15 Apr 2026); IRAS SRS contributions (upd. 14 Aug 2026) |
| Retirement age 64 from 1 Jul 2026; SRS age locked at first contribution | Confirmed | MOM retirement (upd. 1 Jul 2026); IRAS SRS withdrawals (upd. 3 Sep 2026) |
| 63 for Jul 2022–Jun 2026, 62 before | Consistent; inferred from the statutory age history, not tabled on the pages | MOM / IRAS |
| 65 by 2030 — intent, not law | Consistent; no legislation found (Parliament records not searched) | — |
| Early withdrawal 5% penalty + 100% taxable; 50% taxable spread over 10 years | Confirmed | IRAS SRS withdrawals |
| SRS idle ~0.05% | Bank rate; not stated by MOF/IRAS — copy already attributes it to the agent banks | — |
| OA 2.5% floor; first S$20,000 effectively 3.5% | Confirmed (OA 2.5% for Oct–Dec 2026). **Nuance:** the extra 1% is credited to the SA/RA, not the OA | CPF interest page (upd. 15 Sep 2026) |
| S$20,000 OA set-aside before CPFIS | Confirmed | CPF CPFIS pages |
| 35% stock / 10% gold limits; ETFs outside the 35% | Confirmed | CPFIS options page; CPFISInvestmentProducts.pdf (Sep 2025) |
| List A: ES3, G3B, A35, MBH, CFA, SPDR Gold Shares on; CLR not | Confirmed (RCSETF_ListA.pdf "Updated on: September 17, 2025" — still the latest) | cpf.gov.sg |
| Xtrackers: listing 13 Oct 2026; not on CPFIS List A | Confirmed for the Equal Weight PHS; others secondary. SRS eligibility is secondary-sourced and set by the operator banks — copy already says "confirm each counter with your SRS operator" | MAS OPERA PHS (14 Sep 2026) |
| New CPF investment scheme 1H 2028 | Confirmed (Budget 2026, MOM/CPF release 12 Feb 2026; providers 1H 2027) | mom.gov.sg 0212-cpf-new-investment-scheme |

Also new since July, not on the page: CPF's 4% floor on SA/MA/RA extended to 31 Dec 2027 (announced
22 Sep 2026). Not proposed for the page — it does not bear on the OA comparison.

---

## PROPOSED CHANGES (none applied; owner approval required)

### P1. `data/curated.json` — resolve the three YIELD FLAGs

```diff
   "MMS": {"ter": 0.25, "ter_conf": "high", "mgmt_fee": 0.1,
-          "yield": 3.0,
+          "yield": 1.3, "yield_note": "Trailing 12m distributions / NAV 1.27%; Phillip 7-day annualised 1.23% (25 Sep 2026). Moves with SORA.",
           ...}
   "JK8": {"ter": 1.09, "ter_conf": "high", "mgmt_fee": 0.45,
-          "yield": 0.0,
+          "yield": 2.4, "yield_note": "One annual distribution (0.0558, ex 18/19 Dec 2025; POEMS, stockanalysis). Fund renamed UOBAM FTSE China A50 Index ETF.",
           ...}
   "ESG": {"ter": 0.45, "ter_conf": "high", "mgmt_fee": 0.4,
-          "yield": 3.0,
+          "yield": 5.0, "yield_note": "Trailing 12m 0.0719 on ~1.43 (stockanalysis; issuer shows no history). ~5% for two years; may include capital.",
           ...}
```
Effect: all three fall within 1.5pp of the computed yield, so the pipeline's policy lets the computed
figure win and the flags clear. If `yield_note` is not a field the pipeline reads, drop it or fold it
into `ter_note`.

### P2. `data/curated.json` — note refresh only (no number changes)

```diff
   "ES3": {... "ter": 0.28,
-          "ter_note": "Audited realised TER (SSGA); 0.30% is the mgmt-fee cap."}
+          "ter_note": "Audited TER FY Jun-2026 (SSGA factsheet 31 Aug 2026); 0.30% is the mgmt-fee cap."}
   "ESG": {... 
-          "ter_note": "Audited FYE Dec-2025; cap lapsed Apr-2026, may drift up."}
+          "ter_note": "Audited FYE Dec-2025 (Lion Global, rechecked Oct 2026); cap reportedly lapsed Apr-2026 (unverified), FY2026 TER may be higher."}
```

### P3. `template.html` line 1007 — OA extra-interest precision

```diff
-... uninvested OA already earns a guaranteed <b>2.5%</b> (first S$20,000 effectively <b>3.5%</b> with extra interest).</td></tr>
+... uninvested OA already earns a guaranteed <b>2.5%</b> (first S$20,000 effectively <b>3.5%</b> with extra interest, though the extra 1% is paid into your Special or Retirement Account, not the OA).</td></tr>
```
Source: CPF "Earning CPF interest" page (upd. 15 Sep 2026). Then `python scripts/pipeline.py` to rebuild
`docs/index.html`.

### P4. `data/cma.json` — none now

Hold every number and `_meta.vintage: "2026-H1"`. Ad-hoc pass once JPM 2027 LTCMA publishes, which would
also append a `revision_log` entry recording the BlackRock Jun-2026 and Vanguard Jun-2026 points.

### P5. Universe candidates (not added; each needs a two-source TER, domicile check, rebuild)

- **XUS / EUS / XND / XWR** (Xtrackers, IE): on 13 Oct 2026, per the README listing-day list. Confirm the
  TERs against the DWS Singapore prospectus.
- **B95 / B96** (Amova Asia Credit, SG VCC, TER 0.35% single-source) → `asia_bonds`.
- **Q50** (CGS Fullgoal, active): owner decision on the CGSI connection before anything else.

## Owner manual actions

1. Rule on P1–P3.
2. Decide whether Q50 belongs in the tool at all (CGSI product).
3. **Restore the local `fix/min-font-11px` branch.** This run's start-of-session
   `git pull --rebase origin main` was executed while the checkout sat on `fix/min-font-11px` (its commit
   `Raise sub-11px font sizes…` is already on `origin/fix/min-font-11px`); it rebased the local copy onto
   `origin/main`, so it now shows "ahead 6, behind 1" against its remote. Nothing was pushed, and the
   remote branch is untouched. To undo:
   `git -C C:\dev\etf-starter-sg checkout fix/min-font-11px` then
   `git reset --hard origin/fix/min-font-11px` (only if no further local work has been added). The report
   itself was committed from a separate worktree on `main`.
4. Observation outside this task's scope: the Learn tab (line 1019, added in #1) links to
   theenoughpoint.com. The project memory records that this tool and The Enough Point should not be
   traceable to each other; the link connects them in the phuazz → venture direction. Worth a deliberate
   decision.

## No action needed

Price-refresh Action (green); all 12 TERs; CMA classes cash, sgd_bonds, dev_bonds, sg_equity,
asia_bonds on starting yields; dev_equity, em_asia_equity, gold, reits pending JPM 2027; SRS caps,
retirement-age lock, withdrawal tax treatment, CPFIS limits, List A membership and CLR exclusion; no
SGX delistings or renames.

## Dates to confirm

1 Oct 2026 (Thu, this run); 13 Oct 2026 (Tue, Xtrackers listing); 3 Sep 2026 (Q50 listing); 1 Oct or
17 Jul 2026 (B95/B96, unresolved); 29 Sep 2026 (SORA); 30 Sep 2026 (SGS, ES3); 31 Aug 2026 (factsheets);
11 Aug 2026 / 30 Jun 2026 (BlackRock publication / data); 22 Jul 2026 (Vanguard page); 15 Sep 2026
(CPF interest page); 17 Sep 2025 (List A PDF); ~20 Oct 2026 (expected JPM 2027, estimate). Weekdays
verified with Python `datetime`.
