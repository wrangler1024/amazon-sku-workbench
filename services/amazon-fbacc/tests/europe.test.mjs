import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateFbaFee as calculate } from '../src/index.mjs';
import previous from '../rates/2026-10-31.2.json' with { type: 'json' };
import current from '../rates/2026-11-16.1.json' with { type: 'json' };

const request = (marketplace, price = 25, date = '2026-11-16', dimensions = [20, 15, 1], weight = 50) => ({
  marketplace, price: { amount: price, currency: 'EUR' }, saleDate: date, category: 'general',
  package: { dimensions: { length: dimensions[0], width: dimensions[1], height: dimensions[2], unit: 'cm' },
    weight: { value: weight, unit: 'g' } },
});

// Base amounts independently read from official July card pp. 5–7; the public
// HelpHub notice raises fuel from 1.5% to 2% on 2026-11-02, rounded once to cents.
for (const [site, before, after, lowPrice, lowFee] of [
  ['DE', 2.43, 2.44, 11, 1.96], ['FR', 2.82, 2.84, 12, 2.32], ['ES', 2.91, 2.93, 12, 2.27],
]) {
  test(`${site}: current EUR fees, fuel date and conservative low-price boundary`, () => {
    assert.equal(calculate(request(site, 25, '2026-11-01')).fee.total, before);
    const result = calculate(request(site, 25, '2026-11-02'));
    assert.equal(result.fee.total, after);
    assert.equal(result.fee.fuelRate, .02);
    assert.equal(result.fee.includesTax, false);
    assert.equal(result.currency, 'EUR');
    assert.equal(calculate(request(site, lowPrice)).fee.total, lowFee);
    assert.equal(calculate(request(site, lowPrice + .01)).classification.plan, site === 'DE' ? 'peak' : 'regular');
    assert.equal(calculate({ ...request(site, lowPrice + .01), options: { lowPriceEligibility: 'confirmed_eligible' } }).fee.total, lowFee);
    assert.equal(calculate({ ...request(site), program: 'efn' }).status, 'quote_required');
    assert.equal(calculate({ ...request(site), category: 'dangerous_goods' }).status, 'quote_required');
    assert.equal(calculate(request(site, 25, '2026-06-30')).reason.code, 'NO_HISTORICAL_RATE');
    assert.equal(calculate(request(site, 25, '2026-11-16', [200, 100, 100], 40000)).status, 'quote_required');
  });
}

test('German peak, CEP and volumetric bands stay separate from FR/ES', () => {
  const input = request('DE', 25, '2026-11-16', [20, 10, 7], 900);
  assert.equal(calculate(input).fee.total, 3.68); // 3.61 + 0.07
  assert.equal(calculate({ ...input, options: { centralEuropeProgram: true } }).fee.total, 3.42); // 3.35 + 0.07
  assert.equal(calculate({ ...input, saleDate: '2027-01-15' }).fee.total, 3.47); // 3.40 + 0.07
  assert.equal(calculate({ ...input, marketplace: 'FR' }).fee.total, 5.91); // 5.79 + 0.12
  assert.equal(calculate({ ...input, marketplace: 'ES' }).fee.total, 4.03); // 3.95 + 0.08
  const dimensional = calculate(request('DE', 25, '2026-11-16', [35, 25, 12], 400.1));
  assert.equal(dimensional.classification.weightBasis, 'dimensional');
  assert.equal(dimensional.classification.shippingWeight, 2.1);
});

test('new review preserves base tables and every non-European marketplace', () => {
  for (const [site, market] of Object.entries(previous.markets)) {
    assert.deepEqual(current.markets[site].tiers, market.tiers, `${site} base tables`);
    if (!['UK','DE','FR','IT','ES','NL','SE','PL','BE','IE'].includes(site))
      assert.deepEqual(current.markets[site], market, site);
  }
});
