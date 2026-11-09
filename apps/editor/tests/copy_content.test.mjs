import test from 'node:test';
import assert from 'node:assert/strict';
import {copyContent,CopyPermissionError} from '../src/copyContent.ts';
test('denied Base permission never invokes any clipboard fallback',async()=>{let writes=0;await assert.rejects(copyContent('图需',{allowed:async()=>false,write:async()=>{writes++},legacy:()=>{writes++;return true}}),CopyPermissionError);assert.equal(writes,0)});
test('authorized iframe fallback copies the full packet and reports failure truthfully',async()=>{const text='图需\n竞品链接 https://amazon.com/dp/EXAMPLE\n参考重点：版式与五点';let copied='';assert.equal(await copyContent(text,{allowed:async()=>true,write:async()=>{throw Error('iframe clipboard unavailable')},legacy:t=>{copied=t;return true}}),true);assert.equal(copied,text);assert.equal(await copyContent(text,{allowed:async()=>true,write:async()=>{throw Error()},legacy:()=>false}),false)});
