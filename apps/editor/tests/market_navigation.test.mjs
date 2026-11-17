import assert from 'node:assert/strict';
import test from 'node:test';
import {pendingEdit,completedEditorState,copyState,imageState} from '../src/workbenchNavigation.ts';
test('market confirmations use selected market state despite ordinary historic pointers',()=>{
 const r={id:'a',editor_mode:'pan_eu',listing_version:'old-single',brief_version:'old-single-brief',market_editing:{facts:false,listing:true,brief:false}};
 assert.equal(pendingEdit(r,'listing'),true);assert.equal(pendingEdit(r,'brief'),false);
 assert.equal(pendingEdit({...r,market_editing:{...r.market_editing,listing:false}},'listing'),false);
 assert.equal(copyState({...r,copy_state:'ready'}),'ready');assert.equal(imageState({...r,image_state:'ready'}),'ready');
});
test('market batch success stays authoritative after a stale list response',()=>{
 const rows=[{id:'a',editor_mode:'pan_eu',market_editing:{facts:false,listing:true,brief:true}},{id:'b',listing_version:null}];
 const result=completedEditorState(rows,rows,{item:{id:'a',editor_mode:'pan_eu'},document:{revision:9,body:{}},tasks:[],market_editing:{facts:false,listing:false,brief:true}},'listing','pending','');
 assert.equal(result.selected,'b');assert.equal(pendingEdit(result.items[0],'listing'),false);assert.equal(pendingEdit(result.items[0],'brief'),true);
});
