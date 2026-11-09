import assert from 'node:assert/strict';
import test from 'node:test';
import {createImageCache} from '../src/imageCache.ts';

test('image blobs deduplicate mounts and stay scoped to the authenticated session',async()=>{
 const calls=[];const cache=createImageCache(async p=>{calls.push(p);return new Blob(['image'])});
 const [a,b]=await Promise.all([cache.load('/image','session-a'),cache.load('/image','session-a')]);
 assert.equal(a,b);assert.equal(await cache.load('/image','session-a'),a);assert.equal(calls.length,1);
 await cache.load('/image','session-b');assert.equal(calls.length,2);
});
test('limits parallel image downloads and permits retry after failure',async()=>{
 let active=0,peak=0,fail=true;
 const cache=createImageCache(async p=>{active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,5));active--;if(p==='bad'&&fail){fail=false;throw new Error('unavailable')}return new Blob(['image'])},{parallel:3});
 await Promise.all(Array.from({length:12},(_,i)=>cache.load(String(i),'s')));assert.equal(peak,3);
 await assert.rejects(cache.load('bad','s'));await cache.load('bad','s');
});
test('evicts completed blobs to keep preview memory bounded',async()=>{
 let calls=0;const cache=createImageCache(async()=>{calls++;return new Blob(['123456'])},{bytes:10});
 await cache.load('a','s');await cache.load('b','s');await cache.load('a','s');assert.equal(calls,3);
});
