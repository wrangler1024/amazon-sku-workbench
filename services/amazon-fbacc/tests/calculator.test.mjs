import test from 'node:test';
import assert from 'node:assert/strict';
import {calculateFbaFee as calculate,calculateBatch,getMarketplaces,InputError} from '../src/index.mjs';

const currencies=Object.fromEntries(getMarketplaces().map(m=>[m.marketplace,m.currency]));
function input(marketplace='CA',price=25,dims=[20,15,1],weight=50,date='2026-10-31') {
  return {marketplace,price:{amount:price,currency:currencies[marketplace]},
    package:{dimensions:{length:dims[0],width:dims[1],height:dims[2],unit:'cm'},weight:{value:weight,unit:'g'}},
    saleDate:date,category:'general'};
}
function total(request,amount,tier) {
  const result=calculate(request);
  assert.equal(result.status,'estimated',JSON.stringify(result));
  assert.equal(result.fee.total,amount);
  assert.equal(result.fee.totalMinorUnits,Math.round(amount*100));
  assert.equal(result.fee.subtotal+result.fee.lowPriceDiscount,result.fee.base);
  if(tier) assert.equal(result.classification.tier,tier);
  return result;
}

// Expected values are independently transcribed/calculated from the official
// rate tables. Tests do not call another calculator to obtain expected results.
for(const [site,price,amount] of [
  ['AU',25,4.55],['UK',25,1.92],['CA',25,4.90],['US',25,3.63],['MX',25,27],
  ['DE',25,2.43],['FR',25,2.82],['IT',25,3.33],['ES',25,2.91],['NL',25,2.39],
  ['SE',250,34.21],['PL',250,3.11],['BE',25,2.34],['IE',25,.64],
]) test(`official 50 g package: ${site}`,()=>total(input(site,price),amount));

test('AU actual and dimensional weights, sorted dimensions and price boundary',()=>{
  for(const [price,amount] of [[12.99,6.33],[13,7.24],[19.99,7.24],[20,7.24]]) {
    const r=total(input('AU',price,[10,20,15],400),amount,'parcel');
    assert.equal(r.classification.shippingWeight,.75);
  }
  total(input('AU',25,[33,23,2.5],75),4.58,'standard-envelope');
  assert.equal(total(input('AU',25,[33,23,2.5],75.1),5.26).classification.shippingWeight,.076);
});

test('AU dimensional oversize uses published incremental fee; large units expose eligibility warning',()=>{
  total(input('AU',25,[50,40,10],1000),11.46,'small-oversize');
  const r=calculate(input('AU',25,[120,60,60],24000));
  assert.equal(r.status,'estimated');
  assert.ok(r.warnings.some(x=>x.code==='AU_INBOUND_ELIGIBILITY'));
  assert.equal(calculate(input('AU',25,[120,60,60],250000)).status,'quote_required');
});

test('CA dimensional divisor, low-price discount and surcharge ordering',()=>{
  const r=total(input('CA',13.99,[30,20,5],200),7.29,'standard');
  assert.equal(r.classification.shippingWeight,.6);
  assert.deepEqual(r.fee,{base:7.84,lowPriceDiscount:.8,subtotal:7.04,fuelSurcharge:.25,fuelRate:.035,total:7.29,totalMinorUnits:729,includesTax:false});
  total(input('CA',14,[30,20,5],200),8.11);
  total(input('CA',13.99),4.07);
});

test('CA rounds upwards by 100 g and increments above 1500 g',()=>{
  total(input('CA',25,[20,15,10],1500),10.53);
  const r=total(input('CA',25,[20,15,10],1500.1),11.48);
  assert.equal(r.classification.shippingWeight,1.6);
  total(input('CA',25,[20,15,10],1500.1,'2026-09-30'),10.77);
});

test('CA oversize classification and 500 g increments',()=>{
  total(input('CA',25,[20,15,10],10000),26.4,'small-oversize');
  assert.equal(calculate(input('CA',25,[300,50,50],10000)).status,'quote_required');
});

test('CA peak and fuel exact dates are independent',()=>{
  for(const [date,expected] of [['2026-04-16',4.46],['2026-04-17',4.62],['2026-10-14',4.62],['2026-10-15',4.90],['2027-01-14',4.90],['2027-01-15',4.62]]) total(input('CA',25,[20,15,1],50,date),expected);
});

test('UK conservative low-price threshold and regular fallback',()=>{
  total(input('UK',10),1.54);
  total(input('UK',10.01),1.92);
  total(input('UK',15,[40,30,10],500),3.67,'standard-parcel');
  total(input('UK',8,[40,30,10],500),3.67,'standard-parcel');
  total({...input('UK',15),options:{lowPriceEligibility:'confirmed_eligible'}},1.54);
  total({...input('UK',8),options:{lowPriceEligibility:'ineligible'}},1.92);
});

test('UK peak applies only in published dates; no automatic next-year peak',()=>{
  for(const [date,expected] of [['2026-10-14',2.76],['2026-10-15',2.83],['2027-01-14',2.83],['2027-01-15',2.76],['2027-10-31',2.76]]) {
    const r=total(input('UK',25,[30,20,3],500,date),expected);
    if(date>='2027-01-15') assert.ok(r.warnings.some(x=>x.code==='NEW_YEAR_RATE_REVIEW'));
  }
});

test('DE uses separate CEP columns; UK peak values never leak into France',()=>{
  total(input('DE',25,[20,10,7],900),3.66);
  total({...input('DE',25,[20,10,7],900),options:{centralEuropeProgram:true}},3.40);
  total(input('DE',25,[20,10,7],900,'2026-10-14'),3.45);
  for(const date of ['2026-10-14','2026-10-15']) total(input('FR',25,[20,10,7],900,date),5.88);
});

