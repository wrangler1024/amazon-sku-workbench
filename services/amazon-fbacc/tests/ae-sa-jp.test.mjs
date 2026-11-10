import test from 'node:test';
import assert from 'node:assert/strict';
import {calculateFbaFee as calc,InputError,getMarketplaces} from '../src/index.mjs';
import original from '../rates/2026-10-31.json' with {type:'json'};
import current from '../rates/2026-10-31.2.json' with {type:'json'};
const req=(market,price,dims,weight)=>({marketplace:market,price:{amount:price,currency:{AE:'AED',SA:'SAR',JP:'JPY'}[market]},package:{dimensions:{length:dims[0],width:dims[1],height:dims[2],unit:'cm'},weight:{value:weight,unit:'g'}},category:'general',saleDate:'2026-10-31'});
function fee(request,amount,tier){const r=calc(request);assert.equal(r.status,'estimated');assert.equal(r.fee.total,amount);assert.equal(r.fee.totalMinorUnits,Math.round(amount*(request.marketplace==='JP'?1:100)));if(tier)assert.equal(r.classification.tier,tier);return r;}

test('extension preserves every original marketplace rate and adds three currencies',()=>{
 for(const [code,market] of Object.entries(original.markets))assert.deepEqual(current.markets[code],market);
 assert.equal(getMarketplaces().length,17);
 assert.deepEqual(['AE','SA','JP'].map(c=>getMarketplaces().find(m=>m.marketplace===c).currency),['AED','SAR','JPY']);
});
test('UAE official worked examples use unit weight even when dimensional weight is higher',()=>{
 fee(req('AE',1050,[35.1,22.9,1.8],81),9.2,'standard-parcel');
 fee(req('AE',24,[32,16.9,14],1608),9.5,'standard-parcel');
 fee(req('AE',24,[61,19.1,15.2],3555),13.5,'oversize');
 const monitor=fee(req('AE',1050,[137,89,8.9],18597),31.5,'oversize');
 assert.equal(monitor.classification.shippingWeight,18.597);assert.equal(monitor.fee.includesTax,false);
});
test('Saudi official worked examples and distinct parcel price bands',()=>{
 fee(req('SA',15,[35.1,22.9,1.8],81),7.2);
 fee(req('SA',45,[32,16.9,14],1608),12);
 fee(req('SA',22,[61,19.1,15.2],3555),13);
 fee(req('SA',120,[137,89,8.9],18597),33);
 fee(req('SA',30,[40,20,10],1000),10);
 fee(req('SA',30,[40,20,10],1000.1),11.5);
});
test('Middle East exact price boundaries and conflicting UAE large envelope are explicit',()=>{
 fee(req('AE',25,[20,15,1],100),5.5);
 fee(req('AE',25.01,[20,15,1],100),7.5);
 fee(req('SA',24.99,[20,15,1],100),5.5);
 fee(req('SA',25.01,[20,15,1],100),7.5);
 const boundary=calc(req('SA',25,[20,15,1],100));assert.equal(boundary.fee,null);assert.equal(boundary.reason.code,'PRICE_BOUNDARY_UNCONFIRMED');
 fee(req('AE',25,[30,20,5],200),7);
 const conflict=calc(req('AE',25.01,[30,20,5],200));assert.equal(conflict.fee,null);assert.equal(conflict.reason.code,'OFFICIAL_SOURCE_CONFLICT');
});
test('Middle East size and weight transitions do not extrapolate beyond 30 kg',()=>{
 fee(req('AE',30,[20,15,1],100.1),8.2,'standard-envelope');
 fee(req('SA',30,[45,34,26],12000),22,'standard-parcel');
 fee(req('SA',30,[45,34,26],12000.1),28,'oversize');
 fee(req('SA',30,[46,34,26],30000),43);
 assert.equal(calc(req('SA',30,[46,34,26],30000.1)).status,'quote_required');
});
test('Japan April 2026 independent worked examples and whole-yen minor units',()=>{
 const r=fee(req('JP',900,[15,9,5],500),358,'jp-standard-30');assert.equal(r.fee.includesTax,true);
 fee(req('JP',1150,[30,14,5],620),425,'jp-standard-50');
 fee(req('JP',8000,[25,20,10],325),430,'jp-standard-60');
 fee(req('JP',1000,[25,18,2],250),222,'jp-small');
 fee(req('JP',1001,[25,18,2],250),288,'jp-small');
 assert.throws(()=>calc(req('JP',1000.5,[20,15,1],50)),InputError);
});
test('Japan side limits, sum limits and unit-weight limits jointly classify packages',()=>{
 fee(req('JP',1500,[35,30,3.3],1000),318,'jp-standard-thin');
 fee(req('JP',1500,[45,35,20],9000),532,'jp-standard-100');
 fee(req('JP',1500,[45,35,20],9000.1),675,'jp-oversize-100');
 fee(req('JP',1500,[45.001,10,2],500),589,'jp-oversize-60');
 fee(req('JP',1500,[45.001,35,20],9000),781,'jp-oversize-120');
});
test('Japan supersize limits and 2026 effective date are enforced',()=>{
 fee(req('JP',1500,[150,30,20],40000),1756,'jp-oversize-200');
 fee(req('JP',1500,[150,30,20],40000.1),2755,'jp-supersize-200');
 fee(req('JP',1500,[250,100,50],50000),13950,'jp-supersize-400');
 for(const [dims,weight] of [[[250.1,100,49],50000],[[250,100,50.1],50000],[[250,100,50],50000.1]])assert.equal(calc(req('JP',1500,dims,weight)).status,'quote_required');
 const r=calc({...req('JP',1500,[20,15,1],50),saleDate:'2026-03-31'});assert.equal(r.reason.code,'NO_HISTORICAL_RATE');
});
