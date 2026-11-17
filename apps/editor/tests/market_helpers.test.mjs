import test from 'node:test';
import assert from 'node:assert/strict';
import {marketDraft,mergeCandidate,remapText,sharedPacket} from '../src/marketHelpers.ts';
test('selected candidates never overwrite unselected languages or single market documents',()=>{
 const data={item:{editor_targets:['DE','IT']},market_plan:{contents:[]}};
 const d=marketDraft(data);d.listings.DE.title='Existing German';d.image_texts.DE=['','Alt'];
 const result=mergeCandidate(d,{listings:{IT:{title:'Italian'}}},'listing',['IT']);
 assert.equal(result.listings.DE.title,'Existing German');assert.equal(result.listings.IT.title,'Italian');assert.equal(d.listings.IT.title,'');assert.equal(result.image_texts.DE[1],'Alt');
});
test('one prompt contains common design once and all selected languages in the same order',()=>{
 const b={product:'本品',plans:[{purpose:'白底主图',composition:'母版构图',text:''},{purpose:'材质',composition:'共用排版',text:'PP材质'}]};
 const text=sharedPacket(b,{DE:['','PP Material'],IT:['','Materiale PP']},['DE','IT'],'SYNTHETIC');
 assert.equal(text.split('母版构图').length-1,1);assert(text.includes('DE 德语：PP Material'));assert(text.includes('IT 意大利语：Materiale PP'));assert(!text.includes('NL 荷兰语'));assert(text.indexOf('DE 德语')<text.indexOf('IT 意大利语'));
});
test('editing composition preserves translations and editing baseline text invalidates only that row',()=>{
 const old={plans:[{purpose:'主图',composition:'white',text:''},{purpose:'细节',composition:'detail',text:'材质'}]};
 const texts={DE:['','Material']};
 assert.deepEqual(remapText(old,{plans:[old.plans[0],{...old.plans[1],composition:'new'}]},texts).DE,['','Material']);
 assert.deepEqual(remapText(old,{plans:[old.plans[0],{...old.plans[1],text:'新文案'}]},texts).DE,['','']);
});
