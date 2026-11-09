import assert from 'node:assert/strict';
import test from 'node:test';
import {safeDownloadShortName} from '../src/downloadNaming.ts';
test('download preview sanitizes product names consistently with archive names',()=>{
 assert.equal(safeDownloadShortName('../透明/收纳_盘'),'透明-收纳-盘');
 assert.equal(safeDownloadShortName(' 爱心项圈 '),'爱心项圈');
 assert.equal(safeDownloadShortName(' .. '),'');
 assert.equal(safeDownloadShortName('超长商品'.repeat(20)).length,24);
});
