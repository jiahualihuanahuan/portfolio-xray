"""Build data/ca_screener.csv from Cboe Canada files (fund_overview.csv + performance.csv). Run after build_index.py."""
import csv, collections, os
CA = '/workspace/cboe_etfs'; OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ca_screener.csv')
perf = collections.defaultdict(dict)
for r in csv.DictReader(open(f'{CA}/performance.csv')):
    if r['period'] in ('1y', '3y', '5y') and r['nav_total_return'] not in ('', 'None'):
        perf[r['etf_ticker']][r['period']] = r['nav_total_return']; perf[r['etf_ticker']]['asof'] = r['as_of']
cols = ['ticker', 'name', 'issuer', 'inception_date', 'expense_ratio', 'management_fee', 'aum', 'nav_aum_as_of', 'ttm_distribution_yield', 'index',
        'tracking_difference', 'tracking_error', 'avg_volume_1m', 'currency_hedged', 'actively_managed', 'asset_classes', 'exposure', 'product_type', 'source_url']
with open(OUT, 'w', newline='') as g:
    w = csv.writer(g); w.writerow(cols + ['r1y', 'r3y', 'r5y', 'perf_as_of'])
    for r in csv.DictReader(open(f'{CA}/fund_overview.csv')):
        p = perf.get(r['ticker'], {})
        w.writerow([r[c] for c in cols] + [p.get('1y', ''), p.get('3y', ''), p.get('5y', ''), p.get('asof', '')])
print('wrote', OUT)
