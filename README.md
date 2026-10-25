# Portfolio X-ray (local, look-through)

> **Disclaimer:** informational only — not investment, tax or financial advice. Holdings come from issuer websites, SEC N-PORT filings or Cboe Canada (labelled on every row, each with its own as-of date) and can be stale or incomplete.

## Install & run
Unzip **both** `portfolio_xray_part1.zip` (app, Canadian holdings, screener data, samples) and `portfolio_xray_part2.zip` (US holdings) into the **same folder**; both extract into `portfolio_xray/`. The app stops with a clear message if a part is missing.

Needs Python 3.9+; `pip install openpyxl` (only for .xlsx upload/export). Nothing else.

    python app.py                     # http://127.0.0.1:8765/ (index ~8 s + overlap vectors ~6 s in background)
    python app.py --cli my.xlsx --out results/ [--usdcad 1.39] [--no-fx-fetch] [--raw] [--xlsx]
                  [--thresholds '{"stock":5,"sector":30,"country":60,"banks":15,"overlap":40,"equity":80,"fixed_income":15,"cash":5,"max_stock":5,"max_country":60,"max_sector":30}']
    python app.py --overlap XEQT,VFV,QQQ [--weights 50,30,20] --out r/
    python app.py --holders NVDA --out r/          # name / ticker / ISIN / CUSIP
    python app.py --equiv VOO --out r/
    python app.py --research CA --out r/           # or US
    python app.py --screener [--screener-weights '{"perf":40,"fee":35,"yld":0,"aum":15,"track":10}'] --out r/
Report flags can be combined; `--xlsx` writes one workbook with every table (plus disclaimer sheet).

## Features (web tabs)
1. **Portfolio X-ray** — upload any brokerage CSV/XLSX (columns auto-detected, editable). CAD totals (Bank of Canada Valet FX or manual), recursive fund-of-funds look-through. True top 25 companies (share classes/bonds of one issuer combined via CUSIP issuer code) and top 25 securities; hidden concentration alerts (single company, sector, country/US share, Big-6 Canadian banks combined, fund overlap — thresholds editable); currency exposure in CAD with the method per row (security currency in file → inferred from country → unknown), plus a view after stated fund hedging; fund-to-fund overlap heat-map among your ETFs ("VOO and VFV overlap 100%"); flags for partial/top-N, stale SEC snapshots, unmatched symbols; match quality; per-ETF sources & as-of; reconciliation check (securities sum = positions total, to the cent).
2. **Targets & rebalancing** — equity / fixed income / cash targets and max single-company / country / sector limits vs look-through actuals; breaches; simple CAD trade suggestions (gap ÷ "purity" of your holding most concentrated in that class; sells capped; first-order only).
3. **Overlap finder** — 2+ tickers (optional weights): pairwise overlap matrix, common-holdings table, nearest single funds in the index to the combined mix. Overlap = Σ min(weight A, weight B) over matched securities, cash/undisclosed excluded.
4. **Who holds it?** — reverse lookup by name/ticker/ISIN/CUSIP; ETFs ranked by look-through weight with market, direct vs via-fund, source, as-of. Share-class duplicates hidden; leveraged funds noted.
5. **CA ↔ US equivalents** — closest funds in the other market by holdings overlap (e.g. VFV ↔ VOO 100%, QQQ ↔ QQC 91%), with a neutral tax note (withholding tax, RRSP/TFSA) — not advice.
6. **Best Canadian ETFs** — rules-based ranked screener of Canadian-listed ETFs: A) core broad-market passive index (incl. dividend / high-dividend stock ETFs such as XEI, XDIV, VDY, VIDY, FLVI, which are NOT treated as income funds), B) income = option-writing / premium-income ETFs only (covered call, call/put writing, enhanced/premium yield, yield maximizer, UltraYield, autocallable, e.g. ZWC, ZWS, BIGY, QQCC, USCL) on broad indices or broad multi-sector baskets; single-sector covered-call funds (e.g. HMAX, QMAX, ZWB, ZWU) go to the deprioritized single-sector list, C) commodities & resources (precious metals, oil & gas, mining, uranium, lithium), D) spot bitcoin only (HIGH RISK), E) cash & cash equivalents (high-interest savings, money market, 0-3 month / ultra-short T-bill ETFs; anything with bond duration or credit risk, ultra-short bond funds, and option-overlay T-bill funds such as HBIL are excluded; ranked by low volatility (std dev of monthly returns), yield, MER, consistency (negative months), then AUM/track. Monthly-return volatility/consistency are not in the current data snapshot, so they show n/a and the score falls back to yield/MER/AUM/track; CAD and USD funds are labeled separately in the group column), plus a deprioritized single-sector list. Leveraged/inverse, single-stock (incl. single-stock yield) and non-bitcoin crypto are excluded; bonds/balanced/thematic are not ranked. Score = weighted percentiles of: performance vs benchmark (tracking difference + annualized 1/3/5y NAV total-return excess vs a reference fund = largest-AUM passive fund in the same benchmark group and hedging), fee (MER, else management fee), distribution yield, AUM, track length. Weights editable (incl. volatility/consistency for E); raw metrics, component scores, classification evidence and coverage shown; missing = n/a, never imputed. Source: Cboe Canada / Trackinsight data (`data/ca_screener.csv`, NAV/AUM as of 2026-10-21/22).
7. **Research** — most-owned securities by number of ETFs and by aggregate weight (CA and US separately, direct holdings, share-class dupes removed); concentration per fund (holdings count, top-10 weight, HHI, effective N); snapshot change table for the 48 Canadian funds that have an older disclosure (different list types — indicative only). US/SEC funds have one snapshot, so no change tracking there.
Every table has CSV and XLSX export; each tab has "Export ALL (XLSX)".

## Matching method
CUSIP (or CUSIP inside US/CA ISIN) → other ISIN → ticker mapped to the ID most often seen with it in the same region → exact normalized name mapped to an equity ID when that name maps to exactly one ID → normalized name only. Match quality is reported per portfolio (share of CAD by method) and per fund.

## Limits
- Fees exist only for Canadian ETFs (Cboe list); US (mostly SEC N-PORT) has no MER, so no US cost comparisons.
- SEC N-PORT data are quarter-end snapshots (months old); partial/top-N funds show only disclosed weights (rest = "Undisclosed").
- Hedging view uses fund name/description/URL keyword "Hedged"; sector/country/currency come from holdings files where present.
- Screener: tracking difference only exists for ~450 funds and its period is not stated by the source; return excess uses a reference *fund*, not the index itself; commodity groups are coarse (e.g. silver compared to the gold-group reference); classification is keyword + holdings based and can misclassify edge cases (see `classified_by`).

## Rebuilding data
`build_index.py` (holdings, funds, universe incl. CA MER, prior snapshots) then `build_screener.py` (Cboe fund_overview + performance → `data/ca_screener.csv`). Edit paths at the top.

## Data sources, as-of dates & terms
- US holdings: mostly SEC EDGAR N-PORT filings (public data, quarter-end snapshots), plus issuer sites where permitted.
- Canadian holdings and fund metadata: issuer websites and Cboe Canada / Trackinsight. These are subject to each issuer's and Cboe's terms of use. They are included here for personal, informational use; check the terms before redistributing.
- Every table shows its source and as-of date. The data was collected in October 2026 and is not updated automatically.
- Not investment, tax or financial advice.