test('EU low-price uses unit weight and falls back when actual weight exceeds 400 g',()=>{
  const r=total(input('FR',10,[35,25,12],400),4.78,'small-parcel');
  assert.equal(r.classification.weightBasis,'unit');
  assert.equal(r.classification.plan,'low_price');
  const fallback=calculate(input('FR',10,[35,25,12],400.1));
  assert.equal(fallback.classification.plan,'regular');
  assert.equal(fallback.classification.weightBasis,'dimensional');
});

test('IE uses its own published promotional prices',()=>{
  total(input('IE',10),.47,'small-envelope');
  const r=total(input('IE',25,[20,10,7],500),2.05,'small-parcel');
  assert.ok(r.warnings.some(x=>x.code==='IE_PROMOTIONAL_RATES'));
});

test('conflicting official UK small-oversize price never returns a guessed fee',()=>{
  const r=calculate(input('UK',25,[60,40,30],1000));
  assert.equal(r.status,'quote_required');assert.equal(r.fee,null);
  assert.equal(r.reason.code,'OFFICIAL_SOURCE_CONFLICT');
  total(input('UK',25,[50,10,1],500),3.54,'small-oversize');
  total(input('FR',25,[60,40,30],1000),10,'small-oversize');
});

test('US price bands at 10 and 50 dollars and unit conversion',()=>{
  const make=p=>({...input('US',p),package:{dimensions:{length:10,width:6,height:.5,unit:'in'},weight:{value:2,unit:'oz'}}});
  for(const [p,fee] of [[9.99,2.71],[10,3.63],[50,3.63],[50.01,3.90]]) total(make(p),fee,'small-standard');
  total({...make(25),saleDate:'2026-04-16'},3.32);
  total({...make(25),saleDate:'2026-04-17'},3.44);
});

test('US large-standard has no 2-inch floor; bulky applies the official floor',()=>{
  const small={...input('US',25),package:{dimensions:{length:16,width:1,height:1,unit:'in'},weight:{value:.2,unit:'lb'}}};
  const r=total(small,4.11,'large-standard');
  assert.equal(r.classification.weightBasis,'unit');
  const bulky={...small,package:{dimensions:{length:40,width:25,height:1,unit:'in'},weight:{value:4.5,unit:'lb'}}};
  const b=total(bulky,16.26,'large-bulky');
  assert.equal(b.classification.shippingWeight,15);
});

test('MX current official worked examples, inclusive tax and no local fuel surcharge',()=>{
  total(input('MX',139,[19.4,13.8,1.6],81),27,'envelope');
  total(input('MX',499,[20.6,19.9,2.4],222),64,'standard');
  total(input('MX',449,[42.2,33.8,19.6],2990),93.25,'standard');
  const tv=total(input('MX',13900,[152.4,97.8,12.1],44230),401.65,'oversize');
  assert.equal(tv.fee.includesTax,true);assert.equal(tv.fee.fuelSurcharge,0);
});

test('MX low-weight actual-weight rule, price boundaries and July incremental rates',()=>{
  for(const [price,amount] of [[149.99,27],[150,33],[299,49],[499,60]]) total(input('MX',price),amount);
  total(input('MX',499,[40,30,20],100),61.8,'standard');
  total(input('MX',449,[20,10,5],1000),59);
  total(input('MX',449,[20,10,5],1000.1),61.75);
});

test('metric/imperial inputs produce identical prices and input objects are not mutated',()=>{
  const cm=input('CA',25,[30,20,5],200), before=structuredClone(cm);
  const imperial={...cm,package:{dimensions:{length:5/2.54,width:30/2.54,height:20/2.54,unit:'in'},weight:{value:200/453.59237,unit:'lb'}}};
  assert.deepEqual(calculate(cm).fee,calculate(imperial).fee);
  assert.deepEqual(cm,before);
});

test('unknown sites, categories, delivery programs and historical dates return quote_required',()=>{
  for(const req of [{...input(),marketplace:'BR'}, {...input(),category:'dangerous_goods'}, {...input(),category:'unknown'}, {...input(),program:'efn'},input('MX',25,[20,15,1],50,'2026-07-05')]) {
    const r=calculate(req);assert.equal(r.status,'quote_required');assert.equal(r.fee,null);
  }
  total({...input('UK'),marketplace:'GB'},1.92);
});

test('invalid numbers, dates, unknown fields and currency mismatch are rejected',()=>{
  for(const req of [null,[],{}, {...input(),saleDate:'2026-02-30'}, {...input(),saleDate:'2026-10-31T00:00Z'},
    {...input(),price:{amount:13.999,currency:'CAD'}}, {...input(),price:{amount:NaN,currency:'CAD'}},
    {...input(),price:{amount:25,currency:'USD'}}, {...input(),package:{...input().package,weight:{value:-1,unit:'g'}}},
    {...input(),asin:'not accepted'}, {...input(),options:{centralEuropeProgram:'false'}},
    {...input('AU'),options:{centralEuropeProgram:true}},
  ]) assert.throws(()=>calculate(req),InputError);
});

test('batch preserves order and isolates validation errors; metadata cannot mutate calculator',()=>{
  const r=calculateBatch([input('CA'),{},input('AU')]);
  assert.deepEqual(r.map(x=>[x.index,x.ok]),[[0,true],[1,false],[2,true]]);
  assert.throws(()=>calculateBatch([]),InputError);assert.throws(()=>calculateBatch(Array(101).fill(input())),InputError);
  const markets=getMarketplaces();markets[0].sources[0].url='https://invalid.test';
  markets[0].tiers[0].maxDimensions[0]=0;
  assert.notEqual(getMarketplaces()[0].sources[0].url,'https://invalid.test');
  total(input('UK'),1.92);
});
