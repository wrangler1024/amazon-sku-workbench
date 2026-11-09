import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeThumbnailRows} from '../src/thumbnailRefresh.ts';
test('thumbnail arrival changes images only and preserves row order and editor fields',()=>{
 const draft={facts:{uses:'未保存的用途'}};
 const rows=[{id:'a',thumbnail_id:null,thumbnail_pending:true,name:'original',draft,revision:5},{id:'b',thumbnail_id:'existing',thumbnail_pending:false}];
 const next=mergeThumbnailRows(rows,[{id:'a',thumbnail_id:'new',thumbnail_pending:false,name:'unexpected',draft:{},revision:99},{id:'b',thumbnail_id:'wrong'},{id:'outside',thumbnail_id:'private'}]);
 assert.deepEqual(next.map(x=>x.id),['a','b']);assert.equal(next[0].thumbnail_id,'new');assert.equal(next[0].draft,draft);assert.equal(next[0].name,'original');assert.equal(next[0].revision,5);assert.equal(next[1],rows[1]);
 assert.equal(rows[0].thumbnail_id,null);assert.equal(mergeThumbnailRows(next,[{id:'a',thumbnail_id:'stale'}]),next);
});
test('pending and unauthorized omissions do not clear rows or change state',()=>{
 const rows=[{id:'a',thumbnail_id:null,thumbnail_pending:true}];
 assert.equal(mergeThumbnailRows(rows,[]),rows);
 assert.equal(mergeThumbnailRows(rows,[{id:'a',thumbnail_id:null,thumbnail_pending:true}]),rows);
 const next=mergeThumbnailRows(rows,[{id:'a',thumbnail_id:null,thumbnail_pending:false}]);assert.equal(next[0].thumbnail_pending,false);
});
