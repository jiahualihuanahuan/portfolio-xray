#!/usr/bin/env python3
"""Portfolio X-ray (local look-through app). Run: python app.py  [--port 8765] [--no-browser]
Headless: python app.py --cli portfolio.xlsx --out outdir [--usdcad 1.38] [--no-fx-fetch]
Uses ONLY the prebuilt holdings index in ./data (scraped issuer files / SEC N-PORT)."""
import csv, gzip, io, json, os, re, sys, base64, difflib, urllib.request, datetime, threading, webbrowser, argparse
from collections import defaultdict, Counter
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tickers import norm_ticker, parse_user_symbol   # shared ticker normalizer (same rules as build_index.py)
DATA = os.path.join(HERE, 'data')
csv.field_size_limit(10**8)

# ---------------- index ----------------
class Index:
    def __init__(s):
        s.meta = json.load(open(os.path.join(DATA, 'meta.json')))
        s.funds = {}                      # 'CA:XEQT' -> info
        for r in csv.DictReader(open(os.path.join(DATA, 'funds.csv'), encoding='utf-8')):
            s.funds[r['m'] + ':' + r['fund']] = r
        s.univ = {}                       # (m, ticker) -> row ; includes gaps
        s.isin2fund = {}
        s.name2fund = {}
        for r in csv.DictReader(open(os.path.join(DATA, 'universe.csv'), encoding='utf-8')):
            for t in {r['ticker'], r['base']}:
                if t: s.univ.setdefault((r['m'], t.upper()), r)
            if r['isin']: s.isin2fund.setdefault(r['isin'], r['m'] + ':' + r['ticker'])
            if r['name']: s.name2fund.setdefault(normname(r['name']), r['m'] + ':' + r['ticker'])
        s.rows = defaultdict(list)        # fundkey -> raw csv lines
        s.tkrmap = defaultdict(Counter)   # (exchange_country|region, canonical ticker) -> Counter(id-key)
        tany = defaultdict(Counter)       # canonical ticker -> Counter(id-key), any listing country
        s.secmeta = {}                    # key -> (name, tkr, sec, cty, ac, ccy)
        parts = json.load(open(os.path.join(DATA, 'meta.json'))).get('holdings_parts', ['holdings.csv.gz'])
        miss = [p for p in parts if not os.path.exists(os.path.join(DATA, p))]
        if miss: sys.exit('Missing data file(s) ' + ', '.join(miss) + ' in ' + DATA + ': unzip ALL portfolio_xray_part*.zip files into the same folder.')
        def _rows():
            for p in parts:
                with gzip.open(os.path.join(DATA, p), 'rt', encoding='utf-8') as fh:
                    rd = csv.reader(fh); next(rd)
                    yield from rd
        n2i = defaultdict(set)
        if True:
            for row in _rows():
                s.rows[row[0] + ':' + row[1]].append(row)
                m, fund, lvl, name, tkr, isin, cusip = row[:7]
                ik = idkey(isin, cusip)
                if ik and len(name) > 3 and nac(row[10]) == 'Equity': n2i[normname(name)].add(ik)
                if ik and tkr and tkr not in ('-', 'N/A'):
                    s.tkrmap[(region(row[9], row[11]), tkr.upper())][ik] += 1          # legacy CA/X namespace
                    if rexc(row): s.tkrmap[(rexc(row), tkr.upper())][ik] += 1          # listing-country namespace
                    if rexc(row): tany[tkr.upper()][ik] += 1
        s.tkr_best = {k: c.most_common(1)[0][0] for k, c in s.tkrmap.items()}
        s.tkr_any = {k: c.most_common(1)[0][0] for k, c in tany.items()}
        del tany
        del s.tkrmap
        s.name2id = {k: next(iter(v)) for k, v in n2i.items() if len(v) == 1 and len(k) >= 4}
        del n2i
        s.secmeta = {}                    # key -> [sector, country] learned from any fund that classifies it
        for rows in s.rows.values():
            for r in rows:
                sec = nsec(r[8]); cty = r[9]
                if sec in ('Unknown', 'Other', 'Cash and/or Derivatives') or sec.startswith('Open End'): sec = ''
                if not sec and not cty: continue
                k = s.seckey(r[3], r[4], r[5], r[6], r[9], r[11], '', rexc(r))
                mm = s.secmeta.setdefault(k, ['', ''])
                if not mm[0] and sec and sec.upper() == sec.upper() and len(sec) < 40: mm[0] = sec
                if not mm[1] and cty: mm[1] = ncty(cty)

    def fund_has(s, fk): return fk in s.funds and fk in s.rows

    def seckey(s, name, tkr, isin, cusip, cty, ccy, ac='', exc=''):
        ik = idkey(isin, cusip)
        if ik: return ik
        t = (tkr or '').upper()
        if t and t not in ('-', 'N/A', 'CASH'):
            rg = region(cty, ccy)
            return (exc and s.tkr_best.get((exc, t))) or s.tkr_best.get((rg, t)) or 'T:%s:%s' % (rg, t)
        nn = normname(name)
        if nac(ac) not in ('Fixed Income', 'Cash & Equivalents', 'Derivatives') and nn in s.name2id: return s.name2id[nn]
        return 'N:' + nn

IDX = None

def idkey(isin, cusip):
    isin = (isin or '').strip().upper(); cusip = (cusip or '').strip().upper()
    if len(cusip) == 9 and cusip not in ('000000000',): return 'C:' + cusip
    if len(isin) == 12:
        return 'C:' + isin[2:11] if isin[:2] in ('US', 'CA') else 'I:' + isin
    return None

def rexc(row): return row[14] if len(row) > 14 else ''     # exchange_country column (ISO-2) or ''
def region(cty, ccy=''):
    c = (cty or '').strip().upper()
    if c in ('CANADA', 'CA', 'CAN'): return 'CA'
    if not c and (ccy or '').upper() == 'CAD': return 'CA'
    return 'X'

def normname(n):
    n = re.sub(r'[^A-Z0-9 ]', ' ', (n or '').upper())
    n = re.sub(r'\b(INC|CORP|CORPORATION|LTD|LIMITED|PLC|CO|THE|CLASS|CL|COM|SA|AG|NV|HOLDINGS?)\b', ' ', n)
    return ' '.join(n.split())

CTY = {'US': 'United States', 'USA': 'United States', 'UNITED STATES OF AMERICA': 'United States', 'CA': 'Canada', 'CAN': 'Canada',
       'GB': 'United Kingdom', 'UK': 'United Kingdom', 'JP': 'Japan', 'DE': 'Germany', 'FR': 'France', 'CH': 'Switzerland', 'NL': 'Netherlands',
       'AU': 'Australia', 'CN': 'China', 'TW': 'Taiwan', 'KR': 'South Korea', 'KOREA (SOUTH)': 'South Korea', 'IN': 'India', 'IE': 'Ireland',
       'SE': 'Sweden', 'DK': 'Denmark', 'ES': 'Spain', 'IT': 'Italy', 'HK': 'Hong Kong', 'BR': 'Brazil', 'SG': 'Singapore', 'FI': 'Finland',
       'BE': 'Belgium', 'NO': 'Norway', 'IL': 'Israel', 'ZA': 'South Africa', 'MX': 'Mexico', 'BM': 'Bermuda', 'KY': 'Cayman Islands', 'LU': 'Luxembourg', 'JE': 'Jersey'}
def ncty(c):
    c = (c or '').strip()
    if not c or c in ('-', 'N/A'): return 'Unknown'
    return CTY.get(c.upper(), c.title() if c.isupper() else c)
SECA = {'INFORMATION TECHNOLOGY': 'Information Technology', 'IT': 'Information Technology', 'TECHNOLOGY': 'Information Technology',
        'COMMUNICATION': 'Communication Services', 'COMMUNICATION SERVICES': 'Communication Services', 'TELECOMMUNICATION SERVICES': 'Communication Services',
        'CONSUMER DISCRETIONARY': 'Consumer Discretionary', 'CONSUMER STAPLES': 'Consumer Staples', 'HEALTH CARE': 'Health Care', 'HEALTHCARE': 'Health Care',
        'FINANCIALS': 'Financials', 'FINANCIAL': 'Financials', 'INDUSTRIALS': 'Industrials', 'ENERGY': 'Energy', 'MATERIALS': 'Materials',
        'REAL ESTATE': 'Real Estate', 'UTILITIES': 'Utilities'}
def nsec(x):
    x = (x or '').strip()
    if not x or x in ('-', 'N/A'): return 'Unknown'
    return SECA.get(x.upper(), x)
def nac(a, name=''):
    a = (a or '').strip(); u = a.upper()
    if not a: return 'Unknown'
    if u.startswith('EQ') or 'EQUIT' in u or u in ('EC', 'EP', 'COMMON STOCK', 'STOCK'): return 'Equity'
    if u.startswith('FI') or 'BOND' in u or 'FIXED' in u or u.startswith('DBT') or u in ('DEBT',): return 'Fixed Income'
    if 'CASH' in u or 'MONEY' in u or u in ('STIV', 'CA'): return 'Cash & Equivalents'
    if 'FUTURE' in u or 'DERIV' in u or 'SWAP' in u or 'OPTION' in u or u in ('DFE', 'DIR', 'DCO', 'DE'): return 'Derivatives'
    if 'REAL' in u or 'REIT' in u: return 'Real Estate'
    if 'COMMOD' in u or 'GOLD' in u: return 'Commodities'
    return a

FUNDY = re.compile(r'\b(ETF|ETFS|FUND|INDEX|ISHARES|VANGUARD|SPDR|PORTFOLIO|POWERSHARES)\b', re.I)

def resolve_fund_row(row, holder_m):
    """Return fund key if this holding row is itself a fund we have (or know of). Never guesses by fuzzy name."""
    name, tkr, isin = row[3], row[4].upper().strip(), row[5].strip().upper()
    if isin and isin in IDX.isin2fund: return IDX.isin2fund[isin]
    if isin or row[6].strip(): return None          # has a security id that is not a fund we know
    looks = FUNDY.search(name) or 'ETF' in row[10].upper() or 'FUND' in row[10].upper() or 'INVESTMENT VEHICLE' in row[8].upper()
    if tkr and looks:
        for m in ([holder_m, 'US' if holder_m == 'CA' else 'CA']):
            if (m, tkr) in IDX.univ: return m + ':' + IDX.univ[(m, tkr)]['ticker']
    if looks:
        fk = IDX.name2fund.get(normname(name))
        if fk: return fk
    return None

# ---------------- user symbols ----------------
CA_SUF = re.compile(r'(\.TO|-T|\.TSX|:CA|\.CN|-CT|\.NE|\.V|-V|\.CA|:TSX|\.TSE)$', re.I)
CA_PRE = re.compile(r'^(TSE|TSX|XTSE|CVE|NEO|TSXV)[:.]', re.I)
US_SUF = re.compile(r'(\.US|:US|-US)$', re.I)
US_PRE = re.compile(r'^(NYSE|NASDAQ|ARCA|NYSEARCA|BATS|AMEX|XNAS|XNYS)[:.]', re.I)

def parse_symbol(sym):
    s = ' '.join((sym or '').strip().upper().split())
    hint = None
    if re.search(r' [A-Z]{2}$| (EQUITY|PFD)$|/|^[A-Z ]+:', s) or re.match(r'^\S+ [A-Z]$', s):   # 'TSLA US', 'RY CN', 'BRK/B', 'BRK B', 'TSX:RY'
        t, e = parse_user_symbol(s)
        if t and ' ' not in t:
            s = t; hint = e if e in ('CA', 'US') else None
    s = s.replace(' ', '')
    if CA_PRE.search(s): s = CA_PRE.sub('', s); hint = 'CA'
    if US_PRE.search(s): s = US_PRE.sub('', s); hint = 'US'
    if CA_SUF.search(s): s = CA_SUF.sub('', s); hint = 'CA'
    if US_SUF.search(s): s = US_SUF.sub('', s); hint = 'US'
    s = re.sub(r'[-/]U$', '.U', s)          # HXS-U -> HXS.U
    if s.endswith('.U'): hint = hint or 'CA'
    return s, hint

def resolve_symbol(sym, ccy):
    """-> (kind, key, note). kind: fund | fundgap | stock | unknown"""
    s, hint = parse_symbol(sym)
    if not s: return 'unknown', None, 'blank symbol'
    ccy = (ccy or '').upper()
    if hint: order = [hint, 'US' if hint == 'CA' else 'CA']
    elif ccy == 'CAD': order = ['CA', 'US']
    elif ccy == 'USD': order = ['US', 'CA']
    else: order = ['CA', 'US'] if ('CA', s) in IDX.univ and ('US', s) not in IDX.univ else ['US', 'CA']
    cands = [s]
    if '.' in s: cands.append(s.replace('.', '-'))
    for m in order:
        for c in cands:
            u = IDX.univ.get((m, c))
            if u:
                fk = m + ':' + u['ticker']
                if s.endswith('.U') and IDX.fund_has(m + ':' + s): fk = m + ':' + s
                if IDX.fund_has(fk):
                    note = '' if (hint or ccy or not ((('US' if m == 'CA' else 'CA'), s) in IDX.univ)) else 'ambiguous ticker (listed in both CA and US universes); assumed %s - add a suffix/currency to disambiguate' % m
                    return 'fund', fk, note
                base = m + ':' + u['base']
                if IDX.fund_has(base): return 'fund', base, 'holdings of %s used for %s listing' % (u['base'], c)
                return 'fundgap', fk, 'ETF is in our %s universe but holdings not collected: %s' % (m, u.get('gap') or 'no holdings file')
        if hint and m == hint and not any((m, c) in IDX.univ for c in cands):
            pass
    # direct stock: canonical ticker (tickers.norm_ticker) looked up in its listing country first, then anywhere
    base = s.replace('.U', '') if s.endswith('.U') else s
    base = norm_ticker(base)[0] or base
    pref = hint or ('CA' if ccy == 'CAD' else 'US' if ccy == 'USD' else '')
    regs = list(dict.fromkeys(x for x in (pref, 'CA' if pref == 'CA' else 'X', 'US', 'CA', 'X') if x))
    for t in dict.fromkeys((base, base.replace('-', '.'))):
        for rg in regs:
            k = IDX.tkr_best.get((rg, t))
            if k: return 'stock', k, ''
        k = IDX.tkr_any.get(t)
        if k: return 'stock', k, ''
    rg = 'CA' if pref == 'CA' else 'X'
    return 'stock', 'T:%s:%s' % (rg, base), 'symbol not matched to any ISIN/CUSIP in our holdings data; shown as-is with no classification'

