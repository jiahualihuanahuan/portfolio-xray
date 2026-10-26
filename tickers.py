"""Shared ticker normalizer (used by build_index.py, app.py and the one-off data migration).

norm_ticker(raw, name='', cty='', ccy='', ac='') -> (ticker, exchange_country)
  ticker           = bare upper-case local symbol, share class joined by '.', e.g. TSLA, RY, BRK.B, RCI.B, 7203, 0700
  exchange_country = ISO-2 listing country from the vendor suffix (Bloomberg 'TSLA US', 'RY CN', 'ALV.GY';
                     Yahoo 'RY.TO', '7203.T'; exchange prefixes 'TSX:RY'), else from the security currency,
                     else from the country column; '' for non-equity rows (bonds, cash, FX, futures, options, swaps).
Non-equity rows keep a cleaned (upper-case, single-spaced) raw ticker and never get an equity ticker invented.
"""
import re

# Bloomberg exchange codes (composite + main venues) -> ISO-2
BBG = {'US': 'US', 'UN': 'US', 'UW': 'US', 'UQ': 'US', 'UA': 'US', 'UR': 'US', 'UP': 'US', 'UF': 'US', 'UV': 'US', 'UD': 'US', 'PQ': 'US',
       'CN': 'CA', 'CT': 'CA', 'CV': 'CA', 'CF': 'CA', 'CE': 'CA',
       'JP': 'JP', 'JT': 'JP', 'LN': 'GB', 'HK': 'HK', 'GR': 'DE', 'GY': 'DE', 'GF': 'DE', 'FP': 'FR', 'NA': 'NL', 'SW': 'CH', 'SE': 'CH', 'VX': 'CH',
       'AU': 'AU', 'AT': 'AU', 'IM': 'IT', 'SM': 'ES', 'SQ': 'ES', 'BB': 'BE', 'ID': 'IE', 'PL': 'PT', 'AV': 'AT', 'FH': 'FI', 'SS': 'SE', 'DC': 'DK',
       'NO': 'NO', 'IT': 'IL', 'KS': 'KR', 'KP': 'KR', 'KQ': 'KR', 'TT': 'TW', 'TP': 'TW', 'C1': 'CN', 'C2': 'CN', 'CH': 'CN', 'CG': 'CN', 'CS': 'CN',
       'IN': 'IN', 'IB': 'IN', 'IS': 'IN', 'SP': 'SG', 'MK': 'MY', 'TB': 'TH', 'IJ': 'ID', 'PM': 'PH', 'NZ': 'NZ', 'BZ': 'BR', 'MM': 'MX',
       'CI': 'CL', 'CB': 'CO', 'PE': 'PE', 'AR': 'AR', 'SJ': 'ZA', 'TI': 'TR', 'PW': 'PL', 'CP': 'CZ', 'HB': 'HU', 'GA': 'GR', 'RM': 'RU',
       'AB': 'SA', 'UH': 'AE', 'DH': 'AE', 'QD': 'QA', 'KK': 'KW', 'EY': 'EG', 'VN': 'VN', 'VH': 'VN', 'LX': 'LU', 'IR': 'IE'}
# Yahoo / Reuters style dot suffixes (only used after a '.'; checked after BBG for 2-letter codes)
YAH = {'TO': 'CA', 'V': 'CA', 'NE': 'CA', 'CN': 'CA', 'TSX': 'CA', 'L': 'GB', 'T': 'JP', 'HK': 'HK', 'DE': 'DE', 'F': 'DE', 'PA': 'FR', 'AS': 'NL',
       'AX': 'AU', 'SW': 'CH', 'MI': 'IT', 'MC': 'ES', 'BR': 'BE', 'LS': 'PT', 'VI': 'AT', 'HE': 'FI', 'ST': 'SE', 'CO': 'DK', 'OL': 'NO', 'TA': 'IL',
       'KS': 'KR', 'KQ': 'KR', 'TW': 'TW', 'TWO': 'TW', 'SS': 'CN', 'SZ': 'CN', 'NS': 'IN', 'BO': 'IN', 'SI': 'SG', 'KL': 'MY', 'BK': 'TH', 'JK': 'ID',
       'NZ': 'NZ', 'SA': 'BR', 'MX': 'MX', 'SN': 'CL', 'JO': 'ZA', 'IS': 'TR', 'WA': 'PL', 'AT': 'GR', 'IR': 'IE', 'US': 'US'}
