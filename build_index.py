"""Build compact look-through index from scraped CSVs (latest snapshot per fund)."""
import csv,glob,gzip,os,re,json,datetime,collections,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from tickers import norm_ticker   # shared ticker normalizer (canonical ticker + exchange_country)
csv.field_size_limit(10**8)
CA='/workspace/cboe_etfs';US='/workspace/us_etfs';OUT='/workspace/lookthrough/app/data'
MON={m:i+1 for i,m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}
def d(s):
  s=(s or '').strip()
  m=re.match(r'(\d{4})-(\d{2})-(\d{2})',s)
  if m:return m[0]
  m=re.match(r'([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})',s)
  if m and m[1].lower() in MON:return f'{m[3]}-{MON[m[1].lower()]:02d}-{int(m[2]):02d}'
  m=re.match(r'(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*[-/ ](\d{4})',s)
  if m and m[2].lower() in MON:return f'{m[3]}-{MON[m[2].lower()]:02d}-{int(m[1]):02d}'
  m=re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})',s)
  if m:return f'{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}'
  return s[:10]
def f(s):
  try:return float(str(s).replace(',','').replace('%','').strip())
  except:return None
def pick(r,*ks):
  for k in ks:
    v=(r.get(k) or '').strip()
    if v and v.upper() not in('N/A','-','--','NA'):return v
  return ''
PARTIAL=re.compile(r'top|names only|short positions|underlying index|fact sheet|summary of|exposure|highlights|10 issuers',re.I)
H=[];F={};S=[];LT={}
def add_source(path,market,issuer):
  rows=collections.defaultdict(list)
  for r in csv.DictReader(open(path,encoding='utf8',errors='replace')):
    t=(r.get('etf_ticker') or '').strip().upper().replace('XTSE:','')
    if not t:continue
    rows[t].append(r)
  for t,rs in rows.items():
    # vanguard CA: direct vs look-through; keep both tagged
    latest=max(d(r.get('as_of')) for r in rs)
    for od in sorted({d(r.get('as_of')) for r in rs}-{latest}):
      if not od:continue
      for r in rs:
        if d(r.get('as_of'))!=od:continue
        w=f(pick(r,'weight_pct','weight_pct_of_nav','weight_pct_of_net_assets','weight'))
        if w is None:continue
        S.append(dict(m=market,fund=t,asof=od,lt=(r.get('list_type') or r.get('scope') or '').strip(),name=pick(r,'holding_name','issuer'),tkr=pick(r,'holding_ticker').upper(),isin=pick(r,'isin').upper(),w=round(w,6)))
    rs=[r for r in rs if d(r.get('as_of'))==latest]
    LT[(market,t)]='; '.join(sorted({(r.get('list_type') or r.get('scope') or '').strip() for r in rs}-{''}))
    src=(rs[0].get('source') or '').strip() or ('SEC EDGAR N-PORT' if 'sec.gov' in rs[0].get('source_url','') else issuer+' site')
    url=rs[0].get('source_url','')
    scopes=collections.Counter((r.get('list_type') or r.get('scope') or r.get('holdings_type') or '').strip() for r in rs)
    out=[];hdr=False
    for r in rs:
      raw=pick(r,'weight_pct','weight_pct_of_nav','weight_pct_of_net_assets','weight')
      if raw.lower().startswith('weight'):hdr=True;continue   # iShares CA: 2nd table = vendor look-through
      w=f(raw)
      if w is None:continue
      if 'look-through of the single' in (r.get('list_type') or '') and w>=90:continue  # wrapper row; rest is the underlying's holdings
      lt=(r.get('list_type') or r.get('scope') or '')
      if re.search(r'short',lt+r.get('position_side','')+r.get('position_type',''),re.I) and w>0:w=-w
      hv=r.get('holdings_type','')
      out.append(dict(m=market,fund=t,lvl='LT' if (hv=='look-through' or hdr) else 'D',name=pick(r,'holding_name','issuer'),
        tkr=pick(r,'holding_ticker','ticker','identifier').upper() if not src.startswith('SEC') else pick(r,'holding_ticker').upper(),
        isin=pick(r,'isin').upper(),cusip=pick(r,'cusip','cusip_sedol').upper()[:9],w=round(w,6),
        sec=pick(r,'sector','gics_sector','sector_gics','classification','segment','category'),
        cty=pick(r,'country','location','geo'),ac=pick(r,'asset_class','asset_category','security_type'),
        ccy=pick(r,'currency'),ic=pick(r,'issuer_category')))
    if not out:continue
    if any(x['lvl']=='D' for x in out) and any(x['lvl']=='LT' for x in out):out=[x for x in out if x['lvl']=='D']  # recurse instead of storing vendor look-through
    s=sum(x['w'] for x in out if x['lvl']=='D') or sum(x['w'] for x in out)
    sc='; '.join(k for k in scopes if k)
    part=bool(PARTIAL.search(sc)) and not re.search(r'complete|full|all positions|all holdings|entire',sc,re.I)
    if not src.startswith('SEC') and not part and len(out)<=30 and s<90:part=True;sc=(sc+'; ' if sc else '')+f'only {len(out)} holdings summing to {s:.1f}% (likely top-N)'
    if src.startswith('SEC'):cov='SEC N-PORT quarter-end (full)'
    else:cov=('partial: '+sc) if part else ('full'+(' ('+sc+')' if sc else ''))
    F[(market,t)]=dict(m=market,fund=t,issuer=issuer,source=src,url=url,asof=latest,n=len(out),rawsum=round(s,3),coverage=cov,partial=int(part))
    H.extend(out)
