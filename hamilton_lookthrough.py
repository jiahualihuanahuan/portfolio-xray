#!/usr/bin/env python3
"""
On-demand multi-level look-through for Hamilton ETFs (HYLD, SMAX, QMAX, SDAY, ...).

These funds are currently gaps in the portfolio-xray index (Trackinsight 403 + no
issuer holdings URL in the gap notes). This module scrapes the official Hamilton
ETF pages (clean HTML tables) and recursively expands fund-of-funds holdings
down to underlying equities / cash / other ETFs.

Integration notes for portfolio-xray:
- The existing `analyze()` / `explode()` in app.py already supports recursive
  fund-of-funds (maxdepth=6) *when the holdings exist in the index*.
- Use this module to fill the Hamilton gap: either run it offline to produce
  rows that can be merged into holdings_CA.csv.gz, or call it live for any
  position whose resolve_fund returns a fundgap for a Hamilton ticker.

Usage:
    python hamilton_lookthrough.py HYLD
    python hamilton_lookthrough.py SMAX
"""

import urllib.request
import re
from html.parser import HTMLParser
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


class _HoldingsTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_td = False
        self.current: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "table" and ad.get("id", "").startswith("etf-holdings"):
            self.in_table = True
        if self.in_table and tag == "td":
            self.in_td = True
            self.current.append("")

    def handle_endtag(self, tag):
        if tag == "table" and self.in_table:
            self.in_table = False
        if tag == "td" and self.in_td:
            self.in_td = False
        if tag == "tr" and self.in_table and self.current:
            if len(self.current) >= 3:
                self.rows.append([c.strip() for c in self.current[:3]])
            self.current = []

    def handle_data(self, data):
        if self.in_td and self.current:
            self.current[-1] += data.strip()


def fetch_hamilton_holdings(ticker: str, timeout: int = 15) -> Dict:
    """Fetch holdings table from https://hamiltonetfs.com/etf/{ticker}/"""
    ticker = ticker.upper().strip()
    url = f"https://hamiltonetfs.com/etf/{ticker.lower()}/"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; portfolio-xray/1.0; +https://github.com/jiahualihuanahuan/portfolio-xray)"
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    parser = _HoldingsTableParser()
    parser.feed(html)

    holdings = []
    for row in parser.rows:
        tkr, name, wstr = row
        try:
            weight = float(wstr.replace("%", "").replace(",", "").strip())
        except ValueError:
            continue
        holdings.append({
            "ticker": tkr or None,
            "name": name,
            "weight": weight,
        })

    # Prefer the holdings as-of that sits immediately before the table
    as_of = "unknown"
    table_pos = html.find('id="etf-holdings')
    if table_pos > 0:
        before = html[max(0, table_pos - 400):table_pos]
        m = re.search(r"As at\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", before, re.I)
        if m:
            as_of = "As at " + m.group(1)
    if as_of == "unknown":
        m = re.search(r"As at\s+(September|March|June|December)\s+\d{1,2},\s+\d{4}", html, re.I)
        if m:
            as_of = m.group(0)

    return {
        "ticker": ticker,
        "as_of": as_of,
        "url": url,
        "holdings": holdings,
        "raw_sum": round(sum(h["weight"] for h in holdings), 2),
    }


# Hamilton tickers that we know how to expand (fund-of-funds or equity yield-max).
# Add more as they appear in holdings tables.
HAMILTON_FUNDS = {
    "HYLD", "HYLD.U",
    "SMAX", "QMAX", "LMAX", "EMAX", "FMAX", "AMAX", "RMAX",
    "SDAY", "QDAY", "CDAY",
    "HDIV", "HMAX", "UMAX", "HCAL", "HFIN",
    "SMVP", "SMVP.U", "QMVP", "UMVP", "CMVP", "QMVP.U",
}


def _is_hamilton_fund(ticker: Optional[str]) -> bool:
    if not ticker:
        return False
    t = ticker.upper()
    base = t.split(".")[0].split("/")[0]  # SMVP/U -> SMVP, SMVP.U -> SMVP
    return t in HAMILTON_FUNDS or base in HAMILTON_FUNDS or t.replace("/", ".") in HAMILTON_FUNDS