PREFIX = {'TSE': 'CA', 'TSX': 'CA', 'XTSE': 'CA', 'CVE': 'CA', 'TSXV': 'CA', 'NEO': 'CA', 'CBOE CA': 'CA', 'NYSE': 'US', 'NASDAQ': 'US', 'ARCA': 'US',
          'NYSEARCA': 'US', 'BATS': 'US', 'AMEX': 'US', 'XNAS': 'US', 'XNYS': 'US', 'LON': 'GB', 'LSE': 'GB', 'TYO': 'JP', 'HKG': 'HK', 'ETR': 'DE', 'EPA': 'FR', 'AMS': 'NL', 'ASX': 'AU'}
# single-letter Reuters-ish suffixes seen in some CA issuer files (e.g. 'SIEG.N' DE, 'ASELS.E' TR); stripped only when the row country agrees
VEND1 = {'N': 'DE', 'E': 'TR', 'J': 'ZA', 'T': 'JP'}
CCY = {'USD': 'US', 'CAD': 'CA', 'JPY': 'JP', 'GBP': 'GB', 'GBX': 'GB', 'HKD': 'HK', 'CHF': 'CH', 'AUD': 'AU', 'SEK': 'SE', 'DKK': 'DK', 'NOK': 'NO',
       'KRW': 'KR', 'TWD': 'TW', 'CNY': 'CN', 'CNH': 'CN', 'INR': 'IN', 'SGD': 'SG', 'MYR': 'MY', 'THB': 'TH', 'IDR': 'ID', 'PHP': 'PH', 'NZD': 'NZ',
       'BRL': 'BR', 'MXN': 'MX', 'CLP': 'CL', 'COP': 'CO', 'PEN': 'PE', 'ZAR': 'ZA', 'TRY': 'TR', 'PLN': 'PL', 'CZK': 'CZ', 'HUF': 'HU', 'ILS': 'IL',
       'SAR': 'SA', 'AED': 'AE', 'QAR': 'QA', 'KWD': 'KW', 'EGP': 'EG', 'RUB': 'RU', 'VND': 'VN'}
NAMES = {'UNITED STATES': 'US', 'UNITED STATES OF AMERICA': 'US', 'U.S.': 'US', 'USA': 'US', 'CANADA': 'CA', 'CAN': 'CA', 'JAPAN': 'JP', 'CHINA': 'CN',
         'UNITED KINGDOM': 'GB', 'BRITAIN': 'GB', 'UK': 'GB', 'INDIA': 'IN', 'TAIWAN': 'TW', 'KOREA (SOUTH)': 'KR', 'SOUTH KOREA': 'KR', 'KOREA': 'KR',
         'KOREA REPUBLIC OF': 'KR', 'AUSTRALIA': 'AU', 'GERMANY': 'DE', 'FRANCE': 'FR', 'NETHERLANDS': 'NL', 'SWITZERLAND': 'CH', 'SWEDEN': 'SE',
         'HONG KONG': 'HK', 'ITALY': 'IT', 'SPAIN': 'ES', 'BRAZIL': 'BR', 'ISRAEL': 'IL', 'SINGAPORE': 'SG', 'MEXICO': 'MX', 'INDONESIA': 'ID',
         'THAILAND': 'TH', 'SAUDI ARABIA': 'SA', 'MALAYSIA': 'MY', 'NORWAY': 'NO', 'SOUTH AFRICA': 'ZA', 'DENMARK': 'DK', 'FINLAND': 'FI',
         'BELGIUM': 'BE', 'CHILE': 'CL', 'POLAND': 'PL', 'PHILIPPINES': 'PH', 'NEW ZEALAND': 'NZ', 'GREECE': 'GR', 'COLOMBIA': 'CO', 'PORTUGAL': 'PT',
         'KUWAIT': 'KW', 'QATAR': 'QA', 'TURKEY': 'TR', 'AUSTRIA': 'AT', 'IRELAND': 'IE', 'UNITED ARAB EMIRATES': 'AE', 'EGYPT': 'EG', 'PERU': 'PE',
         'HUNGARY': 'HU', 'CZECH REPUBLIC': 'CZ', 'RUSSIAN FEDERATION': 'RU', 'LUXEMBOURG': 'LU', 'ARGENTINA': 'AR', 'MACAU': 'MO', 'BERMUDA': 'BM',
         'URUGUAY': 'UY', 'ICELAND': 'IS', 'JERSEY': 'JE', 'ISLE OF MAN': 'IM', 'SLOVAK REPUBLIC': 'SK'}
