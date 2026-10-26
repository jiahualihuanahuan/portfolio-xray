# Portfolio X-ray (local, look-through)

> **Disclaimer:** informational only — not investment, tax or financial advice. Holdings come from issuer websites, SEC N-PORT filings or Cboe Canada (labelled on every row, each with its own as-of date) and can be stale or incomplete.

## Run with Docker (easiest)

    git clone https://github.com/jiahualihuanahuan/portfolio-xray.git
    cd portfolio-xray
    docker compose up -d --build

Then open http://localhost:8765/ (first load takes ~15 s while the index builds). Stop with `docker compose down`. After pulling new data: `git pull && docker compose up -d --build`.

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
6. **Best Canadian ETFs** — rules-based screener over the full universe of **every ETF listed in Canada** (2,113 funds / 53 issuers in the Cboe Canada snapshot, any underlying: Canadian, US, international, global). "Canadian ETF" = Canada-listed; no category filters on where the holdings are (VFV, ZSP, XUS, XQQ, ZQQ, QQC, VXC, XEF, XEC, VEE, XAW are all in A). Sections: A) core broad-market index (incl. dividend ETFs such as XEI, VDY), B) income = option-writing / premium-income only on broad indices or multi-sector baskets (ZWC, BIGY, HYLD…), C) commodities & resources, D) spot bitcoin only (HIGH RISK), E) cash & equivalents (HISA, money market, 0-3m T-bill; ranked by volatility, yield, MER, consistency), F) bonds / fixed income (ranked by fee, yield, performance vs benchmark, AUM), G) balanced / asset-allocation (XEQT, VEQT, VGRO…), H) thematic / factor / active / other multi-sector, S) single-sector (deprioritized, shown last), X) **Excluded (not recommended)** — leveraged ≥1.45x or any inverse, single-stock (by name or by look-through: one non-wrapper holding ≥40% that matches the fund name), crypto other than pure spot bitcoin — each with reason and evidence. A **coverage line** proves nothing is silently dropped: total Canada-listed = sum of all sections + share-class variants (USD `.U`, `.F`, `.B`, hedged/unhedged or other classes of the same fund are listed in the `variants` column of their main row). Badges: **New (<1y)** — new funds are ranked normally; **Insufficient data** — a score needs ≥2 non-missing scored metrics (yield alone cannot carry a fund), otherwise the fund is listed unranked; **Leverage 1.25x** — moderate leverage (1.25–1.33x, e.g. Hamilton/Global X/Harvest "Enhanced", LBIT) is ranked in its normal category, and funds whose description says they use modest leverage without a ratio get "Leverage (ratio not stated)". Score = weighted percentiles of performance (core/C/D/E/F: tracking difference + 1/3/5y NAV total-return excess vs the largest-AUM passive fund in the same benchmark group and hedging; B/G/H/S: average annualized 1/3/5y NAV total return — shown in `perf_basis`), fee (MER, else management fee), distribution yield, AUM, track length, 1y volatility (E) — weights editable; raw metrics, component scores, classification evidence and metric coverage shown; missing = n/a, never imputed. Source: Cboe Canada / Trackinsight data (`data/ca_screener.csv`, NAV/AUM as of 2026-10-21/22). Not investment advice.
7. **Research** — most-owned securities by number of ETFs and by aggregate weight (CA and US separately, direct holdings, share-class dupes removed); concentration per fund (holdings count, top-10 weight, HHI, effective N); snapshot change table for the 48 Canadian funds that have an older disclosure (different list types — indicative only). US/SEC funds have one snapshot, so no change tracking there.
Every table has CSV and XLSX export; each tab has "Export ALL (XLSX)".

## Matching method
CUSIP (or CUSIP inside US/CA ISIN) → other ISIN → ticker mapped to the ID most often seen with it on the same listing country (`exchange_country`), then in the same region → exact normalized name mapped to an equity ID when that name maps to exactly one ID → normalized name only. Match quality is reported per portfolio (share of CAD by method) and per fund.

**Ticker standard.** Every holdings row (and uploaded portfolio symbol) goes through one function, `tickers.norm_ticker` (used by `build_index.py` and `app.py`): `ticker` = bare uppercase local symbol with share class after a dot (TSLA, RY, BRK.B, RCI.B, CAR.UN, 7203, 0700); vendor exchange suffixes (Bloomberg `TSLA US`, `RY CN`, `ALV GY`, `SAP.GY`, `X US EQUITY`; Yahoo `RY.TO`, `7203.T`, `0700.HK`; prefixes `TSX:`; `_SEK`; `-T`) are stripped into `exchange_country` (ISO-2; falls back to the row country, then currency, ignoring offshore domiciles like KY/BM). The vendor original is kept in `ticker_raw`. Preferred series (`MFC.PR.L`, `DLR.L`), `.UN` units and non-equity rows (bonds, cash, futures, options, swaps, FX: cleaned, no exchange) are not rewritten into equity tickers. "Who holds it?" accepts any of these forms (TSLA, TSLA US, RY.TO, RY CN, BRK/B, BRK B).



## Hamilton multi-level look-through tab
The web UI includes a **Hamilton look-through** tab that live-scrapes official holdings tables from hamiltonetfs.com for fund-of-funds such as HYLD (SMAX / QMAX / LMAX / EMAX / SDAY / QDAY / … → underlying equities such as NVDA / AAPL).  
These tickers are currently gaps in the static index (Trackinsight 403). The tab shows level-1 holdings and the aggregated effective weights of the leaf securities (including cash leverage). Non-Hamilton ETFs (e.g. VOO, QQQM) are left as leaves.

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
