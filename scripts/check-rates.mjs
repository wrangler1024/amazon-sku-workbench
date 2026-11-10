import assert from 'node:assert/strict';
import book from '../rates/2026-10-31.2.json' with {type:'json'};

const expected = ['AU','US','CA','MX','UK','DE','FR','IT','ES','NL','SE','PL','BE','IE','AE','SA','JP'];
assert.deepEqual(Object.keys(book.markets).sort(),expected.sort());
let tables = 0, bands = 0;
for (const [code,m] of Object.entries(book.markets)) {
  assert.ok([0,2].includes(m.currencyExponent ?? 2),code);
  assert.ok(m.version && m.effectiveFrom && m.verifiedAt,code);
  assert.ok(['AUD','USD','CAD','MXN','GBP','EUR','SEK','PLN','AED','SAR','JPY'].includes(m.currency),code);
  assert.ok(Number.isFinite(m.dimensionalDivisor) && m.dimensionalDivisor > 0,code);
  for (const s of m.sources) {
    const url = new URL(s.url);
    assert.equal(url.protocol,'https:');
    assert.match(url.hostname,/(?:^|\.)(?:amazon\.(?:com(?:\.au|\.mx)?|ca|co\.uk|ae|sa|co\.jp)|media-amazon\.com)$/);
  }
  assert.equal(new Set(m.tiers.map(t=>t.id)).size,m.tiers.length,code);
  for (const t of m.tiers) {
    assert.ok(['unit','max','unit_until_half_kg'].includes(t.weightMode),`${code}/${t.id}`);
    assert.ok(t.regular.length);
    if(t.maxDimensions) for(const v of t.maxDimensions) assert.ok(v==null || (Number.isFinite(v) && v>0));
    for(const name of ['regular','peak','low','regularCep','peakCep','lowCep']) {
      if (!t[name]) continue;
      tables++;
      let previous=0;
      t[name].forEach((r,index)=>{
        bands++;
        assert.ok(r.upTo==null || r.upTo>previous,`${code}/${t.id}/${name} ascending weights`);
        if(r.upTo==null) assert.equal(index,t[name].length-1);
        previous=r.upTo ?? Infinity;
        assert.equal(r.cents.length,m.priceBands.length);
        for(const amount of r.cents) assert.ok(Number.isSafeInteger(amount) && amount>0,`${code}/${t.id}/${name} missing fee`);
        if(r.increment) {
          assert.ok(r.increment.step>0 && r.increment.from>=0);
          assert.equal(r.increment.cents.length,m.priceBands.length);
          r.increment.cents.forEach(amount=>assert.ok(Number.isSafeInteger(amount) && amount>=0));
        }
      });
    }
  }
}
console.log(JSON.stringify({version:book.version,marketplaces:expected.length,tables,bands,status:'passed'}));
