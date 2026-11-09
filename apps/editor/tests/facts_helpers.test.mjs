import assert from 'node:assert/strict';
import test from 'node:test';
import {appendChoice,quantitySummary,summarizeContents,syncGeneratedContents,usageSuggestions,validComponent} from '../src/factsHelpers.ts';
const row=(i,q=1,unit='件')=>({id:String(i),name:'组成物料',specification:'规格'+i,quantity:q,unit,confirmed:false});
test('10 single units plus one ten-unit specification remain 11 specifications and 20 units',()=>{
 const rows=[...Array.from({length:10},(_,i)=>row(i)),row(10,10)];
 assert.equal(quantitySummary(rows),'20件');
 const summary=summarizeContents(rows);assert(summary.includes('规格10 × 10件'));assert.equal((summary.match(/ × /g)||[]).length,11);
});
test('different units retain separate totals and per-specification quantities',()=>{
 const rows=[row(1,2,'条'),row(2,1,'个'),row(3,3,'条')];
 assert.equal(quantitySummary(rows),'5条 + 1个');assert(summarizeContents(rows).includes('规格2 × 1个'));
});
test('incomplete or invalid components cannot produce misleading contents',()=>{
 for(const q of ['',0,-1,NaN,Infinity,100001]){assert(!validComponent(row(1,q)));assert.equal(summarizeContents([row(1,q)]),'')}
 assert.equal(summarizeContents([{...row(1),specification:''}]),'');
});
test('large combinations preserve all components with an explicit detail reference within field limits',()=>{
 const rows=Array.from({length:100},(_,i)=>({...row(i),name:('物料'+i).repeat(30),specification:'规格'.repeat(600)}));
 const text=summarizeContents(rows);assert(text.length<=4000);assert(text.includes('100项组成'));assert(text.includes('100件'));assert(text.includes('组成明细'));
});
test('usage choices are grounded in the configured supplier attributes and do not duplicate text',()=>{
 const choices=usageSuggestions([{url:'https://supplier-a'}],[{url:'https://supplier-a',body:{specifications:['材质: 硅胶','用途: 遛狗；训练','适用场景: 训练','使用场景: 通用；见详情；其他']}},{url:'https://competitor',body:{specifications:['用途: 未核实功能']}}]);
 assert.deepEqual(choices.map(x=>x.text),['遛狗','训练']);assert(choices.every(x=>x.origin.includes('supplier-a')));
 assert.equal(appendChoice('遛狗；训练','训练'),'遛狗；训练');assert.equal(appendChoice('遛狗','训练'),'遛狗；训练');
});

test('quantity changes refresh generated contents while preserving added accessories and deliberate corrections',()=>{
 const old=summarizeContents([row(1)]),next=summarizeContents([row(1,10)]);
 const extra='\n附带绑带1条';
 let result=syncGeneratedContents(old+extra,old,'',null);
 assert.equal(result.text,old+extra);
 result=syncGeneratedContents(result.text,'',next,result.basis);
 assert.equal(result.text,next+extra);
 assert.deepEqual(syncGeneratedContents('人工核实的特殊包含物',old,next,null),{text:'人工核实的特殊包含物',basis:null});
});

test('supplier defaults use the existing source and fall back to registered rich links without losing additional links',async()=>{
 const {defaultSupplierUrl,registeredSupplierUrls}=await import('../src/factsHelpers.ts');
 const snapshot={'1688货源':[{text:'供应商',link:'https://detail.1688.com/offer/1.html'},{text:'https://detail.1688.com/offer/2.html'}]};
 assert.equal(defaultSupplierUrl([],snapshot),'https://detail.1688.com/offer/1.html');
 assert.equal(defaultSupplierUrl([{kind:'supplier',url:'https://detail.1688.com/offer/3.html'}],snapshot),'https://detail.1688.com/offer/3.html');
 assert.equal(registeredSupplierUrls(snapshot).length,2);
});
test('package weights convert from kg and missing dimensions never become zero or a guessed unit',async()=>{
 const {packageWeight,packageSize}=await import('../src/factsHelpers.ts');
 assert.equal(packageWeight({weight_kg:0.05}),'50 g（0.05 kg）');
 assert.equal(packageWeight({weight_kg:0}),'未提供');
 assert.equal(packageSize({length:10,width:6,height:null}),'10 × 6 × —（单位未注明）');
 assert.equal(packageSize({length:10,width:6,height:1,dimension_unit:'cm'}),'10 × 6 × 1 cm');
});

test('artwork component images stay attached to the confirmed batch facts after later edits',async()=>{
 const {artworkFacts}=await import('../src/factsHelpers.ts');
 const old={components:[{image_asset_ids:['original-image']}]};
 const data={document:{body:{facts:{components:[{image_asset_ids:['new-image']}]}}},versions:[{id:'brief-1',facts_version:'facts-1'},{id:'facts-1',body:old}]};
 assert.deepEqual(artworkFacts(data,{brief_version:'brief-1'}),old);
 assert.deepEqual(artworkFacts(data,{brief_version:'missing'}),{});
});

test('removing a base product only detaches its bound rows and refreshes generated contents',async()=>{
 const {supplierRemoval,componentFromSource}=await import('../src/factsHelpers.ts');
 const s={id:'s1',url:'https://supplier/1'};
 const rows=[{...row(1),source_id:'s1',url:s.url},{...row(2),source_id:'s2',url:s.url},{...row(3),url:'https://supplier/3'}];
 assert(componentFromSource(rows[0],s));assert(!componentFromSource(rows[1],s));
 assert(componentFromSource({url:s.url},s));
 const old=summarizeContents(rows),next=summarizeContents(rows.slice(1));
 assert.deepEqual(supplierRemoval({components:rows,included:old+'\n附赠绑带'},s),{count:1,included:next+'\n附赠绑带'});
 assert.equal(supplierRemoval({components:rows,included:'人工写好的组合说明'},s).included,'人工写好的组合说明');
 assert.equal(supplierRemoval({components:[rows[0]],included:summarizeContents([rows[0]])},s).included,'');
});
