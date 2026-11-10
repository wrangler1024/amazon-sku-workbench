"""Build the reviewed AE/SA/JP extension from locally archived official tables.
Usage: python3 scripts/build-ae-sa-jp.py EVIDENCE_DIRECTORY
Raw Amazon documents are deliberately not distributed in the repository.
"""
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=Path(sys.argv[1]);VERSION='2026-10-31.2'
book=json.loads((ROOT/'rates/2026-10-31.json').read_text())

def table(name):return json.loads((EVIDENCE/name).read_text())
def source(domain,guid,title):return {'url':f'https://sellercentral.amazon.{domain}/help/hub/reference/external/{guid}','title':title}
def market(currency,start,scale,sources):
 return {'currency':currency,'currencyExponent':scale,'effectiveFrom':start,'verifiedAt':'2026-10-31','version':VERSION,'dimensionUnit':'cm','weightUnit':'kg','dimensionalDivisor':5000,'feeIncludesTax':currency=='JPY','sources':sources,'priceBands':[{'maximum':1000 if currency=='JPY' else 25,'inclusive':currency!='SAR'},{'maximum':None}],'priceBandPlans':['low_price','regular'],'tiers':[],'fuel':None,'peak':None,'notes':[]}
def tier(name,dims,weight,rows,**extra):
 return {'id':name,'maxDimensions':dims,'maxUnitWeight':weight,'maxShippingWeight':weight,'weightMode':'unit','weightStep':0,'regular':rows,**extra}
for code,domain,currency in [('AE','ae','AED'),('SA','sa','SAR')]:
 m=market(currency,'2025-08-01',2,[source(domain,'201112670','FBA fulfilment fee schedule and worked examples'),{'url':f'https://sell.amazon.{domain}/pricing','title':'Amazon public pricing'},source(domain,'201105770','Packaged product size tiers')])
 config={'Small envelope':('small-envelope',[20,15,1],.1),'Standard envelope':('standard-envelope',[33,23,2.5],.5),'Large envelope':('large-envelope',[33,23,5],1),'Standard parcel':('standard-parcel',[45,34,26],12),'Oversize':('oversize',None,30)}
 current=None
 for r in table(code+'-help-tables.json')[1][1:]:
  if r[0] in config:
   name,dims,limit=config[r[0]];current=tier(name,dims,limit,[]);m['tiers'].append(current)
  elif r[0].startswith('≤'):
   assert len(r)==3 and current is not None
   weight=float(re.search(r'[\d.]+',r[0]).group())
   current['regular'].append({'upTo':weight,'cents':[round(float(r[1])*100),round(float(r[2])*100)]})
 assert len(m['tiers'])==5 and sum(len(t['regular']) for t in m['tiers'])==34
 if code=='AE':
  t=m['tiers'][2];t['requiresQuote']='Official UAE large-envelope regular fees conflict: public pricing AED 7.50 versus help schedule AED 9.00.';t['quoteAbovePrice']=25
 else:m['ambiguousPrices']=[25]
 m['notes']=['Uses packaged unit weight for fee bands, as specified in the fulfilment fee page and worked examples; dimensional weight does not set the charged fee.','Fees exclude VAT. Oversize admission eligibility must be verified; weights above 30 kg require a separate quote.']
 book['markets'][code]=m
m=market('JPY','2026-04-01',0,[source('co.jp','201112670','Japan non-frozen FBA fulfilment fees'),source('co.jp','G201105770','Japan packaged product size tiers'),source('co.jp','GRH54FUTD3C5LWWW','Japan April 2026 fee changes'),{'url':'https://sell.amazon.co.jp/pricing','title':'Japan public pricing'}])
rows=table('JP-help-tables.json')[0][1:]
assert len(rows)==22
specs=[('jp-small',[25,18,2],.25,45),('jp-standard-thin',[35,30,3.3],1,None)]
specs += [(f'jp-standard-{s}',[45,35,20],w,s) for s,w in [(20,2),(30,2),(40,2),(50,2),(60,2),(80,5),(100,9)]]
specs += [(f'jp-oversize-{s}',None,w,s) for s,w in [(60,2),(80,5),(100,10),(120,15),(140,20),(160,25),(180,30),(200,40)]]
specs += [(f'jp-supersize-{s}',None,50,s) for s in [200,220,240,260,400]]
assert len(specs)==len(rows)
new_fees=[]
for r in table('JP-2026-tables.json')[1]:
 if len(r)>=4 and re.fullmatch(r'[\d,\s]+円',r[-1]) and re.fullmatch(r'[\d,\s]+円',r[-2]):new_fees.append([int(re.sub(r'\D','',v)) for v in r[-2:]])
assert len(new_fees)==22
for (name,dims,weight,total),r,checked in zip(specs,rows,new_fees):
 regular,low=[int(re.sub(r'\D','',v)) for v in r[-2:]]
 assert [regular,low]==checked
 extra={'maxDimensionSum':total} if total else {}
 if 'supersize' in name:extra['maxLongestSide']=250
 m['tiers'].append(tier(name,dims,weight,[{'upTo':weight,'cents':[low,regular]}],**extra))
m['notes']=['Non-frozen ordinary goods only; includes 10% Japanese consumption tax.','Fees use packaged unit weight. Standard tiers also require individual-side limits; large/supersize tiers use length + width + height.','Supersize is limited to 400 cm combined, 250 cm longest side and 50 kg; separate service/admission restrictions apply.']
book['markets']['JP']=m
book['version']=VERSION
(ROOT/'rates/2026-10-31.2.json').write_text(json.dumps(book,ensure_ascii=False,indent=2)+'\n')
print('Built 17 markets; original 14 market definitions preserved')
