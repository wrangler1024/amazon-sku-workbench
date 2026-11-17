import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {briefIssues,textIssues,copyReviewedBrief,issueLabel} from '../src/briefPolicy.ts';
import {artworkPacket,emptyBrief} from '../src/briefHelpers.ts';
const policy=JSON.parse(readFileSync(new URL('../../../services/amazon-aigc-workbench-service/tests/fixtures/brief_policy.json',import.meta.url),'utf8'));

test('backend review expressions identify editable fields and clear after manual corrections',()=>{
 const brief={...emptyBrief,product:'BrightNest 托盘',plans:[{purpose:'详情',composition:'确保品牌清晰',text:'Amazon'}]};
 const before=structuredClone(brief);const issues=briefIssues(brief,policy);
 assert.deepEqual(new Set(issues.map(i=>i.field)),new Set(['product','plans.0.composition','plans.0.text']));
 assert.equal(issueLabel('plans.5.text'),'第 6 张 · 图中文字');
 assert.deepEqual(brief,before);
 brief.product='四件蓝色托盘';brief.plans[0]={purpose:'结构',composition:'展示真实结构，不保留来源品牌',text:'4 trays'};
 assert.equal(briefIssues(brief,policy).length,0);
 assert.equal(textIssues('Blue silicone trays',policy).length,0);
 assert.ok(textIssues('Blue品牌',policy).length);
});

test('copy never rewrites the draft and blocks unresolved marks including legacy and individual cards',async()=>{
 const writes=[];const write=async text=>writes.push(text);
 for(const text of ['brightnest','Bright Nest-Co','ＢｒｉｇｈｔＮｅｓｔ','确保品牌清晰','原流程图需 https://example.test/ref','Amazon logo']){
  await assert.rejects(copyReviewedBrief(text,policy,write),/核对修改/);
 }
 await assert.rejects(copyReviewedBrief('普通图需',undefined,write),/尚未就绪/);
 assert.equal(writes.length,0);
 const packet=artworkPacket('来源商品名称', {...emptyBrief,product:'四件托盘'},[{url:'https://www.amazon.ca/dp/EXAMPLE',manual_text:'BrightNest'}],'原流程：白底展示4件托盘');
 await copyReviewedBrief(packet,policy,write);
 assert.deepEqual(writes,[packet]);assert(!packet.includes('amazon'));assert(!packet.includes('BrightNest'));
});