# ---------------- spreadsheet parsing ----------------
SYN = {
    'symbol': ['symbol', 'ticker', 'security symbol', 'stock symbol', 'code', 'instrument', 'security', 'ticker symbol', 'sym', 'root'],
    'quantity': ['quantity', 'qty', 'shares', 'units', 'position', 'open qty', 'quantity held', 'no of shares', 'number of shares', 'share quantity'],
    'market_value': ['market value', 'mkt value', 'mkt val', 'market val', 'current value', 'total value', 'position value', 'value', 'current market value', 'market value cad', 'market value usd', 'mv'],
    'currency': ['currency', 'ccy', 'curr', 'market value currency', 'security currency', 'position currency', 'currency code', 'cur'],
    'price': ['price', 'last price', 'current price', 'market price', 'last', 'close', 'closing price', 'mark price'],
    'name': ['description', 'name', 'security name', 'security description', 'company'],
}
NEG = {'symbol': ['currency'], 'market_value': ['book', 'cost', 'gain', 'loss', 'change', 'pnl', 'p&l', 'unreal', 'average', 'avg'],
       'price': ['avg', 'average', 'cost', 'book'], 'quantity': ['value']}

def hnorm(h): return ' '.join(re.sub(r'[^a-z0-9& ]', ' ', str(h or '').lower()).split())

def score(h, field):
    h = hnorm(h)
    if not h: return 0
    if any(n in h for n in NEG.get(field, [])): return 0
    best = 0
    for syn in SYN[field]:
        if h == syn: return 1.0
        if re.search(r'\b' + re.escape(syn) + r'\b', h): best = max(best, 0.85 - 0.02 * (len(h.split()) - len(syn.split())))
        best = max(best, difflib.SequenceMatcher(None, h, syn).ratio() * 0.8)
    return best

def read_table(filename, data):
    fn = filename.lower()
    if fn.endswith(('.xlsx', '.xlsm')):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        best = None
        for ws in wb.worksheets:
            rows = [['' if v is None else v for v in r] for r in ws.iter_rows(values_only=True)]
            if best is None or len(rows) > len(best): best = rows
        return best or []
    if fn.endswith('.xls'): raise ValueError('Old .xls is not supported - save as .xlsx or .csv')
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try: txt = data.decode(enc); break
        except UnicodeDecodeError: continue
    try: dl = csv.Sniffer().sniff(txt[:5000], delimiters=',;\t|').delimiter
    except Exception: dl = ','
    return [r for r in csv.reader(io.StringIO(txt), delimiter=dl)]

def detect(rows):
    bestrow, bestsc, bestmap = 0, -1, {}
    for i, r in enumerate(rows[:40]):
        cells = [str(c) for c in r]
        mp, tot = {}, 0
        for f in SYN:
            sc = [(score(c, f), j) for j, c in enumerate(cells)]
            s, j = max(sc) if sc else (0, None)
            if s >= 0.6: mp[f] = j; tot += s
        # avoid same column for two fields
        if tot > bestsc and 'symbol' in mp: bestrow, bestsc, bestmap = i, tot, mp
    used = {}
    for f in ['symbol', 'market_value', 'quantity', 'currency', 'price', 'name']:
        j = bestmap.get(f)
        if j is not None and j in used.values(): bestmap.pop(f)
        elif j is not None: used[f] = j
    return bestrow, bestmap

def num(v):
    if isinstance(v, (int, float)): return float(v)
    s = str(v or '').strip().replace(',', '').replace('$', '').replace('CAD', '').replace('USD', '').strip()
    neg = s.startswith('(') and s.endswith(')')
    s = s.strip('()')
    try: x = float(s)
    except ValueError: return None
    return -x if neg else x

def positions(rows, hdr, mp):
    out = []
    for r in rows[hdr + 1:]:
        g = lambda f: (r[mp[f]] if f in mp and mp[f] is not None and mp[f] < len(r) else '')
        sym = str(g('symbol')).strip()
        if not sym or re.match(r'^(total|subtotal|cash total|account|grand total)', sym, re.I): continue
        tk, ex = parse_user_symbol(sym)
        p = dict(symbol=sym, ticker=parse_symbol(sym)[0] if ex in ('', 'CA', 'US') else tk, exchange_country=ex, name=str(g('name')).strip(), qty=num(g('quantity')), mv=num(g('market_value')), price=num(g('price')),
                 ccy=str(g('currency')).strip().upper()[:3])
        if p['mv'] is None and p['qty'] is not None and p['price'] is not None: p['mv'] = p['qty'] * p['price']; p['mvnote'] = 'value = quantity x price'
        out.append(p)
    return out

# ---------------- FX ----------------
FXCACHE = {}
def boc_rate(ccy, fetch=True):
    ccy = ccy.upper()
    if ccy == 'CAD': return 1.0, 'n/a', 'CAD'
    if ccy in FXCACHE: return FXCACHE[ccy]
    if not fetch: return None
    try:
        req = urllib.request.Request('https://www.bankofcanada.ca/valet/observations/FX%sCAD/json?recent=1' % ccy, headers={'User-Agent': 'lookthrough-app'})
        j = json.load(urllib.request.urlopen(req, timeout=10))
        o = j['observations'][-1]
        FXCACHE[ccy] = (float(o['FX%sCAD' % ccy]['v']), o['d'], 'Bank of Canada Valet FX%sCAD' % ccy)
        return FXCACHE[ccy]
    except Exception:
        return None

# ---------------- look-through ----------------

def etf_lookthrough(ticker, maxdepth=6, normalize=True):
    """Look through any ETF to individual holdings.
    Prefers the prebuilt index (recursive fund-of-funds). Falls back to live Hamilton scrape for those gaps.
    Returns {ticker, source, as_of, levels, leaf, flags, note}.
    """
    ticker = (ticker or '').upper().strip()
    if not ticker:
        return dict(error='ticker required')

    # Try index first (CA then US)
    kind, key, note = resolve_symbol(ticker, '')
    flags = []
    if kind == 'fund' and IDX.fund_has(key):
        # Build a tiny portfolio of 100 CAD of this fund and run the existing explode
        pos = [dict(symbol=ticker, ticker=ticker, name=ticker, qty=1, mv=100.0, price=100.0, ccy='CAD')]
        res = analyze(pos, normalize=normalize, maxdepth=maxdepth)
        # Extract levels roughly from coverage + securities
        levels = {}
        # Level 1 = direct holdings of the fund
        info = IDX.funds[key]
        rows = IDX.rows.get(key, [])
        l1 = []
        for r in rows:
            try: w = float(r[7])
            except: continue
            sub = resolve_fund_row(r, key.split(':')[0])
            l1.append(dict(ticker=r[4] or None, name=r[3], weight=w, is_fund=bool(sub and IDX.fund_has(sub))))
        levels[1] = sorted(l1, key=lambda x: -abs(x['weight']))[:200]
        # Leaf = securities after full recursion
        leaf = []
        for s in res.get('securities', []):
            if s['key'].startswith(('CASH:', 'UNDISCLOSED:', 'FUND:', 'RESIDUAL:')): continue
            leaf.append(dict(ticker=s.get('ticker'), name=s.get('name'), effective_weight=round(s.get('pct', 0), 2),
                             sector=s.get('sector'), country=s.get('country')))
        leaf = sorted(leaf, key=lambda x: -abs(x['effective_weight']))
        return dict(ticker=ticker, resolved_as=key, source=info.get('source'), as_of=info.get('asof'),
                    url=info.get('url'), levels=levels, leaf=leaf[:500], flags=res.get('flags', []),
                    note='Recursive look-through from prebuilt index (maxdepth=%d). effective_weight is %% of the ETF.' % maxdepth,
                    method='index')

    # Hamilton live fallback
    try:
        import hamilton_lookthrough
        if hamilton_lookthrough._is_hamilton_fund(ticker) or (kind == 'fundgap' and 'Hamilton' in (note or '')):
            res = hamilton_lookthrough.see_through(ticker, max_depth=maxdepth)
            res['method'] = 'hamilton_live'
            res['resolved_as'] = key
            return res
    except Exception as e:
        flags.append('Hamilton fallback failed: ' + str(e))

    # Nothing available
    gap = note or 'no holdings in index'
    return dict(ticker=ticker, error='No holdings available for %s (%s)' % (ticker, gap),
                resolved_as=key, flags=flags, method='none',
                note='This ETF is in the universe but holdings were not collected. Index covers most iShares/Vanguard/BMO/etc. Hamilton gaps can be filled live.')

