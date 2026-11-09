import assert from 'node:assert/strict';
import test from 'node:test';
import {completedEditorState} from '../src/workbenchNavigation.ts';
const rows=['a','b','c'].map(id=>({id,name:'商品 '+id,site:'CA',owner_name:id==='c'?'示例主管甲':'示例运营丙',facts_version:null}));
const confirmed=(id,extra={})=>({item:{id},document:{revision:4,facts_version:'facts',listing_version:null,brief_version:null,body:{},...extra},tasks:[]});
test('confirmation removes the current pending item and advances in existing task order',()=>{
 const result=completedEditorState(rows,[rows[2],rows[1],rows[0]],confirmed('a'),'facts','pending','');
 assert.equal(result.selected,'b');assert.equal(result.items.find(x=>x.id==='a').facts_version,'facts');
});
test('last matching item clears detail without selecting outside the operator search',()=>{
 const previous=[{...rows[0],facts_version:'f'},rows[1],rows[2]];
 assert.equal(completedEditorState(previous,previous,confirmed('b'),'facts','pending','示例运营丙').selected,'');
 assert.equal(completedEditorState([rows[0]],[rows[0]],confirmed('a'),'facts','pending','').selected,'');
});
test('all products preserves the confirmed selection and draft saves do not satisfy completion',()=>{
 assert.equal(completedEditorState(rows,rows,confirmed('a'),'facts','all','').selected,'a');
 assert.equal(completedEditorState(rows,rows,confirmed('a',{facts_version:null}),'facts','pending','').selected,'a');
});
test('listing confirmation and brief publication use their independent queues',()=>{
 const prepared=rows.map(x=>({...x,facts_version:'f'}));
 assert.equal(completedEditorState(prepared,prepared,confirmed('a',{listing_version:'listing'}),'listing','pending','').selected,'b');
 const result={...confirmed('a',{brief_version:'brief'}),tasks:[{id:'task',state:'pool',brief_version:'brief'}]};
 assert.equal(completedEditorState(prepared,prepared,result,'brief','pending','').selected,'b');
});
test('failed or stale list reload cannot keep completed pointers in the pending list',()=>{
 const result=completedEditorState(rows,rows,confirmed('b'),'facts','pending','');
 assert.equal(result.selected,'c');assert.equal(result.items[1].revision,4);
});

test('manual confirmation without facts advances only its own editor queue',()=>{
 const confirmedListing=confirmed('a',{facts_version:null,listing_version:'manual-listing'});
 assert.equal(completedEditorState(rows,rows,confirmedListing,'listing','pending','').selected,'b');
 const brief={...confirmed('a',{facts_version:null,brief_version:'manual-brief'}),tasks:[{id:'task',state:'pool',brief_version:'manual-brief'}]};
 assert.equal(completedEditorState(rows,rows,brief,'brief','pending','').selected,'b');
 assert.equal(completedEditorState(rows,rows,confirmedListing,'facts','pending','').selected,'a');
});
