import assert from 'node:assert/strict';
import test from 'node:test';
import {emptyBrief,withPlans,briefImageCount,briefText,planText} from '../src/briefHelpers.ts';

test('default seven plans and manual changes retain business fields and synchronize count',()=>{
 assert.equal(emptyBrief.plans.length,7);
 const brief={...emptyBrief,product:'本品',references:['https://supplier'],plans:[{purpose:'主图',composition:'白底展示2件',text:''},{purpose:'结构',composition:'展示实际结构',text:'Details'}]};
 const next=withPlans(brief,brief.plans.slice(0,1));
 assert.equal(next.image_count,1);assert.equal(next.product,'本品');assert.equal(next.references,brief.references);assert.equal(brief.plans.length,2);
 assert.equal(briefImageCount({image_count:6}),6);assert.equal(briefImageCount({plans:brief.plans}),2);
});
test('candidate preview and copy include every plan and target-language text',()=>{
 const content=briefText({...emptyBrief,product:'本品',image_count:2,plans:[{purpose:'主图',composition:'白底展示2件',text:''},{purpose:'结构',composition:'展示实际结构',text:'Details'}]});
 assert(content.includes('【套图策划建议】'));assert(content.includes('01 主图'));assert(content.includes('02 结构'));assert(content.includes('图中文字：Details'));assert(content.includes('策划建议数量：2张'));
 assert(!briefText({product:'旧版',image_count:7}).includes('套图策划建议'));
});

test('artwork packet includes competitor notes and links once, preserving legacy briefs',async()=>{
 const {artworkPacket}=await import('../src/briefHelpers.ts');
 const url='https://www.amazon.ca/dp/B0TEST0007';const sources=[{kind:'supplier',url:'https://detail.1688.com/offer/123.html'},{kind:'competitor',url,manual_text:'参考白底构图、排版和五点表达'}];
 for(const legacy of ['', '旧批次原始图需']){
  const text=artworkPacket('本品',{...emptyBrief,references:[url]},sources,legacy,{task_number:'R-1046',code:'DEMO-ITEM-003'});
  assert(text.includes('任务编号：R-1046'));assert(text.includes('SKU：DEMO-ITEM-003'));
  assert.equal(text.split(url).length,2);assert(text.includes('竞品参考链接：'));assert(text.includes('参考重点：\n参考白底构图、排版和五点表达'));assert(text.includes('1688货源链接：'));assert(text.includes(legacy||'【套图策划建议】'));
 }
});


test('last optional planning card can be removed without a zero image count',()=>{
 const b={...emptyBrief,product:'正文保留',image_count:7,plans:[{purpose:'仅主题',composition:'',text:''}]};
 const removed=withPlans(b,[]);assert.equal(removed.plans.length,0);assert.equal(removed.image_count,7);assert.equal(removed.product,b.product);
 const copied=planText({purpose:'',composition:'',text:'Optional'},0);assert.ok(copied.includes('Optional'));assert.ok(!copied.includes('待补充'));assert.ok(!copied.includes('待填写'));
});