def analyze(pos, manual_fx=None, fetch_fx=True, normalize=True, maxdepth=6):
    manual_fx = {k.upper(): float(v) for k, v in (manual_fx or {}).items() if v not in (None, '')}
    agg = {}                    # seckey -> dict
    flags, cov, fxused, prows = [], {}, {}, []
    def addsec(key, v, name, tkr, sec, cty, ac, ccy, via, exc=''):
        a = agg.get(key)
        if not a:
            a = agg[key] = dict(key=key, name=name or tkr or key, ticker=tkr, exchange_country=exc, sector=nsec(sec), country=ncty(cty), asset_class=nac(ac), currency=(ccy or 'Unknown'), value_cad=0.0, direct_cad=0.0, via=Counter())
        else:
            if a['sector'] == 'Unknown' and sec: a['sector'] = nsec(sec)
            if a['country'] == 'Unknown' and cty: a['country'] = ncty(cty)
            if a['asset_class'] == 'Unknown' and ac: a['asset_class'] = nac(ac)
            if a['currency'] == 'Unknown' and ccy: a['currency'] = ccy
            if (not a['ticker']) and tkr: a['ticker'] = tkr
            if (not a['exchange_country']) and exc: a['exchange_country'] = exc
        mm = IDX.secmeta.get(key)
        if mm:
            if a['sector'] in ('Unknown', 'Other') and mm[0]: a['sector'] = mm[0]
            if a['country'] == 'Unknown' and mm[1]: a['country'] = mm[1]
        a['value_cad'] += v; a['via'][via] += v
        if via == 'direct': a['direct_cad'] += v

    def explode(fk, v, depth, path, top):
        info = IDX.funds[fk]; m = fk.split(':')[0]
        c = cov.setdefault(fk, dict(fund=fk, issuer=info['issuer'], source=info['source'], url=info['url'], as_of=info['asof'], n_holdings=info['n'],
                                    raw_weight_sum=info['rawsum'], scope=info['coverage'], partial=info['partial'] == '1', exposure_cad=0.0, reached_via=set()))
        c['exposure_cad'] += v; c['reached_via'].add(' > '.join(path))
        rs = float(info['rawsum'] or 0) or 100.0
        part = info['partial'] == '1'
        div = rs if (normalize and not part and rs > 0) else 100.0
        rows = IDX.rows[fk]
        lev = rs > 115 and not part
        tally = 0.0
        for r in rows:
            try: w = float(r[7])
            except ValueError: continue
            sv = v * w / div; tally += w / div
            sub = resolve_fund_row(r, m)
            if sub and sub != fk and sub not in path and depth < maxdepth:
                if IDX.fund_has(sub): explode(sub, sv, depth + 1, path + [sub], top); continue
                else:
                    flags.append(dict(level='warn', fund=fk, msg='%s holds %s (%s) which we have no holdings for; kept as an opaque position' % (fk, sub, r[3])))
                    addsec('FUND:' + sub, sv, r[3] + ' [fund - not looked through]', r[4], '', '', 'Fund (not looked through)', r[11], top); continue
            if r[10].upper().startswith('CASH') or (r[3].upper().endswith(' CASH') and r[4].upper() in ('CAD', 'USD', 'EUR', 'GBP', 'JPY')) or r[3].strip().lower() in ('cash', 'cash & equivalents'):
                ccy = r[11] if r[3].upper() in ('CASH', 'CASH & EQUIVALENTS') or not r[4] else r[4]
                addsec('CASH:' + (ccy or 'UNK').upper(), sv, 'Cash (%s)' % (ccy or 'unknown ccy'), '', 'Cash', '', 'Cash', ccy, top); continue
            k = IDX.seckey(r[3], r[4], r[5], r[6], r[9], r[11], r[10], rexc(r))
            addsec(k, sv, r[3], r[4] if r[4] not in ('-',) else '', r[8], r[9], r[10], r[11], top, rexc(r))
        if part or tally < 0.995:
            rem = v * (1 - tally)
            if abs(rem) > 1e-9:
                addsec('UNDISCLOSED:' + fk, rem, 'Undisclosed remainder of %s (not in source data)' % fk, '', 'Undisclosed', 'Undisclosed', 'Undisclosed', 'Undisclosed', top)
        elif abs(v * (1 - tally)) > 1e-9:
            addsec('RESIDUAL:' + fk, v * (1 - tally), 'Weight rounding residual of %s (weights normalized to 100%%; tiny gap from rounding/unparsed rows)' % fk, '', 'Residual', 'Residual', 'Residual', 'Residual', top)
        if part: flags.append(dict(level='warn', fund=fk, msg='%s: partial data - %s; remainder %.1f%% shown as "Undisclosed"' % (fk, info['coverage'], 100 - float(info['rawsum'] or 0))))
        if lev: flags.append(dict(level='info', fund=fk, msg='%s: raw weights sum to %s%% (leveraged/derivative overlay?)%s' % (fk, info['rawsum'], ' - normalized to 100%' if normalize else '')))
        if info['source'].startswith('SEC'): flags.append(dict(level='info', fund=fk, msg='%s: SEC N-PORT quarter-end snapshot as of %s (not daily)' % (fk, info['asof'])))
        if info['issuer'] and m == 'CA' and 'cboe' in info['source'].lower(): flags.append(dict(level='warn', fund=fk, msg='%s: Cboe-only data source' % fk))

    total = 0.0
    for p in pos:
        kind, key, note = resolve_symbol(p['symbol'], p['ccy'])
        mkt = key.split(':')[0] if kind in ('fund', 'fundgap') else None
        ccy = p['ccy'] if p['ccy'] in ('CAD', 'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'HKD') else ''
        assumed = ''
        if not ccy:
            s, hint = parse_symbol(p['symbol'])
            ccy = 'USD' if s.endswith('.U') else ('CAD' if (mkt == 'CA' or hint == 'CA') else ('USD' if (mkt == 'US' or hint == 'US' or kind == 'stock') else 'CAD'))
            assumed = 'currency not in file; assumed %s from listing' % ccy
        pr = dict(symbol=p['symbol'], ticker=p.get('ticker', ''), exchange_country=p.get('exchange_country', ''), name=p['name'], quantity=p['qty'], market_value=p['mv'], currency=ccy, resolved_as=key or '', kind=kind, fx=None, fx_source='', value_cad=None, notes='; '.join(x for x in (note, assumed, p.get('mvnote', '')) if x))
        if p['mv'] is None:
            pr['notes'] = (pr['notes'] + '; ' if pr['notes'] else '') + 'NO MARKET VALUE (only quantity, no price) - excluded, map a value or price column'
            flags.append(dict(level='error', fund=p['symbol'], msg='%s has no market value - excluded (not guessed)' % p['symbol']))
            prows.append(pr); continue
        if ccy in manual_fx: rate = (manual_fx[ccy], 'user-entered', 'manual')
        else: rate = boc_rate(ccy, fetch_fx)
        if rate is None:
            pr['notes'] += '; no FX rate for %s - excluded' % ccy
            flags.append(dict(level='error', fund=p['symbol'], msg='No %s/CAD rate (Bank of Canada unreachable and no manual rate) - %s excluded' % (ccy, p['symbol'])))
            prows.append(pr); continue
        fxused[ccy] = rate
        v = p['mv'] * rate[0]; pr['fx'] = rate[0]; pr['fx_source'] = '%s (%s)' % (rate[2], rate[1]); pr['value_cad'] = v
        total += v
        top = p['symbol']
        if kind == 'fund':
            explode(key, v, 0, [key], top)
        elif kind == 'fundgap':
            flags.append(dict(level='warn', fund=key, msg=note))
            addsec('FUND:' + key, v, (p['name'] or key) + ' [ETF - holdings not in our data]', p['symbol'], '', '', 'Fund (not looked through)', ccy, top)
        else:
            nm = p['name']
            if key.startswith('T:'): flags.append(dict(level='info', fund=p['symbol'], msg='%s: %s' % (p['symbol'], note)))
            addsec(key, v, nm, p.get('ticker') or parse_symbol(p['symbol'])[0], '', '', 'Equity' if not key.startswith('T:') else '', ccy if key.startswith('T:') else '', 'direct', p.get('exchange_country', ''))
        prows.append(pr)
    # dedupe flags
    seen, fl = set(), []
    for f in flags:
        if f['msg'] not in seen: seen.add(f['msg']); fl.append(f)
    secs = sorted(agg.values(), key=lambda a: -a['value_cad'])
    for a in secs:
        a['pct'] = 100 * a['value_cad'] / total if total else 0
        a['via_map'] = dict(a['via'])
        a['via'] = '; '.join('%s %.0f' % (k, x) for k, x in a['via'].most_common(6))
    def by(f):
        d = defaultdict(float)
        for a in secs: d[a[f]] += a['value_cad']
        return [dict(bucket=k, value_cad=x, pct=100 * x / total if total else 0) for k, x in sorted(d.items(), key=lambda t: -t[1])]
    covl = []
    for c in sorted(cov.values(), key=lambda c: -c['exposure_cad']):
        c = dict(c); c['reached_via'] = ' | '.join(sorted(c['reached_via'])); covl.append(c)
    return dict(total_cad=total, positions=prows, securities=secs, sector=by('sector'), country=by('country'), asset_class=by('asset_class'),
                currency=by('currency'), coverage=covl, flags=fl, fx={k: dict(rate=r[0], date=r[1], source=r[2]) for k, r in fxused.items()},
                index=IDX.meta, normalized=normalize)

def to_csv(rows, cols):
    b = io.StringIO(); w = csv.writer(b); w.writerow(cols)
    for r in rows: w.writerow([('%.2f' % r[c]) if isinstance(r.get(c), float) else r.get(c, '') for c in cols])
    return b.getvalue()

EXPORTS = {
    'securities': ['name', 'ticker', 'exchange_country', 'key', 'sector', 'country', 'asset_class', 'currency', 'value_cad', 'pct', 'direct_cad', 'via'],
    'positions': ['symbol', 'ticker', 'exchange_country', 'name', 'quantity', 'market_value', 'currency', 'fx', 'fx_source', 'value_cad', 'kind', 'resolved_as', 'notes'],
    'sector': ['bucket', 'value_cad', 'pct'], 'country': ['bucket', 'value_cad', 'pct'], 'asset_class': ['bucket', 'value_cad', 'pct'], 'currency': ['bucket', 'value_cad', 'pct'],
    'coverage': ['fund', 'issuer', 'source', 'as_of', 'n_holdings', 'raw_weight_sum', 'scope', 'partial', 'exposure_cad', 'url', 'reached_via'],
    'flags': ['level', 'fund', 'msg'],
}

# ---------------- Portfolio X-ray: fund vectors, overlap, lookup, research ----------------
SKIP_PREFIX = ('CASH:', 'UNDISCLOSED:', 'FUND:', 'RESIDUAL:')
def is_sec(k): return not k.startswith(SKIP_PREFIX)
def keytype(k):
    return {'C': 'ID (CUSIP/ISIN)', 'I': 'ID (CUSIP/ISIN)', 'T': 'ticker only', 'N': 'name only'}.get(k.split(':')[0], 'other')

def classify_row(r):
    """-> ('fund', fk) | ('cash', ccy) | ('sec', key)"""
    sub = resolve_fund_row(r, r[0])
    if sub and sub != r[0] + ':' + r[1]: return 'fund', sub
    if r[10].upper().startswith('CASH') or (r[3].upper().endswith(' CASH') and r[4].upper() in ('CAD', 'USD', 'EUR', 'GBP', 'JPY')) or r[3].strip().lower() in ('cash', 'cash & equivalents'):
        return 'cash', r[11]
    return 'sec', IDX.seckey(r[3], r[4], r[5], r[6], r[9], r[11], r[10], rexc(r))

class Vectors:
    """Look-through weight vector (fraction of NAV) per fund, built once in the background."""
    def __init__(s):
        s.ready = threading.Event(); s.vec = {}; s.dvec = {}; s.info = {}; s.inv = defaultdict(list); s.err = None
        s.tick = {}   # ticker -> set(keys)
    def build(s):
        try:
            t0 = datetime.datetime.now()
            for fk in list(IDX.funds):
                if fk in IDX.rows: s.get(fk)
            for fk, v in s.vec.items():
                for k, w in v.items():
                    if w > 0: s.inv[k].append((fk, w))
            for k, inf in s.info.items():
                for t in inf['tk']: s.tick.setdefault(t, set()).add(k)
            print('Fund vectors ready: %d funds, %d securities in %.1fs' % (len(s.vec), len(s.info), (datetime.datetime.now() - t0).total_seconds()), flush=True)
        except Exception as e:
            import traceback; traceback.print_exc(); s.err = str(e)
        s.ready.set()
    def wait(s):
        s.ready.wait()
        if s.err: raise RuntimeError('vector build failed: ' + s.err)
    def note(s, k, r):
        i = s.info.get(k)
        if i is None:
            i = s.info[k] = dict(name=r[3], tk=set(), exc=Counter(), isin='', cusip='', sec='', cty='', ccy='', ac='')
        if r[4] and r[4] not in ('-', 'N/A') and len(i['tk']) < 6: i['tk'].add(r[4].upper())
        if rexc(r) and r[4]: i['exc'][rexc(r)] += 1
        if not i['isin'] and len(r[5]) == 12: i['isin'] = r[5]
        if not i['cusip'] and len(r[6]) == 9: i['cusip'] = r[6]
        for f, j in (('sec', 8), ('cty', 9), ('ccy', 11), ('ac', 10)):
            if not i[f] and r[j]: i[f] = r[j]
    def get(s, fk, path=()):
        if fk in s.vec: return s.vec[fk]
        info = IDX.funds[fk]; part = info['partial'] == '1'
        rs = float(info['rawsum'] or 0) or 100.0
        div = rs if (not part and rs > 0) else 100.0
        v = defaultdict(float); d = defaultdict(float)
        for r in IDX.rows.get(fk, []):
            try: w = float(r[7]) / div
            except ValueError: continue
            kind, k = classify_row(r)
            if kind == 'fund':
                if IDX.fund_has(k) and k not in path and len(path) < 6:
                    for kk, ww in s.get(k, path + (fk,)).items(): v[kk] += w * ww
                    d['FUND:' + k] += w
                else: v['FUND:' + k] += w; d['FUND:' + k] += w
            elif kind == 'cash': v['CASH:' + (k or 'UNK')] += w; d['CASH:' + (k or 'UNK')] += w
            else:
                v[k] += w; d[k] += w; s.note(k, r)
        v = dict(v); s.vec[fk] = v; s.dvec[fk] = dict(d)
        return v
VEC = None

def overlap(a, b):
    """sum over matched securities of min(weight_a, weight_b); cash/undisclosed/opaque excluded. Returns %."""
    if len(a) > len(b): a, b = b, a
    return 100 * sum(min(w, b[k]) for k, w in a.items() if w > 0 and k in b and b[k] > 0 and is_sec(k))

def match_quality(v):
    q = Counter()
    for k, w in v.items():
        if w > 0: q[keytype(k) if is_sec(k) else 'cash/undisclosed/opaque fund'] += w
    tot = sum(q.values()) or 1
    return {k: round(100 * x / tot, 1) for k, x in q.items()}

def fund_label(fk):
    m, t = fk.split(':', 1); u = IDX.univ.get((m, t)) or {}; f = IDX.funds.get(fk, {})
    mer = u.get('mer', '')
    try: mer = '%.2f%%' % (float(mer) * 100) if mer not in ('', None) else ''
    except ValueError: mer = ''
    return dict(fund=fk, market=m, ticker=t, name=u.get('name', ''), issuer=f.get('issuer', u.get('issuer', '')), source=f.get('source', ''),
                as_of=f.get('asof', ''), coverage=f.get('coverage', ''), partial=f.get('partial') == '1', url=f.get('url', ''),
                mer=mer, mer_source='Cboe Canada ETF list (expense_ratio)' if mer else 'not in our data')

def is_dup_class(fk):
    """Share-class / currency-class copies (XXX.U, XXX.F, XXX.B ...) of a fund we also have."""
    m, t = fk.split(':', 1)
    if '.' in t and IDX.fund_has(m + ':' + t.split('.')[0]): return True
    u = IDX.univ.get((m, t))
    return bool(u and u['base'] != t and IDX.fund_has(m + ':' + u['base']))

def nearest(target, exclude=(), market=None, n=10, min_cov=False):
    VEC.wait(); sc = defaultdict(float)
    for k, w in target.items():
        if w <= 0 or not is_sec(k): continue
        for fk, fw in VEC.inv.get(k, ()):
            sc[fk] += min(w, fw)
    out = []
    for fk, x in sorted(sc.items(), key=lambda t: -t[1]):
        if fk in exclude or is_dup_class(fk) or (market and not fk.startswith(market + ':')): continue
        if any(fk.split(':')[1].split('.')[0] == e.split(':')[1].split('.')[0] and fk[:2] == e[:2] for e in exclude): continue
        d = fund_label(fk); d['overlap_pct'] = round(100 * x, 2); out.append(d)
        if len(out) >= n: break
    return out

def resolve_tickers(tk):
    res, errs = [], []
    for t in [x.strip() for x in re.split(r'[,\s;]+', tk or '') if x.strip()]:
        kind, key, note = resolve_symbol(t, '')
        if kind == 'fund': res.append((t, key, note))
        else: errs.append('%s: %s' % (t, note or 'not an ETF with holdings in our index'))
    return res, errs

def topexc(i): return i['exc'].most_common(1)[0][0] if i and i.get('exc') else ''
def secname(k):
    i = VEC.info.get(k) if VEC else None
    return (i['name'] if i else k), (', '.join(sorted(i['tk'])) if i else '')

def overlap_report(tickers, weights=None):
    VEC.wait(); funds, errs = resolve_tickers(tickers)
    if len(funds) < 2: raise ValueError('Need at least 2 ETFs with holdings in the index. ' + '; '.join(errs))
    fks = [f[1] for f in funds]; vs = {fk: VEC.get(fk) for fk in fks}
    ws = [float(x) for x in re.split(r'[,\s;]+', weights) if x] if weights else []
    if len(ws) != len(fks) or sum(ws) <= 0: ws = [1.0] * len(fks); wnote = 'equal weights'
    else: wnote = 'user weights ' + ', '.join('%g' % x for x in ws)
    ws = [x / sum(ws) for x in ws]
    pairs = []
    for i in range(len(fks)):
        for j in range(i + 1, len(fks)):
            pairs.append(dict(a=fks[i], b=fks[j], overlap_pct=round(overlap(vs[fks[i]], vs[fks[j]]), 2)))
    matrix = [dict(fund=a, **{b: (100.0 if a == b else round(overlap(vs[a], vs[b]), 2)) for b in fks}) for a in fks]
    keys = Counter(k for fk in fks for k, w in vs[fk].items() if w > 0 and is_sec(k))
    common = []
    for k, c in keys.items():
        if c < 2: continue
        nm, tk = secname(k)
        row = dict(security=nm, tickers=tk, exchange_country=topexc(VEC.info.get(k)), key=k, match=keytype(k), n_funds=c)
        for fk in fks: row[fk] = round(100 * vs[fk].get(k, 0), 4)
        row['min_weight'] = round(min(100 * vs[fk].get(k, 0) for fk in fks), 4)
        common.append(row)
    common.sort(key=lambda r: (-r['n_funds'], -min([r[f] for f in fks if r[f] > 0] or [0])))
    comb = defaultdict(float)
    for fk, w in zip(fks, ws):
        for k, x in vs[fk].items(): comb[k] += w * x
    sug = nearest(comb, exclude=set(fks), n=10)
    warns = ['%s and %s overlap %.0f%%' % (p['a'].split(':')[1], p['b'].split(':')[1], p['overlap_pct']) for p in pairs if p['overlap_pct'] >= 40]
    return dict(funds=[dict(input=t, note=n, **fund_label(k), match_quality=match_quality(vs[k]), n_securities=sum(1 for x in vs[k] if is_sec(x))) for t, k, n in funds],
                errors=errs, pairs=pairs, matrix=matrix, matrix_cols=['fund'] + fks, common=common[:500], common_total=len(common),
                suggestions=sug, combined_weights=wnote, warnings=warns,
                fee_note='Fees: MER/expense ratio is only available for Canadian ETFs (from the Cboe Canada ETF list). US funds (mostly SEC N-PORT data) have no fee data here, so no cost comparison is made.')

def lookup(q='', key=''):
    VEC.wait()
    if not key:
        qq = (q or '').strip().upper()
        if not qq: raise ValueError('Enter a name, ticker, ISIN or CUSIP')
        cands = set()
        ik = idkey(qq if len(qq) == 12 else '', qq if len(qq) == 9 else '')
        if ik and ik in VEC.info: cands.add(ik)
        nt, ne = parse_user_symbol(qq)          # 'TSLA US', 'RY.TO', 'RY CN', 'BRK/B' -> canonical ticker (+ listing country)
        if True:
            hits = set(VEC.tick.get(qq, ())) | set(VEC.tick.get(nt, ()))
            if ne: hits = {k for k in hits if ne in VEC.info[k]['exc']} or hits
            cands |= hits
        idc = [k for k in cands if k[:2] in ('C:', 'I:')]
        if not ik and len(idc) >= 1:
            best = max(idc, key=lambda k: len(VEC.inv.get(k, ())))
            if len(idc) == 1 or len(VEC.inv.get(best, ())) > 3 * max(len(VEC.inv.get(k, ())) for k in idc if k != best): ik = best; cands = {best}
        if not ik and (not cands or len(qq) >= 4):
            nq = normname(qq)
            if nq:
                for k, i in VEC.info.items():
                    if nq in normname(i['name']):
                        cands.add(k)
                        if len(cands) > 300: break
        out = []
        for k in cands:
            i = VEC.info[k]; n = len(VEC.inv.get(k, ()))
            out.append(dict(key=k, name=i['name'], tickers=', '.join(sorted(i['tk'])), exchange_country=topexc(i), isin=i['isin'], cusip=i['cusip'], country=ncty(i['cty']), asset_class=nac(i['ac']), match=keytype(k), n_funds=n))
        out.sort(key=lambda r: -r['n_funds'])
        if len(out) == 1 or (out and len(out) > 1 and ik and out[0]['key'] == ik): key = out[0]['key']
        else: return dict(candidates=out[:60])
    i = VEC.info.get(key, dict(name=key, tk=set(), isin='', cusip='', cty='', ac=''))
    rows = []
    for fk, w in VEC.inv.get(key, ()):
        if is_dup_class(fk): continue
        d = fund_label(fk); d['weight_pct'] = round(100 * w, 4)
        d['held'] = 'direct' if key in VEC.dvec.get(fk, {}) else 'via underlying fund'
        rsum = float(IDX.funds[fk]['rawsum'] or 0)
        d['note'] = ('leveraged/derivative fund (raw weights sum %.0f%%)' % rsum) if rsum > 115 else ('partial/top-N data' if d['partial'] else '')
        rows.append(d)
    rows.sort(key=lambda r: -r['weight_pct'])
    return dict(security=dict(key=key, name=i['name'], tickers=', '.join(sorted(i['tk'])), exchange_country=topexc(i), isin=i['isin'], cusip=i['cusip'], match=keytype(key)),
                funds=rows, n_CA=sum(1 for r in rows if r['market'] == 'CA'), n_US=sum(1 for r in rows if r['market'] == 'US'),
                note='Weights are look-through % of each fund (fund-of-funds expanded). Share-class duplicates (.U/.F etc.) hidden. Partial/top-N funds only show disclosed positions.')

def equivalents(ticker, n=10):
    VEC.wait(); fs, errs = resolve_tickers(ticker)
    if not fs: raise ValueError('; '.join(errs) or 'ticker not found')
    fk = fs[0][1]; other = 'US' if fk.startswith('CA') else 'CA'
    v = VEC.get(fk)
    return dict(fund=dict(**fund_label(fk), match_quality=match_quality(v)), other_market=other,
                matches=nearest(v, exclude={fk}, market=other, n=n), same_market=nearest(v, exclude={fk}, market=fk[:2], n=5),
                tax_note='General information only: US-listed and Canadian-listed ETFs are taxed differently for Canadian investors (e.g. US withholding tax on dividends, which is generally treated differently in an RRSP than in a TFSA or non-registered account; US-listed funds also mean USD conversion and possible US estate-tax considerations). This is not personalized tax or investment advice; consult a qualified professional.',
                fee_note='MER shown only where present in the Cboe Canada ETF list; US fee data is not in our index.')

RESEARCH = {}
def research(market):
    VEC.wait()
    if market in RESEARCH: return RESEARCH[market]
    cnt, agg = Counter(), defaultdict(float); stats = []
    for fk, d in VEC.dvec.items():
        if not fk.startswith(market + ':') or is_dup_class(fk): continue
        for k, w in d.items():
            if is_sec(k) and w > 0: cnt[k] += 1; agg[k] += w
        v = [w for k, w in VEC.vec[fk].items() if is_sec(k) and w > 0]
        v.sort(reverse=True); f = fund_label(fk)
        stats.append(dict(fund=fk, name=f['name'], issuer=f['issuer'], source=f['source'], as_of=f['as_of'], partial=f['partial'],
                          n_holdings=len(v), top10_pct=round(100 * sum(v[:10]), 2), hhi=round(sum((100 * w) ** 2 for w in v), 1),
                          effective_n=round(1 / sum(w * w for w in v), 1) if v else 0))
    def mk(src, by):
        out = []
        for k, x in src.most_common(100) if isinstance(src, Counter) else sorted(src.items(), key=lambda t: -t[1])[:100]:
            nm, tk = secname(k)
            out.append(dict(security=nm, tickers=tk, exchange_country=topexc(VEC.info.get(k)), key=k, match=keytype(k), n_etfs=cnt[k], aggregate_weight_pct=round(100 * agg[k], 2)))
        return out
    stats.sort(key=lambda r: -r['top10_pct'])
    r = dict(market=market, by_count=mk(cnt, 'count'), by_weight=mk(agg, 'weight'), stats=stats,
             note='Counts use each fund\'s DIRECT holdings (not fund-of-funds look-through) so wrappers are not double counted; share-class duplicates excluded. Aggregate weight = sum of the security\'s % across those funds (unweighted by fund size; AUM is not used). HHI = sum of squared look-through weights in % (0-10,000); effective N = 1/sum(w^2). Partial/top-N funds understate holding counts.')
    RESEARCH[market] = r; return r

SNAP = None
def snapshots():
    global SNAP
    if SNAP is not None: return SNAP
    p = os.path.join(DATA, 'snapshots_prior.csv.gz'); out = []
    if os.path.exists(p):
        prior = defaultdict(list)
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            for r in csv.DictReader(fh): prior[r['m'] + ':' + r['fund']].append(r)
        for fk, ps in prior.items():
            if fk not in IDX.funds: continue
            cur = {}
            for r in IDX.rows.get(fk, []):
                try: cur[(r[5] or normname(r[3]))] = (r[3], float(r[7]))
                except ValueError: pass
            curn = {normname(v[0]): v for v in cur.values()}
            for r in ps:
                c = cur.get(r['isin']) if r['isin'] else None
                c = c or curn.get(normname(r['name']))
                if not c: continue
                out.append(dict(fund=fk, holding=r['name'], prior_as_of=r['asof'], prior_list=r['lt'], prior_weight=float(r['w']),
                                current_as_of=IDX.funds[fk]['asof'], current_list=IDX.funds[fk].get('latest_list_type', ''), current_weight=c[1], change_pp=round(c[1] - float(r['w']), 3)))
    SNAP = out; return out

# ---- portfolio-level X-ray on top of analyze() ----
BANKS = [('Royal Bank of Canada', r'ROYAL BANK OF CANADA'), ('TD Bank', r'TORONTO[- ]DOMINION'), ('Scotiabank', r'BANK OF NOVA SCOTIA'),
         ('BMO', r'BANK OF MONTREAL'), ('CIBC', r'CANADIAN IMPERIAL BANK'), ('National Bank', r'NATIONAL BANK OF CANADA')]
CTYCCY = {'United States': 'USD', 'Canada': 'CAD', 'United Kingdom': 'GBP', 'Japan': 'JPY', 'Switzerland': 'CHF', 'Australia': 'AUD', 'Hong Kong': 'HKD',
          'China': 'CNY', 'Taiwan': 'TWD', 'South Korea': 'KRW', 'India': 'INR', 'Sweden': 'SEK', 'Denmark': 'DKK', 'Norway': 'NOK', 'Brazil': 'BRL',
          'Singapore': 'SGD', 'Israel': 'ILS', 'South Africa': 'ZAR', 'Mexico': 'MXN', **{c: 'EUR' for c in ('Germany', 'France', 'Netherlands', 'Ireland', 'Spain', 'Italy', 'Finland', 'Belgium', 'Austria', 'Portugal', 'Luxembourg')}}
DEFAULT_T = dict(stock=5, sector=30, country=60, banks=15, overlap=40, equity=None, fixed_income=None, cash=None, max_stock=None, max_country=None, max_sector=None)

def issuer_of(k):
    if k.startswith('C:'): return 'ISS:' + k[2:8]
    if k.startswith('N:'): return k
    i = VEC.info.get(k) if VEC and VEC.ready.is_set() else None
    return 'ISSN:' + normname(i['name']) if i else k

def aclass(a):
    return a if a in ('Equity', 'Fixed Income', 'Cash & Equivalents') else ('Cash & Equivalents' if a == 'Cash' else 'Other / unclassified')

def xray(res, th=None):
    t = dict(DEFAULT_T); t.update({k: (float(v) if v not in (None, '') else None) for k, v in (th or {}).items() if k in DEFAULT_T})
    tot = res['total_cad'] or 1; secs = res['securities']
    real = [a for a in secs if is_sec(a['key'])]
    # issuers
    iss = {}
    for a in real:
        g = iss.setdefault(issuer_of(a['key']), dict(issuer='', tickers=set(), value_cad=0.0, n_lines=0, classes=set(), country=a['country'], sector=a['sector'], top=0))
        g['value_cad'] += a['value_cad']; g['n_lines'] += 1; g['classes'].add(a['asset_class'])
        if a['ticker']: g['tickers'].add(a['ticker'])
        if a['value_cad'] > g['top']: g['top'] = a['value_cad']; g['issuer'] = a['name']; g['country'] = a['country']; g['sector'] = a['sector']
    issl = sorted(iss.values(), key=lambda g: -g['value_cad'])
    top = [dict(rank=i + 1, company=g['issuer'], tickers=', '.join(sorted(g['tickers']))[:60], sector=g['sector'], country=g['country'], asset_classes=', '.join(sorted(g['classes'])),
                lines=g['n_lines'], value_cad=g['value_cad'], pct=100 * g['value_cad'] / tot) for i, g in enumerate(issl[:25])]
    alerts = []
    def al(kind, msg, val, lim): alerts.append(dict(type=kind, msg=msg, value_pct=round(val, 2), threshold_pct=lim))
    for r in top:
        if r['pct'] > t['stock']: al('single company', '%s is %.1f%% of your portfolio (look-through, all funds + direct)' % (r['company'], r['pct']), r['pct'], t['stock'])
    known = [b for b in res['sector'] if b['bucket'] not in ('Unknown', 'Undisclosed', 'Cash', 'Other')]
    for b in known:
        if b['pct'] > t['sector']: al('sector', 'Sector %s is %.1f%%' % (b['bucket'], b['pct']), b['pct'], t['sector'])
    for b in res['country']:
        if b['bucket'] not in ('Unknown', 'Undisclosed') and b['pct'] > t['country']: al('country', '%s is %.1f%% of the portfolio' % (b['bucket'], b['pct']), b['pct'], t['country'])
    us = next((b['pct'] for b in res['country'] if b['bucket'] == 'United States'), 0.0)
    bank = []
    for nm, rx in BANKS:
        v = sum(a['value_cad'] for a in real if re.search(rx, a['name'] or '', re.I) and a['asset_class'] in ('Equity', 'Unknown'))
        if v: bank.append(dict(bank=nm, value_cad=v, pct=100 * v / tot))
    bsum = sum(b['pct'] for b in bank)
    if bsum > t['banks']: al('issuer group', 'Big-6 Canadian banks (equity) combined = %.1f%%' % bsum, bsum, t['banks'])
    # currency
    cur = defaultdict(lambda: [0.0, Counter()])
    hedged_pos = {}
    for p in res['positions']:
        if p['kind'] == 'fund':
            u = IDX.univ.get(tuple(p['resolved_as'].split(':', 1))) or {}
            txt = ' '.join([u.get('name', ''), p.get('name') or '', IDX.funds.get(p['resolved_as'], {}).get('url', '').replace('-', ' ')])
            if re.search(r'hedged', txt, re.I) and not re.search(r'unhedged|non[- ]hedged', txt, re.I): hedged_pos[p['symbol']] = (u.get('name', '') + ' [hedging detected from fund name/description/URL]')
    hedgedview = defaultdict(float)
    for a in secs:
        c = (a['currency'] or '').upper()
        if a['key'].startswith('CASH:'): meth = 'cash currency'; c = c or a['key'][5:]
        elif c and c not in ('UNKNOWN', 'UNDISCLOSED', '-'): meth = 'security currency in holdings file'
        elif a['country'] in CTYCCY: c = CTYCCY[a['country']]; meth = 'inferred from country'
        else: c = 'Unknown'; meth = 'no currency/country data'
        if c in ('UNDISCLOSED',): c = 'Undisclosed'
        cur[c][0] += a['value_cad']; cur[c][1][meth] += a['value_cad']
        for via, x in a.get('via_map', {}).items():
            hedgedview['CAD (hedged by fund)' if (via in hedged_pos and c not in ('CAD', 'Unknown', 'Undisclosed')) else c] += x
    ccy = [dict(currency=k, value_cad=v[0], pct=100 * v[0] / tot, method='; '.join('%s %.0f%%' % (m, 100 * x / v[0]) for m, x in v[1].most_common()) if v[0] else '') for k, v in sorted(cur.items(), key=lambda t: -t[1][0])]
    ccyh = [dict(currency=k, value_cad=v, pct=100 * v / tot) for k, v in sorted(hedgedview.items(), key=lambda t: -t[1])]
    # fund overlap matrix
    fpos = defaultdict(float)
    for p in res['positions']:
        if p['kind'] == 'fund' and p['value_cad']: fpos[p['resolved_as']] += p['value_cad']
    fks = sorted(fpos, key=lambda k: -fpos[k]); matrix, owarn, pairs = [], [], []
    if len(fks) >= 2:
        VEC.wait(); vs = {k: VEC.get(k) for k in fks}
        for a in fks:
            row = dict(fund=a)
            for b in fks: row[b] = 100.0 if a == b else round(overlap(vs[a], vs[b]), 1)
            matrix.append(row)
        for i, a in enumerate(fks):
            for b in fks[i + 1:]:
                o = overlap(vs[a], vs[b]); pairs.append(dict(a=a, b=b, overlap_pct=round(o, 2), value_a=fpos[a], value_b=fpos[b]))
                if o >= t['overlap']:
                    owarn.append('%s and %s overlap %.0f%%' % (a.split(':')[1], b.split(':')[1], o))
                    al('fund overlap', '%s and %s overlap %.0f%% (sum of min weights)' % (a.split(':')[1], b.split(':')[1], o), o, t['overlap'])
    # targets
    acl = defaultdict(float)
    for b in res['asset_class']: acl[aclass(b['bucket'])] += b['value_cad']
    tgt = []
    for k, nm in (('equity', 'Equity'), ('fixed_income', 'Fixed Income'), ('cash', 'Cash & Equivalents')):
        act = 100 * acl.get(nm, 0) / tot
        tgt.append(dict(item=nm, kind='asset class target', actual_pct=round(act, 2), target_pct=t[k], gap_cad=(t[k] / 100 * tot - acl.get(nm, 0)) if t[k] is not None else None,
                        status='' if t[k] is None else ('OK' if abs(act - t[k]) <= 2 else 'off target (>2pp)')))
    tgt.append(dict(item='Other / unclassified', kind='info', actual_pct=round(100 * acl.get('Other / unclassified', 0) / tot, 2), target_pct=None, gap_cad=None, status='includes Undisclosed, opaque funds, derivatives, real estate etc.'))
    breaches = []
    for k, lab, rows, f in (('max_stock', 'company', top, 'company'), ('max_country', 'country', [dict(x=b['bucket'], pct=b['pct']) for b in res['country'] if b['bucket'] not in ('Unknown', 'Undisclosed')], 'x'),
                            ('max_sector', 'sector', [dict(x=b['bucket'], pct=b['pct']) for b in known], 'x')):
        if t[k] is None: continue
        for r in rows:
            if r['pct'] > t[k]: breaches.append(dict(limit=lab, item=r[f], actual_pct=round(r['pct'], 2), limit_pct=t[k], excess_cad=(r['pct'] - t[k]) / 100 * tot))
    # composition per input position -> rebalancing vehicles
    comp = defaultdict(lambda: defaultdict(float)); pv = {}
    for p in res['positions']:
        if p['value_cad']: pv[p['symbol']] = pv.get(p['symbol'], 0) + p['value_cad']
    for a in secs:
        for via, x in a.get('via_map', {}).items():
            comp['DIRECT:' + a['ticker'] if via == 'direct' else via][aclass(a['asset_class'])] += x
    for p in res['positions']:
        if p['kind'] != 'fund' and p['value_cad']: pv['DIRECT:' + parse_symbol(p['symbol'])[0]] = pv.pop(p['symbol'], p['value_cad'])
    reb = []
    for r in tgt:
        if r['gap_cad'] is None or abs(r['gap_cad']) < 1: continue
        best = max(((v, c[r['item']] / pv[v]) for v, c in comp.items() if v in pv and pv[v] > 0), key=lambda z: z[1], default=(None, 0))
        if r['item'] == 'Cash & Equivalents' and best[1] < 0.5:
            reb.append(dict(asset_class=r['item'], gap_cad=r['gap_cad'], vehicle='cash (add/withdraw)', vehicle_purity_pct=100.0, suggested_trade_cad=r['gap_cad'], note='no existing position is mostly cash'))
            continue
        if not best[0] or best[1] < 0.05:
            reb.append(dict(asset_class=r['item'], gap_cad=r['gap_cad'], vehicle='(none of your holdings)', vehicle_purity_pct=0, suggested_trade_cad=None, note='no existing holding has meaningful exposure to this class - a new holding would be needed'))
            continue
        trade = r['gap_cad'] / best[1]
        if trade < -pv[best[0]]: trade = -pv[best[0]]
        reb.append(dict(asset_class=r['item'], gap_cad=r['gap_cad'], vehicle=best[0].replace('DIRECT:', ''), vehicle_purity_pct=round(100 * best[1], 1), suggested_trade_cad=trade,
                        note='trade = gap / purity (vehicle = your holding with the highest share of this class); first-order only, ignores side effects on other classes'))
    mq = Counter()
    for a in secs: mq[keytype(a['key']) if is_sec(a['key']) else ('cash' if a['key'].startswith('CASH:') else 'undisclosed / opaque fund')] += a['value_cad']
    return dict(thresholds=t, top_companies=top, top_securities=[dict(rank=i + 1, security=a['name'], ticker=a['ticker'], exchange_country=a.get('exchange_country', ''), key=a['key'], match=keytype(a['key']), sector=a['sector'], country=a['country'], value_cad=a['value_cad'], pct=a['pct']) for i, a in enumerate(real[:25])],
                alerts=alerts, us_share_pct=round(us, 2), banks=bank, banks_total_pct=round(bsum, 2), currency=ccy, currency_hedged=ccyh, hedged_funds=[dict(position=k, fund_name=v) for k, v in hedged_pos.items()],
                overlap_matrix=matrix, overlap_cols=['fund'] + fks, overlap_pairs=pairs, overlap_warnings=owarn, targets=tgt, breaches=breaches, rebalance=reb,
                match_quality=[dict(method=k, value_cad=v, pct=100 * v / tot) for k, v in mq.most_common()],
                reconcile=dict(total_cad=res['total_cad'], sum_securities_cad=sum(a['value_cad'] for a in secs), sum_positions_cad=sum(p['value_cad'] or 0 for p in res['positions'])))

XEXPORTS = {'top_companies': None, 'top_securities': None, 'alerts': None, 'currency': None, 'currency_hedged': None, 'banks': None, 'overlap_pairs': None, 'targets': None, 'breaches': None, 'rebalance': None, 'match_quality': None}
def rows_csv(rows, cols=None):
    cols = cols or (list(rows[0].keys()) if rows else [])
    b = io.StringIO(); w = csv.writer(b); w.writerow(cols)
    for r in rows: w.writerow([('%.4f' % r.get(c)) if isinstance(r.get(c), float) else ('' if r.get(c) is None else r.get(c)) for c in cols])
    return b.getvalue()
def xlsx_bytes(sheets):
    import openpyxl
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(re.sub(r'[\[\]:*?/\\]', '_', name)[:31])
        if isinstance(rows, dict): rows = [rows]
        if not rows: ws.append(['(no rows)']); continue
        cols = list(rows[0].keys()) if isinstance(rows[0], dict) else None
        if cols:
            ws.append(cols)
            for r in rows: ws.append([v if isinstance(v, (int, float)) or v is None else str(v) for v in (r.get(c) for c in cols)])
        else:
            for r in rows: ws.append([str(x) for x in r])
    b = io.BytesIO(); wb.save(b); return b.getvalue()
DISCLAIMER = 'Informational only - not investment, tax or financial advice. Holdings come from issuer websites, SEC N-PORT filings or Cboe Canada as labelled, each with its own as-of date; they can be stale or incomplete.'

# ---------------- Best Canadian ETFs screener (rules-based) ----------------
SCR_SRC = 'Cboe Canada / Trackinsight fund data (fund_overview + performance), NAV/AUM as of the date shown; holdings-based checks use our holdings index'
LEVMAP = {}
_GENERIC = set('canadian canada international global world equity equities banks bank telecom covered call enhanced equal weight index shares share corp corporation group holdings limited ltd inc class common america american national financial energy income growth core united states emerging markets developed'.split())
def lev_info(r, rs=None):
    """-> ('X', reason) to exclude, or (ratio or None, evidence). Moderate leverage 1.0<x<=1.4 is allowed (badged)."""
    nm = r['name']; desc = (r.get('description') or '') + ' ' + (r.get('exposure') or '')
    _inv = re.search(r'\binverse\b|\bbear\b|-\s?\d(\.\d+)?x\b|savvyshort|megashort|(?<!long/)(?<!long )(?<!ultra-)(?<!ultra )\bshort\b(?!.?(term|duration|maturity|-term|corporate|federal|provincial|bond|government))', nm, re.I)
    if _inv: return 'X', 'inverse/short (name: %s)' % _inv.group(0)
    if re.search(r'inverse exposure|\(-\d', desc, re.I): return 'X', 'inverse (description)'
    ratio = ev = None
    for src, t in (('name', nm), ('description', desc)):
        m = re.search(r'\b(\d(?:\.\d+)?)\s?x\b', t, re.I) or re.search(r'\((\d(?:\.\d+)?)x\)', t, re.I)
        if m and 1 < float(m.group(1)) <= 5: ratio, ev = float(m.group(1)), '%s: %s' % (src, m.group(0)); break
        m = re.search(r'\b(1[1-9][0-9](?:\.\d+)?)\s?%\s?(?:exposure|leverage)', t, re.I) or re.search(r'\b(2[0-9]|3[0-9])(?:\.\d+)?\s?%\s?(?:leverage|borrowing)', t, re.I)
        if m:
            v = float(m.group(1)); ratio = v / 100 if v > 100 else 1 + v / 100; ev = '%s: %s' % (src, m.group(0)); break
    if ratio is None and re.search(r'betapro|\bbull\b|ultrapro|\bdaily\b', nm, re.I): return 'X', 'daily leveraged product (name: %s), >=2x' % re.search(r'betapro|\bbull\b|ultrapro|\bdaily\b', nm, re.I).group(0)
    if ratio is None and re.search(r'^(hamilton|global x|harvest)\b.*\benhanced\b', nm, re.I) and not re.search(r'enhanced (yield|premium|equity income fund)', nm, re.I) or (ratio is None and re.search(r'^harvest .*enhanced', nm, re.I)):
        ratio, ev = 1.25, 'issuer naming convention: %s "Enhanced" funds use ~25%% leverage (1.25x)' % r['issuer']

    if ratio is None and re.search(r'\b(leveraged|levered)\b', nm, re.I): return 'X', 'leveraged, ratio not stated (assumed >=2x)'
    if ratio is None and re.search(r'\b(uses?|using|employs?|employing|applies|applying|delivers|modest|moderate|light|cash|portfolio|with) (modest |moderate |light |)leverage\b', desc, re.I) and not re.search(r'(no|not|without|low|financial|un)[ -]?(use )?(of )?leverag', desc, re.I): ratio, ev = 'n/s', 'description mentions leverage, ratio not stated: ' + re.search(r'.{0,50}\bleverag.{0,40}', desc, re.I).group(0)
    if isinstance(ratio, float) and ratio >= 1.45: return 'X', 'leverage %.2gx (%s)' % (ratio, ev)
    return ratio, ev

LEV_RX = re.compile(r'\b(2x|3x|1\.25x|1\.33x|1\.5x|-1x|-2x|-3x|leveraged|levered|inverse|bull|bear|ultra(pro)?|daily)\b|betapro|\bshort\b(?!.?(term|duration|maturity|-term))', re.I)
STOCK_RX = re.compile(r'\b(apple|tesla|nvidia|microsoft|amazon|alphabet|google|meta platforms|netflix|broadcom|amd|advanced micro|coinbase|berkshire|shopify|palantir|microstrategy|strategy inc|eli lilly|costco|walmart|jpmorgan|visa|mastercard|uber|super micro|intel|oracle|salesforce|exxon|unitedhealth|royal bank|toronto-dominion|enbridge|barrick|agnico|constellation software|brookfield|suncor|canadian natural)\b|yield shares|income shares|single[- ]stock', re.I)
BTC_RX = re.compile(r'bitcoin|\bbtc\b', re.I); ALT_RX = re.compile(r'equity|portfolio|\+|balanced|ether|ethereum|solana|xrp|crypto|blockchain|miner|digital asset|multi|covered|yield|income|premium|enhanced|option|cardano|litecoin|altcoin', re.I)
COM = [('Gold & precious metals', r'\bgold\b|silver|precious|platinum|palladium|bullion'), ('Uranium / nuclear', r'uranium|nuclear'),
       ('Lithium / battery metals', r'lithium|battery'), ('Oil & gas', r'\boil\b|crude|natural gas|oil & gas|energy producers|s&p/tsx capped energy|canadian energy'),
       ('Mining / metals', r'mining|miners|base metals|global metals|copper|critical minerals|commodit|natural resources?|resources')]
FAM = [('Nasdaq-100', r'nasdaq[- ]?100(?!.{0,20}tech)|nasdaq 100'), ('S&P 500', r's&p 500(?! (equal|low vol|dividend|growth|value|high|quality|momentum|top))|us 500|united states 500|us large cap index'),
       ('US total market', r'total (us |u\.s\. )?(stock )?market|crsp us total|s&p total market|us total|s&p us total'),
       ('Canada broad', r'tsx (capped )?composite|s&p/tsx 60|tsx 60|canada broad|ftse canada all cap|canada (large|all)[- ]cap|msci canada (imi )?(index)?|canadian large (and|&) mid'),
       ('Developed ex-North America', r'eafe|developed (markets )?ex|ex north america|international developed|ftse developed'),
       ('Emerging markets', r'emerging markets?(?! (bond|debt))'),
       ('World / all-country', r'all[- ]world|acwi|all country|msci world|ftse global all|global equity index|(all[- ]equity|core equity|growth) etf portfolio|all-in-one|equity etf portfolio'),
       ('US large/mid broad', r'large (and|&) mid[- ]cap (us|u\.s\.) equities|large cap us equities|us equity index|u\.s\. equity index'),
       ('Canada broad', r'large (and|&) mid[- ]cap canadian equities|large cap canadian equities')]
OPT_RX = re.compile(r'covered call|call[- ]writing|option[- ]writing|options? (income|strategy|overlay)|premium (yield|income)|enhanced yield|buy[- ]?write|put[- ]writ|yield maximizer|\bmax(imum)? (income|yield)|\b[A-Z]?MAX\b|\bcall\b|\bpremium\b|ultra ?yield|autocallable|structured equity yield|enhanced (equity )?income|^harvest .*income|^purpose .*equity income', re.I)  # issuer families whose Income/UltraYield funds write options per their stated strategy
DIV_RX = re.compile(r'dividend|high yield|yield|income|aristocrat|distribution', re.I)
INC_RX = OPT_RX
SECT_RX = re.compile(r'\b(banks?|financials?|technology|tech|health ?care|healthcare|utilit(y|ies)|real estate|reits?|telecom|communication|consumer|industrials?|semiconductors?|biotech|cyber|cloud|artificial intelligence|ai|robotics|infrastructure|insurance|pipelines?|clean energy|innovation|fintech|gaming|cannabis|water|defen[cs]e|aerospace|transport|energy|materials|software|internet|genomics|esports|metaverse)\b', re.I)
CATS = [('A', 'Core broad-market index'), ('B', 'Income: option-writing / covered call (broad index / multi-sector)'), ('C', 'Commodities & resources'), ('D', 'Bitcoin (spot only) - HIGH RISK'), ('E', 'Cash & cash equivalents (HISA, money market, ultra-short T-bill)'), ('F', 'Bonds / fixed income'), ('G', 'Balanced / asset-allocation'), ('H', 'Thematic / other multi-sector'), ('S', 'Single-sector (deprioritized)')]
DEFW = {'A': dict(perf=40, fee=35, yld=0, aum=15, track=10), 'B': dict(perf=30, fee=20, yld=35, aum=10, track=5), 'C': dict(perf=40, fee=30, yld=0, aum=20, track=10),
        'D': dict(perf=30, fee=50, yld=0, aum=15, track=5), 'S': dict(perf=40, fee=35, yld=0, aum=15, track=10),
        'E': dict(vol=30, yld=30, fee=25, cons=10, aum=3, track=2, perf=0),
        'F': dict(perf=25, fee=35, yld=25, aum=10, track=5), 'G': dict(perf=40, fee=35, yld=0, aum=15, track=10), 'H': dict(perf=40, fee=35, yld=0, aum=15, track=10)}
BAL_RX = re.compile(r'\b(core (equity|growth|balanced|conservative|income balanced) etf|balanced|portfolio|asset allocation|all[- ]equity|all[- ]in[- ]one|growth etf|conservative etf|income etf portfolio|tactical asset)\b', re.I)
BOND_RX = re.compile(r'\b(bond|fixed income|treasury|government|corporate|mortgage|preferred|floating rate|high yield|debt|credit|aggregate|maturity|laddered)\b', re.I)
for _c in DEFW: DEFW[_c].setdefault('vol', 0); DEFW[_c].setdefault('cons', 0)
CASH_RX = re.compile(r'high interest savings|\bsavings\b|money market|t-?bill|treasury bill|cash management|premium cash|\bcash (maximizer|etf|account)|0-3 month|cash maximizer|\bcash fund\b|ultra[- ]short[- ]term (government|treasury|t-bill)', re.I)
CASH_BAD = re.compile(r'yield maximizer|premium yield|bond|corporate|credit|high yield|floating|mortgage|aggregate|income fund|covered|\b(1|2|3|5)-?\d* ?(year|yr)', re.I)
def fnum(x):
    try: return float(x) if x not in ('', 'None', None) else None
    except ValueError: return None
SCR = None
def scr_rows():
    global SCR
    if SCR is None:
        p = os.path.join(DATA, 'ca_screener.csv'); SCR = list(csv.DictReader(open(p, encoding='utf-8'))) if os.path.exists(p) else []
    return SCR

def classify_ca(r):
    """-> (cat or 'X:<reason>', group, evidence)"""
    nm = r['name']; txt = ' '.join([nm, r['index'], r['exposure']]); fk = 'CA:' + r['ticker']
    ev = []; hv = None; sectors = None; topw = None; topname = ''; rs = None; inf = {'partial': '1'}
    if CASH_RX.search(nm) and not CASH_BAD.search(nm) and 'Equity' not in r['asset_classes']:
        return 'E', ('HISA' if re.search(r'savings|cash', nm, re.I) else ('Money market' if re.search('money market', nm, re.I) else 'T-bill')) + (' USD' if re.search(r'\bUSD\b|U\.S\.|\bUS\b', nm) or r['ticker'].endswith('.U') else ' CAD'), 'name: ' + CASH_RX.search(nm).group(0) + ' (no duration/credit keywords)'
    if VEC and VEC.ready.is_set() and fk in VEC.vec:
        hv = VEC.vec[fk]; inf = IDX.funds[fk]
        sw = defaultdict(float)
        for k, w in hv.items():
            if is_sec(k) and w > 0:
                s = nsec((VEC.info.get(k) or {}).get('sec', '')) or 'Unknown'
                mm = IDX.secmeta.get(k)
                if s in ('Unknown', '') and mm and mm[0]: s = mm[0]
                sw[s] += w
        known = {s: w for s, w in sw.items() if s not in ('Unknown', 'Other') and not s.startswith('Open End')}
        if sum(known.values()) > 0.5 and inf['partial'] != '1':
            tot = sum(known.values()); sectors = (sum(1 for w in known.values() if w / tot >= 0.02), max(known.items(), key=lambda t: t[1])[0], max(known.values()) / tot)
            ev.append('look-through: %d sectors >=2%%, top %s %.0f%%' % (sectors[0], sectors[1], 100 * sectors[2]))
        _wrap = re.compile(r'total net assets|\betf\b|\bfund\b|\bclass [a-z]\b|portfolio|\btrust\b|index|pool|\bseries\b|\bunits?\b|cash|future|swap|forward', re.I)
        secs = sorted((w for k, w in hv.items() if is_sec(k) and w > 0 and not _wrap.search((VEC.info.get(k) or {}).get('name', '') or k)), reverse=True); topw = secs[0] if secs else None; topname = next(((VEC.info.get(k) or {}).get('name', '') for k, w in sorted(hv.items(), key=lambda t: -t[1]) if is_sec(k) and w > 0 and not _wrap.search((VEC.info.get(k) or {}).get('name', '') or k)), '')  # wrappers/aggregate lines are not single stocks
        rs = float(inf['rawsum'] or 0)
    li = lev_info(r, rs if (hv is not None and inf['partial'] != '1') else None)
    if li[0] == 'X': return 'X:leveraged (>=1.45x) / inverse', '', li[1]
    LEVMAP[r['ticker']] = li
    if BTC_RX.search(nm) or 'bitcoin' in r['index'].lower():
        if ALT_RX.search(nm) or ALT_RX.search(r['index']): return 'X:crypto other than pure spot bitcoin', '', 'name/index mentions other crypto or overlay'
        return 'D', 'Bitcoin', 'name/index: bitcoin'
    if ALT_RX.search(nm) and re.search(r'ether|solana|xrp|crypto|digital asset', nm, re.I): return 'X:crypto other than pure spot bitcoin', '', 'altcoin / multi-crypto'
    if STOCK_RX.search(nm) or (r['index'] and STOCK_RX.search(r['index'])): return 'X:single-stock', '', 'name/index: ' + (STOCK_RX.search(nm) or STOCK_RX.search(r['index'])).group(0)
    for g, rx in COM:
        if re.search(rx, txt, re.I): return 'C', g, 'name/index/exposure: ' + re.search(rx, txt, re.I).group(0)
    if topw and topw >= 0.4 and (re.search(r'highshares|-linked|\blinked\b|powerstock|single', nm, re.I) or any(len(w_) > 3 and w_.lower() not in _GENERIC and w_.lower() not in (r['issuer'] + ' ' + nm.split()[0]).lower() and re.search(r'\b%s\b' % re.escape(w_), nm, re.I) for w_ in re.findall(r'[A-Za-z]+', topname or ''))) and 'Equity' in r['asset_classes'] and not re.search(r'portfolio|balanced', nm, re.I): return 'X:single-stock', '', 'look-through: one holding = %.0f%%' % (100 * topw)
    ac = r['asset_classes']
    if not OPT_RX.search(nm) and (BAL_RX.search(nm) or ('Equity' in ac and 'Fixed Income' in ac)):
        return 'G', 'Asset allocation', ('name: ' + BAL_RX.search(nm).group(0) if BAL_RX.search(nm) else 'holds equity + fixed income')
    if ('Fixed Income' in ac and 'Equity' not in ac) or (not ac and BOND_RX.search(nm)) or re.search(r'preferred share', nm, re.I):
        g = next((lab for lab, rx in (('Preferred shares', 'preferred'), ('High yield', 'high yield'), ('Floating rate', 'floating'), ('Short-term', r'short|1-[35] year|0-5'), ('Long-term', r'long|20\+|25\+'), ('Government', r'government|treasury|federal|provincial'), ('Corporate', 'corporate|investment grade'), ('Aggregate', 'aggregate|universe|core plus|total bond')) if re.search(rx, nm, re.I)), 'Other bond')
        return 'F', g, 'asset class Fixed Income' if 'Fixed Income' in ac else 'name: ' + BOND_RX.search(nm).group(0)
    eq = 'Equity' in ac and 'Fixed Income' not in ac
    fam = next((g for g, rx in FAM if re.search(rx, txt, re.I)), None)
    broad_h = sectors and sectors[0] >= 6 and sectors[2] < 0.5
    sect = SECT_RX.search(nm) or (not fam and sectors and sectors[2] >= 0.6)
    otxt = ' '.join([nm, r['index'], r.get('description', '') or '', r.get('exposure', '') or ''])
    if eq and (OPT_RX.search(nm) or OPT_RX.search(otxt)):  # option-writing / premium-income only
        if sect: return 'S', (SECT_RX.search(nm).group(0).title() if SECT_RX.search(nm) else sectors[1]) + ' (covered call)', 'single-sector option-writing: ' + OPT_RX.search(otxt).group(0)
        if fam or broad_h or not sectors: return 'B', fam or ('Multi-sector' if broad_h else 'Broad (no sector data; ETF-of-ETFs)'), 'option-writing: ' + OPT_RX.search(otxt).group(0) + '; ' + ('index family %s' % fam if fam else 'broad multi-sector holdings') + ('; ' + ev[0] if ev else '')
    if eq and DIV_RX.search(nm) and not sect:  # dividend-stock ETFs are passive core, not income
        return 'A', (fam or next((g for g, rx in (('Canada', r'canad'), ('US', r'\bU\.?S\.?\b|america'), ('International', r'international|eafe|europe|developed'), ('Global', r'global|world'), ('Emerging', 'emerging')) if re.search(rx, nm, re.I)), 'Multi-sector')) + ' (dividend)', 'dividend-stock ETF (no option writing) -> core; ' + ('index family %s' % fam if fam else 'broad multi-sector holdings') + ('; ' + ev[0] if ev else '')
    if eq and fam and not sect: return 'A', fam, 'index family match' + ('; ' + ev[0] if ev else '')
    if eq and sect: return 'S', (SECT_RX.search(nm).group(0).title() if SECT_RX.search(nm) else sectors[1]), ('name: ' + SECT_RX.search(nm).group(0) if SECT_RX.search(nm) else ev[0])
    return 'H', (r.get('themes') or '').split('|')[0] or ('Active / other equity' if eq else 'Other / alternative'), 'not core/income/sector/commodity/bond/balanced -> thematic/other' + ('; ' + ev[0] if ev else '')

def screener(weights=None):
    rows = scr_rows(); have = {r['ticker'] for r in rows}
    out, excl, xl, var_pending = [], Counter(), [], []
    for r in rows:
        t = r['ticker']
        if '.' in t and t.split('.')[0] in have: var_pending.append((t.split('.')[0], r)); continue
        cat, grp, ev = classify_ca(r)
        if cat.startswith('X:'):
            excl[cat[2:]] += 1
            xl.append(dict(category='X', ticker=t.split(':')[-1], name=r['name'], issuer=r['issuer'], reason=cat[2:], evidence=ev, aum_cad=fnum(r['aum']), mer_pct=None if fnum(r['expense_ratio']) is None else 100 * fnum(r['expense_ratio']), source_url=r['source_url'], nav_aum_as_of=r['nav_aum_as_of'] or 'n/a'))
            continue
        inc = fnum(r['inception_date'][:4]) if r['inception_date'] else None
        try: yrs = (datetime.date.today() - datetime.date.fromisoformat(r['inception_date'][:10])).days / 365.25
        except ValueError: yrs = None
        rr = {p: fnum(r['r' + p]) for p in ('1y', '3y', '5y')}
        ann = {p: (None if rr[p] is None else ((1 + rr[p]) ** (1 / n) - 1)) for p, n in (('1y', 1), ('3y', 3), ('5y', 5))}
        hedged = r['currency_hedged'] == 'True'
        mer = fnum(r['expense_ratio']); fee_src = 'MER (expense_ratio)'
        if mer is None or mer == 0:
            mf = fnum(r['management_fee'])
            if mf: mer, fee_src = mf, 'management fee (MER n/a)'
        out.append(dict(category=cat, group=grp, ticker=t, name=r['name'], issuer=r['issuer'], hedged=hedged, active=r['actively_managed'] == 'True', index=r['index'] or 'n/a',
                        mer_pct=None if mer is None else 100 * mer, fee_basis=fee_src if mer is not None else 'n/a', yield_pct=None if fnum(r['ttm_distribution_yield']) is None else 100 * fnum(r['ttm_distribution_yield']),
                        aum_cad=fnum(r['aum']), avg_volume_1m=fnum(r['avg_volume_1m']), years=None if yrs is None else round(yrs, 1),
                        td_pct=None if fnum(r['tracking_difference']) is None else 100 * fnum(r['tracking_difference']), te_pct=None if fnum(r['tracking_error']) is None else 100 * fnum(r['tracking_error']),
                        ret1y_pct=None if ann['1y'] is None else 100 * ann['1y'], ret3y_ann_pct=None if ann['3y'] is None else 100 * ann['3y'], ret5y_ann_pct=None if ann['5y'] is None else 100 * ann['5y'],
                        nav_aum_as_of=r['nav_aum_as_of'] or 'n/a', perf_as_of=r['perf_as_of'] or 'n/a', classified_by=ev, source=SCR_SRC, source_url=r['source_url'], variants='', leverage=(LEVMAP.get(t) or (None,))[0], leverage_evidence=(LEVMAP.get(t) or (None, ''))[1] or '',
                        holdings_in_index=('CA:' + t) in IDX.funds, vol_pct=None if fnum(r.get('volatility_1y')) is None else 100 * fnum(r.get('volatility_1y')), neg_months=fnum(r.get('neg_months_12m'))))
    best, grp_ = {}, defaultdict(list)
    for d in out:
        d['ticker'] = d['ticker'].split(':')[-1]; d.setdefault('variants', '')
        k = (d['name'].lower(), d['issuer'].lower()); grp_[k].append(d)
        if k not in best or (d['aum_cad'] or 0) > (best[k]['aum_cad'] or 0): best[k] = d
    nvar = 0
    for k, L in grp_.items():  # merged share classes are listed on the kept row, not dropped
        oth = [x for x in L if x is not best[k]]; nvar += len(oth)
        best[k]['variants'] = ', '.join('%s%s' % (x['ticker'], ' (hedged)' if x['hedged'] else '') for x in oth)
    out = list(best.values()); byt = {d['ticker']: d for d in out}; bxt = {x['ticker']: x for x in xl}
    for base, r in var_pending:  # .U/.F/.B listings of the same fund
        tgt = byt.get(base) or bxt.get(base); nvar += 1
        lab = r['ticker'] + (' (USD)' if r['ticker'].endswith('.U') or r.get('trading_currency') == 'USD' else '') + (' (hedged)' if r['currency_hedged'] == 'True' else '')
        if tgt is None: xl.append(dict(category='X', ticker=r['ticker'], name=r['name'], issuer=r['issuer'], reason='variant of unlisted base', evidence='', aum_cad=fnum(r['aum']), mer_pct=None, source_url=r['source_url'], nav_aum_as_of=r['nav_aum_as_of'] or 'n/a')); nvar -= 1; continue
        tgt['variants'] = (tgt.get('variants', '') + ', ' if tgt.get('variants') else '') + lab
    # reference fund per (benchmark group, hedged): largest-AUM fund in A (or C/D for their groups)
    ref = {}
    for d in out:
        if d['category'] in ('A', 'C', 'D', 'F', 'G', 'H') and not d['active'] and d['aum_cad']:
            k = (d['group'], d['hedged'])
            if k not in ref or d['aum_cad'] > ref[k]['aum_cad']: ref[k] = d
    for d in out:
        rf = ref.get((d['group'], d['hedged'])); d['reference'] = rf['ticker'] if rf else 'n/a'
        parts = []
        for p in ('ret1y_pct', 'ret3y_ann_pct', 'ret5y_ann_pct'):
            x = None if (not rf or d[p] is None or rf[p] is None) else d[p] - rf[p]
            d['excess_' + p.split('_')[0][3:] + '_pp'] = x
            if x is not None: parts.append(x)
        if d['td_pct'] is not None: parts.append(d['td_pct'])
        d['perf_metric_pp'] = (sum(parts) / len(parts)) if parts else None
        if d['category'] in ('B', 'G', 'H', 'S'):  # income / thematic / sector funds: compare on total return (NAV incl. distributions), since references are non-option funds
            tr = [d[p] for p in ('ret1y_pct', 'ret3y_ann_pct', 'ret5y_ann_pct') if d[p] is not None]
            d['perf_metric_pp'] = (sum(tr) / len(tr)) if tr else None; d['perf_basis'] = 'avg annualized NAV total return 1/3/5y'
        else: d['perf_basis'] = 'excess vs reference + tracking difference'
    W = {}
    for c, _ in CATS:
        w = dict(DEFW[c])
        for k in w:
            if weights and weights.get(k) not in (None, ''): w[k] = float(weights[k])
        W[c] = w
    def pct_rank(vals, x, higher=True):
        v = sorted(vals);
        if len(v) < 2: return 50.0
        import bisect
        lo = bisect.bisect_left(v, x); hi = bisect.bisect_right(v, x); r_ = (lo + hi - 1) / 2 / (len(v) - 1)
        return 100 * (r_ if higher else 1 - r_)
    res = {}
    for c, lab in CATS:
        L = [d for d in out if d['category'] == c]
        M = dict(perf=('perf_metric_pp', True), fee=('mer_pct', False), yld=('yield_pct', True), aum=('aum_cad', True), track=('years', True), vol=('vol_pct', False), cons=('neg_months', False))
        pools = {m: [d[f] for d in L if d[f] is not None] for m, (f, h) in M.items()}
        for d in L:
            num_, den = 0.0, 0.0
            for m, (f, h) in M.items():
                if d[f] is None: d['s_' + m] = None; continue
                s = pct_rank(pools[m], d[f], h); d['s_' + m] = round(s, 1)
                if W[c][m] > 0: num_ += W[c][m] * s; den += W[c][m]
            subst = [m for m in ('perf', 'fee', 'yld', 'vol', 'cons') if d['s_' + m] is not None and W[c][m] > 0]
            if W[c]['perf'] > 0 and d['s_perf'] is None and d['ret1y_pct'] is None: subst = [m for m in subst if m != 'yld']  # yield alone can't carry a fund with no performance record
            d['score'] = round(num_ / den, 1) if den and len(subst) >= 2 else None
            d['too_new'] = d['score'] is None  # = insufficient data (kept for sort)
            b = []
            if (d['years'] is not None and d['years'] < 1) or d['ret1y_pct'] is None: b.append('New (<1y)' if (d['years'] is not None and d['years'] < 1) else 'No 1y return')
            if d.get('leverage'): b.append('Leverage %.2fx' % d['leverage'] if isinstance(d['leverage'], float) else 'Leverage (ratio not stated)')
            if d['score'] is None: b.append('Insufficient data (<2 scored core metrics)')
            d['badges'] = '; '.join(b)
            d['metrics_missing'] = ', '.join(m for m in M if d['s_' + m] is None and W[c][m] > 0) or ''
        L.sort(key=lambda d: (d['too_new'], -(d['score'] or -1), -(d['aum_cad'] or 0), -(d['years'] or 0)))
        k_ = 0
        for d in L:
            if d['too_new']: d['rank'] = None; d['subsection'] = 'Insufficient data (listed, not ranked)'
            else: k_ += 1; d['rank'] = k_; d['subsection'] = 'Ranked'
        est = [d for d in L if not d['too_new']]
        cov = {m: '%d/%d' % (len(pools[m]), len(L)) for m in M}
        res[c] = dict(label=lab, weights=W[c], funds=L, coverage=cov, n=len(L), n_ranked=len(est), n_insufficient=len(L) - len(est), n_new=sum(1 for d in L if 'New' in d['badges']), n_levered=sum(1 for d in L if d.get('leverage')), n_variants=sum(len([v for v in d['variants'].split(', ') if v]) for d in L))
    res['X'] = dict(label='Excluded (not recommended): leveraged/inverse, single-stock, non-bitcoin crypto', weights={}, funds=sorted(xl, key=lambda x: (x['reason'], x['ticker'])), coverage={}, n=len(xl), n_ranked=0, n_insufficient=0, n_new=0, n_levered=0, n_variants=0)
    total = len(rows); listed = sum(v['n'] for v in res.values()) + nvar
    coverage = dict(total_canada_listed=total, primary_rows=sum(v['n'] for v in res.values()), share_class_variants_listed_on_parent=nvar, sum_of_sections=listed, reconciles=listed == total,
                    per_section={k: dict(n=v['n'], ranked=v['n_ranked'], insufficient=v['n_insufficient'], new=v['n_new'], levered=v['n_levered'], variants=v['n_variants']) for k, v in res.items()})
    return dict(categories=res, coverage=coverage, excluded=dict(excl), references={'%s%s' % (k[0], ' (hedged)' if k[1] else ''): v['ticker'] for k, v in ref.items()},
                method=('Classification (in order): leveraged/inverse excluded (name keywords 2x/3x/-1x/Bull/Bear/Inverse/Leveraged/BetaPro/Daily, or holdings weights >130%); '
                        'Bitcoin = name/index mentions bitcoin and nothing else crypto/overlay; single-stock excluded (company names in name/index, or one look-through holding >=40%); '
                        'commodities by name/index/exposure keywords; income = option-writing names (covered call, premium/enhanced yield, yield maximizer, UltraYield, put-write); dividend ETFs go to core + broad index family or >=6 sectors with top sector <50% (look-through); '
                        'core = equity + broad index family (name/index/exposure); single-sector = sector keyword in name or one sector >=60% of look-through; E cash = HISA/money market/0-3m T-bill names without bond/credit keywords; G balanced = allocation/portfolio names or equity+fixed income; F bonds = fixed income asset class; H = everything else. Universe = every Canada-listed ETF in Cboe Canada etf_list (all issuers; underlying holdings may be anywhere). New funds (<1y) are ranked with a New badge; a score needs >=2 non-missing weighted core metrics among performance/fee/yield/volatility/consistency (yield does not count toward the minimum when the category weights performance and the fund has none), otherwise the fund is listed with an Insufficient data badge. Moderate leverage (<=1.4x, e.g. 1.25x Enhanced funds) is ranked with a Leverage badge; >=1.45x and inverse are excluded. Share classes (.U/.F/hedged) are listed in the variants column of the main row. '
                        'Performance metric = average of available: tracking difference (Cboe/Trackinsight, vs the fund\'s own benchmark) and annualized NAV total-return excess vs the reference fund (largest-AUM passive fund in the same benchmark group and same hedging) over 1y/3y/5y. '
                        'Each metric becomes a percentile within its category (fees: lower is better); score = weighted average of available percentiles (missing metrics are skipped, not imputed). Ties: AUM then track length.'),
                disclaimer=DISCLAIMER, source=SCR_SRC)

# ---------------- web ----------------
PAGE = open(os.path.join(HERE, 'index.html'), encoding='utf-8').read() if os.path.exists(os.path.join(HERE, 'index.html')) else ''
from urllib.parse import urlparse, parse_qs

def run_analysis(rows, hdr, mp, manual_fx, fetch, normalize, th):
    res = analyze(positions(rows, hdr, mp), manual_fx, fetch, normalize)
    res['xray'] = xray(res, th)
    for a in res['securities']: a.pop('via_map', None)
    res['disclaimer'] = DISCLAIMER
    return res

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ct='application/json', extra=None):
        b = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def js(self, obj): return self.send(200, json.dumps(obj, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ('/', '/index.html'): return self.send(200, PAGE, 'text/html; charset=utf-8')
            if u.path == '/api/meta':
                r = boc_rate('USD')
                return self.js(dict(meta=IDX.meta, usdcad=dict(rate=r[0], date=r[1], source=r[2]) if r else None, vectors_ready=VEC.ready.is_set(), disclaimer=DISCLAIMER, defaults=DEFAULT_T))
            if u.path == '/api/screener':
                return self.js(screener({k: q.get(k) for k in ('perf', 'fee', 'yld', 'aum', 'track')}))
            if u.path == '/api/status': return self.js(dict(vectors_ready=VEC.ready.is_set(), funds=len(VEC.vec)))
            if u.path == '/api/overlap': return self.js(overlap_report(q.get('tickers', ''), q.get('weights')))
            if u.path == '/api/lookup': return self.js(lookup(q.get('q', ''), q.get('key', '')))
            if u.path == '/api/equiv': return self.js(equivalents(q.get('ticker', ''), int(q.get('n', 10))))
            if u.path == '/api/research':
                r = research(q.get('market', 'CA').upper()); lim = int(q.get('limit', 300))
                sn = snapshots()
                return self.js(dict(r, stats=r['stats'][:lim] if lim else r['stats'], stats_total=len(r['stats']), snapshots=sn[:2000], snapshot_funds=len({x['fund'] for x in sn}),
                                    snapshot_note='Our index keeps the latest snapshot per fund. Only %d funds (all Canadian) also have an OLDER disclosure in the raw data, usually a different list type (e.g. quarterly top-25 vs current top-10), so changes are shown only for holdings present in both and are indicative. No multi-snapshot history exists for US/SEC funds, so change tracking is omitted there.' % len({x['fund'] for x in sn})))
            if u.path in ('/api/hamilton', '/api/lookthrough'):
                ticker = (q.get('ticker') or 'HYLD').upper().strip()
                try:
                    depth = int(q.get('depth') or 6)
                except ValueError:
                    depth = 6
                depth = max(1, min(depth, 6))
                try:
                    res = etf_lookthrough(ticker, maxdepth=depth)
                    return self.js(res)
                except Exception as e:
                    return self.js(dict(error='Look-through failed: ' + str(e)))
        except Exception as e:
            import traceback; traceback.print_exc(); return self.send(400, json.dumps(dict(error=str(e))))
        self.send(404, '{}')
    def do_POST(self):
        try:
            j = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if self.path == '/api/xlsx':
                return self.send(200, xlsx_bytes(j['sheets']), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', {'Content-Disposition': 'attachment; filename="%s.xlsx"' % re.sub(r'\W', '_', j.get('name', 'export'))})
            rows = read_table(j['filename'], base64.b64decode(j['data']))
            if self.path == '/api/parse':
                hdr, mp = detect(rows)
                return self.send(200, json.dumps(dict(header_row=hdr, mapping=mp, headers=[str(c) for c in rows[hdr]] if rows else [], preview=[[str(c) for c in r] for r in rows[hdr + 1:hdr + 8]], fields=list(SYN))))
            if self.path == '/api/analyze':
                hdr = int(j.get('header_row', 0)); mp = {k: (int(v) if v not in (None, '', -1, '-1') else None) for k, v in j['mapping'].items()}
                mp = {k: v for k, v in mp.items() if v is not None}
                res = run_analysis(rows, hdr, mp, j.get('manual_fx'), True, j.get('normalize', True), j.get('thresholds'))
                res['csv'] = {k: to_csv(res[k], c) for k, c in EXPORTS.items()}
                return self.js(res)
        except Exception as e:
            import traceback; traceback.print_exc()
            return self.send(400, json.dumps(dict(error=str(e))))
        self.send(404, '{}')

def write_rows(out, name, rows):
    open(os.path.join(out, name + '.csv'), 'w', encoding='utf-8', newline='').write(rows_csv(rows))

def main():
    global IDX, VEC
    ap = argparse.ArgumentParser(description='Portfolio X-ray (local). ' + DISCLAIMER)
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8765))); ap.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1')); ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--cli', help='portfolio CSV/XLSX to analyze headless'); ap.add_argument('--out', default='xray_out')
    ap.add_argument('--usdcad', type=float); ap.add_argument('--no-fx-fetch', action='store_true'); ap.add_argument('--raw', action='store_true', help='do not normalize fund weights')
    ap.add_argument('--thresholds', help='JSON, e.g. {"stock":5,"sector":30,"country":60,"banks":15,"overlap":40,"equity":80,"fixed_income":20,"cash":0,"max_stock":5,"max_country":70,"max_sector":30}')
    ap.add_argument('--overlap', help='comma list of ETF tickers -> overlap report'); ap.add_argument('--weights', help='weights for --overlap')
    ap.add_argument('--holders', help='which ETFs hold this stock (name/ticker/ISIN/CUSIP)')
    ap.add_argument('--equiv', help='CA<->US equivalents for this ETF ticker')
    ap.add_argument('--research', choices=['CA', 'US'], help='most-owned stocks + concentration stats')
    ap.add_argument('--screener', action='store_true', help='Best Canadian ETFs ranked screener'); ap.add_argument('--screener-weights', help='JSON {"perf":40,"fee":35,"yld":0,"aum":15,"track":10} applied to all categories')
    ap.add_argument('--xlsx', action='store_true', help='also write one XLSX workbook with every table')
    a = ap.parse_args()
    t0 = datetime.datetime.now(); print('Loading holdings index...', flush=True)
    IDX = Index(); print('Loaded %d funds in %.1fs' % (len(IDX.funds), (datetime.datetime.now() - t0).total_seconds()), flush=True)
    VEC = Vectors(); threading.Thread(target=VEC.build, daemon=True).start()
    th = json.loads(a.thresholds) if a.thresholds else None
    reports = a.cli or a.overlap or a.holders or a.equiv or a.research or a.screener
    if reports: os.makedirs(a.out, exist_ok=True)
    book = {}
    if a.cli:
        rows = read_table(a.cli, open(a.cli, 'rb').read()); hdr, mp = detect(rows)
        print('header row', hdr, 'mapping', {k: rows[hdr][v] for k, v in mp.items()})
        res = run_analysis(rows, hdr, mp, {'USD': a.usdcad} if a.usdcad else None, not a.no_fx_fetch, not a.raw, th)
        for k, c in EXPORTS.items():
            open(os.path.join(a.out, k + '.csv'), 'w', encoding='utf-8').write(to_csv(res[k], c)); book[k] = [{cc: r.get(cc) if not isinstance(r.get(cc), set) else ', '.join(r.get(cc)) for cc in c} for r in res[k]]
        x = res['xray']
        for k in XEXPORTS: write_rows(a.out, 'xray_' + k, x[k]); book['xray_' + k] = x[k]
        write_rows(a.out, 'xray_overlap_matrix', x['overlap_matrix']); book['xray_overlap_matrix'] = x['overlap_matrix']
        print('Total CAD %.2f | FX %s' % (res['total_cad'], res['fx']))
        print('Reconcile: positions %.2f | securities %.2f' % (x['reconcile']['sum_positions_cad'], x['reconcile']['sum_securities_cad']))
        print('Top companies (look-through):')
        for s in x['top_companies'][:10]: print('  %2d %-50s %12.2f %6.2f%%' % (s['rank'], s['company'][:50], s['value_cad'], s['pct']))
        for al in x['alerts']: print('  ALERT [%s] %s' % (al['type'], al['msg']))
        for f in res['flags']: print('  [%s] %s' % (f['level'], f['msg']))
    if a.overlap:
        r = overlap_report(a.overlap, a.weights)
        for k in ('pairs', 'matrix', 'common', 'suggestions', 'funds'): write_rows(a.out, 'overlap_' + k, r[k]); book['overlap_' + k] = r[k]
        for p in r['pairs']: print('  overlap %s vs %s: %.2f%%' % (p['a'], p['b'], p['overlap_pct']))
        for s in r['suggestions'][:5]: print('  nearest single fund %s (%s) overlap %.1f%%' % (s['fund'], s['name'][:40], s['overlap_pct']))
        print(' ', r['fee_note'])
    if a.holders:
        r = lookup(a.holders)
        if 'candidates' in r:
            print('  %d candidates - refine (first ones):' % len(r['candidates']))
            for c in r['candidates'][:10]: print('   ', c['key'], c['name'], c['tickers'], c['n_funds'])
            r = lookup(key=r['candidates'][0]['key']) if r['candidates'] else None
        if r:
            write_rows(a.out, 'holders', r['funds']); book['holders'] = r['funds']
            print('  %s: held by %d CA + %d US ETFs' % (r['security']['name'], r['n_CA'], r['n_US']))
            for f in r['funds'][:10]: print('    %-12s %7.3f%%  %s  %s' % (f['fund'], f['weight_pct'], f['as_of'], f['source']))
    if a.equiv:
        r = equivalents(a.equiv)
        write_rows(a.out, 'equivalents', r['matches']); book['equivalents'] = r['matches']
        for f in r['matches'][:5]: print('  %s -> %s (%s) overlap %.1f%%' % (a.equiv, f['fund'], f['name'][:40], f['overlap_pct']))
        print(' ', r['tax_note'])
    if a.research:
        r = research(a.research)
        for k in ('by_count', 'by_weight', 'stats'): write_rows(a.out, 'research_%s_%s' % (a.research, k), r[k]); book['research_' + k] = r[k]
        sn = snapshots(); write_rows(a.out, 'research_snapshot_changes', sn); book['snapshot_changes'] = sn
        for s in r['by_count'][:5]: print('  %-40s in %d ETFs' % (s['security'][:40], s['n_etfs']))
    if a.screener:
        VEC.wait(); r = screener(json.loads(a.screener_weights) if a.screener_weights else None)
        for c, d in r['categories'].items():
            write_rows(a.out, 'screener_%s' % c, d['funds']); book['screener_' + c] = d['funds']
            print('== %s (%d funds; metric coverage %s)' % (d['label'], d['n'], d['coverage']))
            if c == 'X':
                for f in d['funds'][:5]: print('  %-8s %-45s %s' % (f['ticker'], f['name'][:45], f['reason']))
                continue
            for f in [x for x in d['funds'] if x['rank']][:5]:
                fm = lambda v, p='%.2f': 'n/a' if v is None else p % v
                print('  %d %-6s score %5s | perf %s pp (TD %s, ex1y %s, ex3y %s vs %s) | MER %s%% | yield %s%% | AUM %sM | %sy | %s' % (f['rank'], f['ticker'], fm(f['score'], '%.1f'), fm(f['perf_metric_pp']), fm(f['td_pct']), fm(f['excess_1y_pp']), fm(f['excess_3y_pp']), f['reference'], fm(f['mer_pct']), fm(f['yield_pct']), fm(f['aum_cad'] and f['aum_cad'] / 1e6, '%.0f'), fm(f['years'], '%.0f'), f['name'][:45]))
        print('  excluded:', r['excluded'])
        cv = r['coverage']; print('  coverage: total Canada-listed %d = %s + %d share-class variants = %d (%s)' % (cv['total_canada_listed'], ' + '.join('%s %d' % (k, v['n']) for k, v in cv['per_section'].items()), cv['share_class_variants_listed_on_parent'], cv['sum_of_sections'], 'reconciles' if cv['reconciles'] else 'MISMATCH'))
        write_rows(a.out, 'screener_coverage', [dict(section=k, **v) for k, v in cv['per_section'].items()] + [dict(section='share-class variants on parent rows', n=cv['share_class_variants_listed_on_parent']), dict(section='TOTAL Canada-listed', n=cv['total_canada_listed'])]); book['screener_coverage'] = [dict(section=k, **v) for k, v in cv['per_section'].items()]
        book['screener_method'] = [dict(method=r['method'], source=r['source'])]
    if reports:
        book['disclaimer'] = [dict(disclaimer=DISCLAIMER)]
        if a.xlsx: open(os.path.join(a.out, 'portfolio_xray.xlsx'), 'wb').write(xlsx_bytes(book))
        print('Wrote', a.out); return
    srv = ThreadingHTTPServer((a.host, a.port), H)
    url = 'http://%s:%d/' % ('127.0.0.1' if a.host in ('0.0.0.0', '') else a.host, a.port); print('Open', url, flush=True)
    if not a.no_browser: threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    srv.serve_forever()

if __name__ == '__main__': main()