ISO = set(CCY.values()) | set(NAMES.values()) | {'FI', 'BE', 'NL', 'FR', 'DE', 'IT', 'ES', 'IE', 'AT', 'PT', 'GR', 'LU', 'KY', 'BM', 'JE', 'GG', 'VG', 'IM', 'PR', 'MO'}

DOMICILE_ONLY = {'KY', 'BM', 'JE', 'GG', 'VG', 'IM', 'LU', 'PA', 'MH', 'LR', 'CW', 'BS', 'MU', 'GI', 'XX', 'XU'}

def iso_country(cty):
    c = (cty or '').strip().upper()
    if len(c) == 2 and c.isalpha() and c not in ('XX', 'XU'): return 'GB' if c == 'UK' else c
    return NAMES.get(c, '')

def _fallback(cty, ccy):
    # country column first (some CA funds report every line in CAD); currency only when the
    # country is missing or is a pure tax domicile (KY, BM, ...)
    k = iso_country(cty)
    if k and k not in DOMICILE_ONLY: return k
    c = (ccy or '').strip().upper()
    return CCY.get(c, '')

NONEQ_AC = re.compile(r'FIXED|BOND|DEBT|DBT|CASH|MONEY|FUTURE|DERIV|SWAP|OPTION|FORWARD|CURRENCY|FX|LOAN|REPO|COMMERCIAL PAPER|TREASURY|^STIV$|^DFE$|^DIR$|^DCO$|^DE$|^FI', re.I)
NONEQ_TK = re.compile(r'\d{1,2}/\d{1,2}/\d{2,4}|\b(COMDTY|INDEX|CURNCY|CORP|GOVT|MTGE|MUNI|PFD\s+\d)\b|^\$|^CASH[_ ]|^\d{6}[CP]\d|\s[CP]\d+(\.\d+)?$|\s\d{6}[CP]\d{8}$|\s[A-Z]\d{2}(-[A-Z]{3})?$|\d{2}[A-Z]-[A-Z]{3}$|\s\d+(\.\d+)?$|\s[0-9]+\s+[0-9/]+\s', re.I)
PFD = re.compile(r'\bPFD\b|\bPREF|PREFERRED|\bSR\s+\w+\s+\d', re.I)
CLASS_SEP = re.compile(r'^([A-Z0-9&]+)[/\- ]([A-Z]{1,2})$')

def is_non_equity(t, ac='', name=''):
    a = (ac or '').strip()
    if a and NONEQ_AC.search(a) and not re.search(r'EQUIT|PREFERRED', a, re.I): return True
    return bool(NONEQ_TK.search(t))

def clean(raw):
    return ' '.join(str(raw or '').upper().split())