for p in sorted(glob.glob(f'{CA}/*/holdings.csv')):
  add_source(p,'CA',p.split('/')[-2])
for p in sorted(glob.glob(f'{US}/*/holdings.csv')):
  add_source(p,'US',p.split('/')[-2])
# universes (known ETFs without holdings)
U=[]
for r in csv.DictReader(open(f'{CA}/etf_list.csv')):
  for tk in (r['allCanadaTickers'] or r['ticker']).split('|'):
    U.append(dict(m='CA',ticker=tk.strip().upper(),base=r['ticker'].upper(),name=r['label'],issuer=r['provider'],isin=r['isin'],mer=r.get('expense_ratio','')))
cag={r['ticker'].upper():r['why'] for r in csv.DictReader(open(f'{CA}/gaps.csv')) if 'holdings' in r['missing']}
for p in glob.glob(f'{US}/*/etf_list.csv'):
  iss=p.split('/')[-2]
  for r in csv.DictReader(open(p)):
    U.append(dict(m='US',ticker=r['etf_ticker'].upper(),base=r['etf_ticker'].upper(),name=r['fund_name'],issuer=iss,isin=r.get('isin','')))
usg=collections.defaultdict(list)
for p in glob.glob(f'{US}/*/gaps.csv'):
  for r in csv.DictReader(open(p)):
    for tk in (r['etf_ticker'] or '').split(';'):
      if tk:usg[tk.upper()].append(r['item']+': '+r['reason'])
for u in U:
  if u['m']=='CA':u['gap']='' if ('CA',u['base']) in F else cag.get(u['base'],'no holdings scraped')
  else:u['gap']='; '.join(usg.get(u['ticker'],[]))[:400] if ('US',u['base']) not in F else '; '.join(usg.get(u['ticker'],[]))[:400]
for x in H:   # standardize tickers: keep vendor original in ticker_raw
  x['ticker_raw']=x.pop('tkr');x['ticker'],x['exchange_country']=norm_ticker(x['ticker_raw'],x['name'],x['cty'],x['ccy'],x['ac'])
for x in S:
  x['ticker_raw']=x.pop('tkr');x['ticker'],x['exchange_country']=norm_ticker(x['ticker_raw'],x['name'])
cols=['m','fund','lvl','name','ticker','isin','cusip','w','sec','cty','ac','ccy','ic','ticker_raw','exchange_country']   # app reads columns by position
for _f in glob.glob(f'{OUT}/holdings*.csv.gz'):os.remove(_f)
PARTS=[]
for _m in ('CA','US'):
  PARTS.append(f'holdings_{_m}.csv.gz')
  with gzip.open(f'{OUT}/holdings_{_m}.csv.gz','wt',compresslevel=9,newline='') as g:
    w=csv.DictWriter(g,cols);w.writeheader();w.writerows(x for x in H if x['m']==_m)
with gzip.open(f'{OUT}/snapshots_prior.csv.gz','wt',compresslevel=9,newline='') as g:
  w=csv.DictWriter(g,['m','fund','asof','lt','name','ticker','isin','w','ticker_raw','exchange_country']);w.writeheader();w.writerows(S)
for k,v in F.items():v['latest_list_type']=LT.get(k,'')
with open(f'{OUT}/funds.csv','w',newline='') as g:
  w=csv.DictWriter(g,list(next(iter(F.values())).keys()));w.writeheader();w.writerows(F.values())
with open(f'{OUT}/universe.csv','w',newline='') as g:
  w=csv.DictWriter(g,['m','ticker','base','name','issuer','isin','gap','mer'],extrasaction='ignore');w.writeheader();w.writerows(U)
meta=dict(built=datetime.datetime.now().isoformat(timespec='minutes'),rows=len(H),funds_CA=sum(1 for k in F if k[0]=='CA'),funds_US=sum(1 for k in F if k[0]=='US'),universe_CA=len({u['base'] for u in U if u['m']=='CA'}),universe_US=len({u['base'] for u in U if u['m']=='US'}),partial=sum(v['partial'] for v in F.values()),holdings_parts=PARTS,prior_snapshot_rows=len(S),prior_snapshot_funds=len({(x['m'],x['fund']) for x in S}))
json.dump(meta,open(f'{OUT}/meta.json','w'),indent=1);print(meta)
