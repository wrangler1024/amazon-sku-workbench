import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const [release,samplePath]=process.argv.slice(2);
const {calculateFbaFee}=await import(pathToFileURL(resolve(release,'src/index.mjs')));
const document=JSON.parse(readFileSync(samplePath,'utf8'));
assert.equal(document.schemaVersion,1);
assert.ok(document.samples.length>=17);
const results=[];
for(const sample of document.samples){
  assert.ok(sample.id && sample.reference?.url && sample.reference?.description);
  const url=new URL(sample.reference.url);
  assert.equal(url.protocol,'https:');
  assert.match(url.hostname,/(?:^|\.)(?:amazon\.(?:com(?:\.au|\.mx)?|ca|co\.uk|ae|sa|co\.jp)|media-amazon\.com)$/);
  assert.ok(['official_table','official_worked_example','official_calculator'].includes(sample.reference.kind));
  const actual=calculateFbaFee(sample.request);
  assert.equal(actual.status,sample.expected.status,sample.id);
  if(actual.status==='estimated'){
    assert.equal(actual.fee.totalMinorUnits,sample.expected.totalMinorUnits,sample.id);
    if(sample.expected.tier) assert.equal(actual.classification.tier,sample.expected.tier,sample.id);
  }else assert.equal(actual.reason.code,sample.expected.reasonCode,sample.id);
  results.push({id:sample.id,marketplace:actual.marketplace,status:'matched',referenceKind:sample.reference.kind});
}
console.log(JSON.stringify({status:'passed',samples:results.length,results}));