def norm_ticker(raw, name='', cty='', ccy='', ac=''):
    t = clean(raw)
    if not t or t in ('-', '--', 'N/A', 'NA', 'NONE'): return '', ''
    t = re.sub(r' (EQUITY|PFD|PREFERRED)$', '', t)          # Bloomberg yellow keys
    if is_non_equity(t, ac, name):
        m = re.match(r'^([A-Z]{2,6})/([A-Z])$', t)               # fund share class written ZUAG/F
        return (m.group(1) + '.' + m.group(2) if m else t), ''
    rowc = _fallback(cty, ccy)
    exc = ''
    m = re.match(r'^([A-Z ]{2,9})[:]\s*(.+)$', t)            # TSX:RY, NASDAQ:NVDA
    if m and m.group(1).strip() in PREFIX: exc = PREFIX[m.group(1).strip()]; t = m.group(2).strip()
    m = re.match(r'^(.+?)_([A-Z]{3})$', t)                   # 'SECU B_SEK', 'SOBI_SEK'
    if m and m.group(2) in CCY: t = m.group(1); exc = exc or CCY[m.group(2)]
    # space-separated 2-letter Bloomberg exchange code: 'TSLA US', 'RY CN', 'BRK/B US', 'NDA FI'
    m = re.match(r'^(\S+) ([A-Z][A-Z0-9])$', t)
    if m and not exc:
        code = m.group(2)
        ispfd = bool(PFD.search(name or '')) or 'PREFERRED' in (ac or '').upper()
        if code in ('US', 'UN', 'UW', 'UQ', 'UA', 'UR', 'UP', 'CN', 'CT', 'CV') or (not ispfd and (code == rowc or code in BBG)):
            t = m.group(1); exc = rowc if (code == rowc and code not in BBG) else BBG.get(code, code)
            if code == rowc: exc = rowc
    # dot suffix: 'ALV.GY', 'RY.TO', '7203.T', 'DIR-U.CT', 'BT/A.LN', 'ATCO.B.SEK'
    m = re.match(r'^(.+?)\.([A-Z]{1,3})$', t)
    if m and not exc:
        base, sfx = m.group(1), m.group(2)
        if sfx in CCY and '.' in base: t = base; exc = CCY[sfx]
        elif sfx in ('UN', 'PR', 'WI', 'WS', 'DB', 'SB', 'PF', 'RT', 'U', 'NT', 'IR'): pass   # unit/pref/warrant/when-issued: part of the symbol
        elif len(sfx) == 2 and sfx in BBG and not (sfx == 'SS' and re.fullmatch(r'\d{6}', base)):
            t = base; exc = BBG[sfx]
        elif len(sfx) >= 2 and sfx in YAH: t = base; exc = YAH[sfx]
        elif sfx == 'SS': t = base; exc = 'CN'
        elif sfx in ('T',) and base.isdigit(): t = base; exc = 'JP'
        elif sfx in ('L', 'V', 'F') and YAH[sfx] == rowc and not re.search(r'\b(CL|CLASS|SERIES|SR)\b', name or '', re.I): t = base; exc = YAH[sfx]
        elif sfx in VEND1 and VEND1[sfx] == rowc and rowc not in ('US', 'CA'): t = base; exc = rowc
    m = re.match(r'^([A-Z0-9.]+)-(T|TO|CT|V|CN|NE)$', t)       # Questrade/Stooq style RY-T, ABC-V
    if m and not exc and rowc in ('', 'CA'): t = m.group(1); exc = 'CA'
    # trailing Bloomberg/vendor junk: 'RR/' 'BA.' 'WALMEX*' 'WALMEX *'
    t2 = re.sub(r'\s*[*/.]+$', '', t)
    if t2 != t:
        if t.rstrip().endswith('*') and not exc: exc = 'MX'
        t = t2
    # class separators -> '.': BRK/B, BRK-B, BRK B, KBANK-R, RCI/B
    m = CLASS_SEP.match(t)
    if m and not re.fullmatch(r'\d+', m.group(1)):
        b, cl = m.group(1), m.group(2)
        if cl == 'U' and (exc or rowc) == 'CA' and re.search(r'REIT|TRUST|UNIT|FUND|PARTNERS|\bL\.?P\b|STAPLED', name or '', re.I): cl = 'UN'
        t = b + '.' + cl
    t = t.replace('/', '.')
    from_suffix = bool(exc)
    if not exc:
        exc = _fallback(cty, ccy)                       # country column, else security currency
    if t.isdigit():
        if exc == 'HK': t = t.zfill(4)
        elif from_suffix and exc in ('CN', 'KR'): t = t.zfill(6)
    return t, exc

def parse_user_symbol(sym):
    """For uploaded portfolios / search boxes (no row context). Returns (ticker, exchange_country_or_'')."""
    s = clean(sym)
    t, exc = norm_ticker(s)
    if exc and exc == _fallback('', ''): exc = ''
    return t, exc
