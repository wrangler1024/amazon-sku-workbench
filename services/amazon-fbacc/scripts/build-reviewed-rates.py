"""Build versioned numeric fee tables from archived official evidence.

No third-party calculator code is imported or executed. Run after source review;
this script does not fetch the network or write to Feishu/the review application.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence'
OUT = ROOT / 'rates' / '2026-10-31.json'
EU_SOURCE = 'https://m.media-amazon.com/images/G/02/sell/images/260630-FBA-Rate-Card-EN1.pdf'
VERSION = '2026-10-31.1'
PEAK = {'from': '2026-10-15', 'until': '2027-01-15'}


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def cents(text):
    # PDF extraction separates decimal points from the numeric glyphs.
    if text is None or not str(text).strip():
        return None
    digits = re.sub(r'\D', '', str(text))
    if not digits:
        raise ValueError(f'Invalid amount: {text!r}')
    return int(digits)


def decimal_cents(text):
    return round(float(re.search(r'(\d+\.\d+)', str(text)).group(1))*100)


def row(weight, amount, increment=None):
    result = {'upTo': weight, 'cents': amount if isinstance(amount, list) else [amount]}
    if increment:
        result['increment'] = increment
    return result


def tier(key, dims, max_unit, mode='unit', step=0, max_dim=None, max_shipping=None):
    return {'id': key, 'maxDimensions': dims, 'maxUnitWeight': max_unit,
            'weightMode': mode, 'weightStep': step,
            'maxDimensionalWeight': max_dim, 'maxShippingWeight': max_shipping,
            'regular': []}


def market(currency, start, sources, dimension_unit='cm', weight_unit='kg', divisor=5000):
    return {'currency': currency, 'effectiveFrom': start, 'verifiedAt': '2026-10-31',
            'version': VERSION, 'dimensionUnit': dimension_unit, 'weightUnit': weight_unit,
            'dimensionalDivisor': divisor, 'feeIncludesTax': False,
            'sources': sources, 'priceBands': [{'maximum': None}], 'tiers': [],
            'fuel': {'from': '2026-04-17', 'basisPoints': 150}, 'peak': PEAK,
            'notes': []}


markets = {}
eu_currencies = {'UK': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'IT': 'EUR', 'ES': 'EUR',
                 'NL': 'EUR', 'SE': 'SEK', 'PL': 'PLN', 'BE': 'EUR'}
columns = {'UK': 2, 'DE': 4, 'FR': 5, 'IT': 6, 'ES': 7, 'NL': 8, 'SE': 9, 'PL': 10, 'BE': 11}
regular = read('eu-page-6-tables.json')[0]
peak = read('eu-page-7-tables.json')[0]
low = read('eu-page-5-tables.json')[0]
# Explicit size/weight criteria transcribed from official pages 5-7.
specs = [
    ('light-envelope', [33, 23, 2.5], .1, 'unit', None, [20,40,60,80,100], 3, 2),
    ('standard-envelope', [33,23,2.5], .46, 'unit', None, [210,460], 8, 7),
    ('large-envelope', [33,23,4], .96, 'unit', None, [960], 10, 9),
    ('extra-large-envelope', [33,23,6], .96, 'unit', None, [960], 11, 10),
    ('small-parcel', [35,25,12], 3.9, 'max', 2.1, [150,400,900,1400,1900,3900], 12, 11),
    ('standard-parcel', [45,34,26], 11.9, 'max', 7.96, [150,400,900,1400,1900,2900,3900,5900,8900,11900], 18, None),
]
for code, currency in eu_currencies.items():
    m = market(currency, '2026-07-01', [{'url': EU_SOURCE, 'title': 'Amazon Europe rate card, July 2026', 'pages': [5,6,7]}])
    m['lowPrice'] = {'maximum': {'UK':10,'DE':11,'SE':140,'PL':55}.get(code,12),
                     'extendedMaximum': {'UK':20,'SE':230,'PL':85}.get(code,20), 'inclusive': True, 'weightMode':'unit'}
    if code not in ['UK', 'DE']:
        m['peak'] = None
    for key, dims, max_unit, mode, max_dim, weights_g, first, low_first in specs:
        t = tier(key, dims, max_unit, mode, max_dim=max_dim)
        t['regular'] = [row(g/1000, cents(regular[first+i][columns[code]])) for i,g in enumerate(weights_g)]
        if code in ['UK','DE']:
            t['peak'] = [row(g/1000, cents(peak[first+i][5 if code=='UK' else 7])) for i,g in enumerate(weights_g)]
        if code == 'DE':
            t['regularCep'] = [row(g/1000,cents(regular[first+i][3])) for i,g in enumerate(weights_g)]
            t['peakCep'] = [row(g/1000,cents(peak[first+i][6])) for i,g in enumerate(weights_g)]
        if low_first is not None:
            low_weights = weights_g[:2] if key == 'small-parcel' else weights_g
            t['low'] = [row(g/1000,cents(low[low_first+i][columns[code]+1])) for i,g in enumerate(low_weights)]
            if code == 'DE':
                t['lowCep'] = [row(g/1000,cents(low[low_first+i][4])) for i,g in enumerate(low_weights)]
        m['tiers'].append(t)
    # Ordinary oversize; special oversize has separate eligibility and is not assumed.
    for key,dims,max_unit,max_dim,first,start in [
        ('small-oversize',[61,46,46],1.76,25.82,28,.76),
        ('standard-oversize-light',[101,60,60],15,72.72,30,.76),
        ('standard-oversize-heavy',[101,60,60],23,72.72,32,15.76),
        ('standard-oversize-large',[120,60,60],23,86.4,34,.76),
        ('bulky-oversize',None,23,126,36,.76),
        ('heavy-oversize',None,31.5,126,38,31.5),
    ]:
        t=tier(key,dims,max_unit,'max',max_dim=max_dim)
        t['maxLongestSide']=175
        t['maxLengthPlusGirth']=360
        def formula(col):
            return [row(None,cents(regular[first][col]),{'from':start,'step':1,'cents':[cents(regular[first+1][col])]})]
        t['regular']=formula(columns[code])
        if code=='DE': t['regularCep']=formula(3)
        if code=='UK' and key=='small-oversize':
            t['requiresQuote']='OFFICIAL_SOURCE_CONFLICT: UK small-oversize incremental rate differs between PDF pages 6 and 7 (0.25 vs 0.22 GBP/kg).'
            t['quoteAboveWeight']=.76
        m['tiers'].append(t)
    markets[code]=m

# Ireland has its own currently applicable promotional domestic table (page 16).
ie=read('eu-page-16-tables.json')[0]
m=market('EUR','2026-07-01',[{'url':EU_SOURCE,'title':'Amazon Ireland domestic rate card','pages':[16]}])
m['peak']=None
m['lowPrice']={'maximum':12,'extendedMaximum':20,'inclusive':True,'weightMode':'unit'}
m['notes']=['Ireland promotional domestic rates; Amazon may adjust these with 30 days notice.']
for key,dims,max_unit,mode,max_dim,weights,first in [
    ('small-envelope',[20,15,1],.08,'unit',None,[.08],3),
    ('standard-envelope',[33,23,2.5],.46,'unit',None,[.06,.21,.46],4),
    ('large-envelope',[33,23,4],.96,'unit',None,[.96],7),
    ('extra-large-envelope',[33,23,6],.96,'unit',None,[.96],8),
    ('small-parcel',[35,25,12],3.9,'max',2.1,[.15,.4,.9,1.4,1.9,3.9],9),
    ('standard-parcel',[45,34,26],11.9,'max',7.96,[.15,.4,.9,1.4,1.9,2.9,3.9,5.9,8.9,11.9],15),
]:
    t=tier(key,dims,max_unit,mode,max_dim=max_dim)
    t['regular']=[row(w,cents(ie[first+i][3])) for i,w in enumerate(weights)]
    low_weights=weights[:2] if key=='small-parcel' else weights
    if key!='standard-parcel': t['low']=[row(w,cents(ie[first+i][8])) for i,w in enumerate(low_weights)]
    m['tiers'].append(t)
for key,dims,max_unit,max_dim,weights,first in [
    ('small-oversize',[61,46,46],1.76,25.82,[.76,1.26,1.76],25),
    ('standard-oversize',[120,60,60],29.76,86.4,[.76,1.76,2.76,3.76,4.76,9.76,14.76,19.76,24.76,29.76],29),
]:
    t=tier(key,dims,max_unit,'max',max_dim=max_dim)
    t['regular']=[row(w,cents(ie[first+i][3])) for i,w in enumerate(weights)]
    t['regular'].append(row(None,t['regular'][-1]['cents'],{'from':weights[-1],'step':1,'cents':[1]}))
    if key=='standard-oversize': t['exclusiveMaximums']=True
    m['tiers'].append(t)
markets['IE']=m

# AU/CA: reuse previously reviewed numeric source tables.
seed=read('au-reviewed-standard-20261031.json')['rows']
au_rows=[r for r in seed if r['site']=='AU']
m=market('AUD','2026-06-10',[{'url':'https://sellercentral.amazon.com.au/help/hub/reference/external/G4UU2ZQNHJNKAG5Y','title':'Amazon AU 2026 FBA fee changes'}],divisor=4000)
m['fuel']=None;m['peak']=None;m['lowPrice']={'maximum':13,'inclusive':False}
for label in dict.fromkeys(r['tier'] for r in au_rows):
    rows=[r for r in au_rows if r['tier']==label]
    r=rows[0]
    t=tier(label.lower().replace(' ','-'),r['maxDimsCm'],r['maxUnitWeightG']/1000,
           'unit' if r['shippingMethod']=='unit' else 'max',.001)
    for key,plan in [('regular','常规'),('low','低价')]:
        t[key]=[row(r['weightMaxG']/1000,round(r['baseFee']*100)) for r in sorted(rows,key=lambda r:r['weightMaxG']) if r['plan']==plan]
    t['maxShippingWeight']=t['regular'][-1]['upTo']
    m['tiers'].append(t)
markets['AU']=m
au_full=read('au-fees-20261031.json')['tables'][0]
m['sources'].append({'url':'https://sellercentral.amazon.com.au/help/hub/reference/external/201112670','title':'Amazon AU full fulfilment fee schedule'})
for key,dims,max_unit,weights,first,extra_index,start,increment in [
    ('small-oversize',[61,46,46],2,[1,1.25,1.5,1.75,2],24,29,2,1),
    ('standard-oversize',[105,60,60],22,[1,2,3,4,5,6,7,8,9,10,15,20,22],30,43,22,1),
    ('large-oversize',None,35,[5,10,15,20,25,30,35],44,51,35,10),
    ('extra-large',None,250,[35],52,53,35,10),
]:
    t=tier(key,dims,max_unit,'max',.001)
    if key=='extra-large':t['exclusiveUnitMaximum']=True
    for plan,column in [('regular',-1),('low',-3)]:
        t[plan]=[row(w,decimal_cents(au_full[first+i][column])) for i,w in enumerate(weights)]
        t[plan].append(row(None,decimal_cents(au_full[extra_index][column]),{'from':start,'step':1,'cents':[increment]}))
    m['tiers'].append(t)
ca=read('ca-reviewed-rates-20261031.json')
m=market('CAD','2026-01-15',[{'url':ca['source'],'title':'Amazon CA 2026 regular and peak fees'}, {'url':ca['dimensionalWeightSource'],'title':'Amazon CA dimensional weight'}, {'url':'https://sell.amazon.ca/pricing','title':'Canada low-price fee reduction'}])
m['fuel']['basisPoints']=350
m['lowPriceDiscount']={'below':14,'cents':80}
for key,label,dims,max_w in [('envelope','信封Envelope',[38,27,2],.5),('standard','标准Standard',[45,35,20],9)]:
    t=tier(key,dims,max_w,'max',.1,max_shipping=max_w)
    for plan,field in [('regular','baseFee'),('peak','peakFee')]:
        t[plan]=[]
        for r in ca['rows']:
            if r['tier']!=label:continue
            inc={'from':1.5,'step':.1,'cents':[9]} if r['weightMaxG']>1500 else None
            t[plan].append(row(r['weightMaxG']/1000,round(r[field]*100),inc))
    m['tiers'].append(t)
markets['CA']=m
ca_official=read('ca-official-20261031.json')['tables'][0]
for key,dims,max_w,girth,first in [
    ('small-oversize',[152,76,None],32,330,22),
    ('medium-oversize',[270,None,None],68,330,24),
    ('large-oversize',[270,None,None],68,419,26),
]:
    t=tier(key,dims,max_w,'max',.5,max_shipping=max_w)
    t['maxLengthPlusGirth']=girth
    for plan,col in [('regular',-2),('peak',-1)]:
        t[plan]=[row(None,decimal_cents(ca_official[first][col]['text']),
                    {'from':.5,'step':.5,'cents':[decimal_cents(ca_official[first+1][col]['text'])]})]
    m['tiers'].append(t)
m['notes']=['Special oversize requires separate eligibility and quote.']

# US: source cells include previous-year columns; take only the last three.
us=read('us-official-20261031.json')['tables']
us_regular=us[0][3:];us_peak=us[1][2:]
assert len(us_regular)==27 and len(us_peak)==27
m=market('USD','2026-01-15',[{'url':'https://sellercentral.amazon.com/help/hub/reference/external/GABBX6GZPA8MSZGW','title':'Amazon US 2026 FBA regular and peak fees'},{'url':'https://sellercentral.amazon.com/help/hub/reference/external/G53Z9EKF8VVZVH29','title':'Amazon US dimensional weight'},{'url':'https://sellercentral.amazon.com/help/hub/reference/external/GEVWP48HPBLEFJEY','title':'Amazon US shipping weight'}],'in','lb',139)
m['fuel']['basisPoints']=350
m['priceBands']=[{'maximum':10,'inclusive':False},{'maximum':50,'inclusive':True},{'maximum':None}]
def us_money(cells):
    return [round(float(re.search(r'\$(\d+\.\d+)',v).group(1))*100) for v in cells[-3:]]
for key,dims,max_unit,max_ship,weights,first in [
    ('small-standard',[15,12,.75],1,1,[i/8 for i in range(1,9)],0),
    ('large-standard',[18,14,8],20,20,[i/4 for i in range(1,13)],8),
]:
    t=tier(key,dims,max_unit,'unit' if key=='small-standard' else 'max',max_shipping=max_ship)
    for plan,table in [('regular',us_regular),('peak',us_peak)]:
        t[plan]=[row(w,us_money(table[first+i])) for i,w in enumerate(weights)]
        if key=='large-standard': t[plan].append(row(20,us_money(table[20]),{'from':3,'step':.25,'cents':[8,8,8]}))
    m['tiers'].append(t)
for key,dims,max_ship,index,start,increment in [
    ('small-bulky',[37,28,20],50,21,1,38),
    ('large-bulky',[59,33,33],50,22,1,38),
    ('extra-large-0-50',None,50,23,1,38),
    ('extra-large-50-70',None,70,24,51,75),
    ('extra-large-70-150',None,150,25,71,75),
]:
    t=tier(key,dims,150,'max',1,max_shipping=max_ship)
    t['dimensionalMinimumSides']=[0,2,2]
    t['maxLengthPlusGirth']=130;t['maxLongestSide']=96
    for plan,table in [('regular',us_regular),('peak',us_peak)]:
        t[plan]=[row(None,us_money(table[index]),{'from':start,'step':1,'cents':[increment]*3})]
    m['tiers'].append(t)
m['notes']=['Overmax and Extra-large 150+ lb require a separate quote.']
markets['US']=m

# The current general table and the July change table must agree cell by cell.
mx_current=read('mx-fees-20261031.json')['tables'][1]
mx_changes=read('mx-changes-20261031.json')['tables'][1]
mx_values=[]
for current,changes in zip(mx_current[3:],mx_changes[3:]):
    values=[decimal_cents(v) for v in current[-4:]]
    assert values==[decimal_cents(v) for v in changes[-8:][1::2]]
    mx_values.append(values)
assert len(mx_values)==23
m=market('MXN','2026-07-06',[{'url':'https://sellercentral.amazon.com.mx/help/hub/reference/external/G200209150?mons_sel_locale=es_MX','title':'Amazon MX current domestic fulfilment fees'}, {'url':'https://sellercentral.amazon.com.mx/help/hub/reference/external/G201411300?mons_sel_locale=es_MX','title':'Amazon MX June and July 2026 fee changes'}])
m['fuel']=None;m['peak']=None;m['feeIncludesTax']=True
m['priceBands']=[{'maximum':150,'inclusive':False},{'maximum':299,'inclusive':False},{'maximum':499,'inclusive':False},{'maximum':None}]
t=tier('envelope',[38,27,2],.5,'unit_until_half_kg')
t['regular']=[row((i+1)/10,mx_values[i]) for i in range(5)]
m['tiers'].append(t)
t=tier('standard',[45,35,20],None,'unit_until_half_kg')
t['regular']=[row((i+1)/10,mx_values[5+i]) for i in range(10)]
t['regular'].append(row(None,mx_values[15],{'from':1,'step':.25,'cents':mx_values[16]}))
m['tiers'].append(t)
t=tier('oversize',[550,270,270],None,'max')
t['regular']=[row(50,mx_values[17],{'from':1,'step':.5,'cents':mx_values[18]}),
              row(100,mx_values[19],{'from':50,'step':.5,'cents':mx_values[20]}),
              row(None,mx_values[21],{'from':100,'step':.5,'cents':mx_values[22]})]
m['tiers'].append(t)
m['notes']=['Uses the ordinary/general fee group. Health, grocery and alcohol discounts are not applied. Prices and published fees include IVA.']
markets['MX']=m

assert len(markets)==14
OUT.write_text(json.dumps({'schemaVersion':1,'version':VERSION,'verifiedAt':'2026-10-31',
    'scope':'Domestic FBA, general non-apparel non-dangerous goods; published size tiers only.',
    'markets':markets},ensure_ascii=False,indent=2)+'\n')
print(f'{OUT}: {len(markets)} marketplaces')