def see_through(
    ticker: str,
    max_depth: int = 3,
    normalize_equity: bool = False,
) -> Dict:
    """
    Recursively look through a Hamilton ETF.

    Returns dict with:
      levels   — holdings at each depth (1 = direct, 2 = second level, ...)
      leaf     — aggregated underlying positions with effective weight in the root
      flags    — fetch errors / notes
    """
    ticker = ticker.upper().strip()
    cache: Dict[str, Dict] = {}
    flags: List[str] = []

    def get(tk: str) -> Dict:
        if tk not in cache:
            try:
                cache[tk] = fetch_hamilton_holdings(tk)
            except Exception as e:
                cache[tk] = {"ticker": tk, "holdings": [], "error": str(e), "as_of": None, "url": None}
                flags.append(f"Failed to fetch {tk}: {e}")
        return cache[tk]

    levels: Dict[int, List[Dict]] = defaultdict(list)
    leaf_agg: Dict[Tuple[Optional[str], str], Dict] = {}

    def recurse(tk: str, parent_weight: float, depth: int, path: List[str]):
        if depth > max_depth:
            return
        data = get(tk)
        if not data.get("holdings"):
            return

        for h in data["holdings"]:
            scaled = parent_weight * h["weight"] / 100.0
            is_fund = _is_hamilton_fund(h["ticker"]) and depth < max_depth
            row = {
                "ticker": h["ticker"],
                "name": h["name"],
                "weight": h["weight"],
                "effective_weight": round(scaled * 100, 4),
                "is_fund": is_fund,
            }
            levels[depth].append(row)

            if not is_fund:
                key = (h["ticker"], h["name"])
                if key not in leaf_agg:
                    leaf_agg[key] = {
                        "ticker": h["ticker"],
                        "name": h["name"],
                        "effective_weight": 0.0,
                        "paths": [],
                    }
                leaf_agg[key]["effective_weight"] += scaled * 100
                leaf_agg[key]["paths"].append(" > ".join(path + [h["ticker"] or h["name"]]))
            else:
                recurse(h["ticker"], scaled, depth + 1, path + [h["ticker"]])

    root = get(ticker)
    if not root.get("holdings"):
        return {"ticker": ticker, "error": "No holdings found", "flags": flags}

    recurse(ticker, 1.0, 1, [ticker])

    for d in levels:
        levels[d].sort(key=lambda x: -abs(x["effective_weight"]))

    leaf = sorted(leaf_agg.values(), key=lambda x: -abs(x["effective_weight"]))
    for x in leaf:
        x["effective_weight"] = round(x["effective_weight"], 2)

    if normalize_equity:
        pos = [x for x in leaf if (x["effective_weight"] or 0) > 0 and x["ticker"]]
        total = sum(x["effective_weight"] for x in pos) or 1.0
        for x in pos:
            x["normalized_equity_weight"] = round(100.0 * x["effective_weight"] / total, 2)
        leaf = pos

    return {
        "ticker": ticker,
        "as_of": root.get("as_of"),
        "url": root.get("url"),
        "raw_sum_root": root.get("raw_sum"),
        "levels": dict(levels),
        "leaf": leaf,
        "flags": flags,
        "note": (
            "effective_weight = contribution to the root ETF in percentage points. "
            "Negative weights (cash borrowing / written calls) are preserved. "
            "HYLD uses ~25% cash leverage; SDAY/QDAY are ~2x. "
            "Non-Hamilton ETFs (VOO, QQQM, ...) are treated as leaves."
        ),
    }


if __name__ == "__main__":
    import json
    import sys

    tk = sys.argv[1] if len(sys.argv) > 1 else "HYLD"
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    res = see_through(tk, max_depth=depth)

    print(f"=== {res['ticker']}  ({res['as_of']}) ===")
    print(f"Root raw weight sum: {res.get('raw_sum_root')}")
    print(f"URL: {res.get('url')}")
    if res.get("flags"):
        print("Flags:", res["flags"])

    print("\n--- Level 1 (direct holdings) ---")
    for r in res["levels"].get(1, [])[:15]:
        mark = " [FUND]" if r["is_fund"] else ""
        print(f"  {r['ticker'] or 'CASH':10} {r['effective_weight']:7.2f}%  {r['name']}{mark}")

    print("\n--- Top 20 leaf / underlying (effective weight in root) ---")
    for x in res["leaf"][:20]:
        print(f"  {x['ticker'] or 'CASH':10} {x['effective_weight']:7.2f}%  {x['name']}")

    # Highlight a few popular names
    want = {"NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO"}
    print("\n--- Selected mega-caps ---")
    for x in res["leaf"]:
        if x["ticker"] in want:
            print(f"  {x['ticker']:8} {x['effective_weight']:6.2f}%   via {x['paths'][0] if x['paths'] else ''}")
