"""Build data/ca_screener.csv from Cboe Canada files (fund_overview.csv + performance.csv). Run after build_index.py."""
import csv, collections, os, datetime
CA = '/workspace/cboe_etfs'; OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ca_screener.csv')
perf = collections.defaultdict(dict)
for r in csv.DictReader(open(f'{CA}/performance.csv')):
    if r['period'] in ('1y', '3y', '5y') and r['nav_total_return'] not in ('', 'None'):
        perf[r['etf_ticker']][r['period']] = r['nav_total_return']; perf[r['etf_ticker']]['asof'] = r['as_of']
cols = ['ticker', 'name', 'issuer', 'inception_date', 'expense_ratio', 'management_fee', 'aum', 'nav_aum_as_of', 'ttm_distribution_yield', 'index',
        'tracking_difference', 'tracking_error', 'avg_volume_1m', 'currency_hedged', 'actively_managed', 'asset_classes', 'exposure', 'product_type', 'source_url',
        'volatility_1y', 'volatility_3y', 'max_drawdown_1y', 'share_class', 'base_currency', 'trading_currency', 'themes', 'description']
with open(OUT, 'w', newline='') as g:
    w = csv.writer(g); w.writerow(cols + ['r1y', 'r3y', 'r5y', 'perf_as_of'])
    seen = set()
    for r in csv.DictReader(open(f'{CA}/fund_overview.csv')):
        p = perf.get(r['ticker'], {}); seen.add(r['ticker'])
        w.writerow([r.get(c, '') for c in cols] + [p.get('1y', ''), p.get('3y', ''), p.get('5y', ''), p.get('asof', '')])
    # ETFs in Cboe's full Canada-listed list (etf_list.csv) without a fund_overview record: include from list-level fields so none are silently dropped
    for r in csv.DictReader(open(f'{CA}/etf_list.csv')):
        if r['ticker'] in seen: continue
        p = perf.get(r['ticker'], {})
        d = dict(ticker=r['ticker'], name=r['label'], issuer=r['provider'], inception_date=(datetime.datetime.utcfromtimestamp(int(r['creationDate'])).date().isoformat() if r.get('creationDate', '').isdigit() else ''),
                 expense_ratio=r['expense_ratio'], aum=r['CAD:aum'], exposure=r['exposure_description'], source_url='https://etfmarket.cboe.com/etf/%s' % r['ticker'], product_type='ETF (list-level data only)')
        w.writerow([d.get(c, '') for c in cols] + [p.get('1y', ''), p.get('3y', ''), p.get('5y', ''), p.get('asof', '')])
print('wrote', OUT)
