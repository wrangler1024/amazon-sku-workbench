// Exercise the review adapter, its real Vite proxy and the standalone calculator.
// Random loopback ports and synthetic requests only; no Feishu or production service.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createServer as createCalculator } from '../services/amazon-fbacc/src/server.mjs';
import { RATE_VERSION } from '../services/amazon-fbacc/src/index.mjs';
import referenceSamples from '../services/amazon-fbacc/infra/rates/samples.json' with { type: 'json' };

const requireReview = createRequire(new URL('../apps/review/package.json', import.meta.url));
const { createServer: createVite } = await import(requireReview.resolve('vite'));

test('review adapter calculates through FBACC proxy and preserves manual-quote results', { timeout: 30_000 }, async t => {
  const calculator = createCalculator();
  await new Promise((resolve, reject) => {
    calculator.once('error', reject);
    calculator.listen(0, '127.0.0.1', resolve);
  });
  t.after(() => new Promise(resolve => calculator.close(resolve)));
  const previous = process.env.FBACC_SERVICE_URL;
  process.env.FBACC_SERVICE_URL = `http://127.0.0.1:${calculator.address().port}`;
  t.after(() => {
    if (previous === undefined) delete process.env.FBACC_SERVICE_URL;
    else process.env.FBACC_SERVICE_URL = previous;
  });
  const vite = await createVite({
    root: fileURLToPath(new URL('../apps/review/', import.meta.url)),
    configFile: fileURLToPath(new URL('../apps/review/vite.config.ts', import.meta.url)),
    server: { host: '127.0.0.1', port: 0, open: false },
    logLevel: 'error',
    // SSR adapter tests do not need the browser dependency scanner; letting it
    // run here races test teardown and emits a spurious closed-server error.
    optimizeDeps: { noDiscovery: true, include: [] },
  });
  t.after(() => vite.close());
  await vite.listen();
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const nativeFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = nativeFetch; });
  globalThis.fetch = (url, options) => nativeFetch(new URL(url, origin), options);
  const health = await (await fetch('/__ae-fba/healthz')).json();
  assert.equal(health.marketplaces, 17);
  assert.equal(health.rateVersion, RATE_VERSION);
  const { fetchAeEstimates, fetchFbaEstimates, usesRemoteFba, AE_FBA_ENDPOINT } = await vite.ssrLoadModule('/src/multisite/aeFbaService.ts');
  assert.equal(AE_FBA_ENDPOINT, '/__ae-fba/v1/fba/calculate-batch');
  const inputs = [
    { site: 'AE', category: '普通商品', price: 12, unitWeightG: 33, dimensionsCm: [12, 5, 1], fbaFeeLocal: 6.25 },
    { site: 'AE', category: '普通商品', price: 25.01, unitWeightG: 200, dimensionsCm: [30, 20, 5], fbaFeeLocal: 9 },
    { site: 'DE', category: '普通商品', price: 25, unitWeightG: 50, dimensionsCm: [20, 15, 1], fbaFeeLocal: 4.99 },
    { site: 'DE', category: '普通商品', price: 25, unitWeightG: 900, dimensionsCm: [20, 10, 7], fbaFeeLocal: 5.99 },
  ];
  const before = structuredClone(inputs);
  const results = await fetchAeEstimates(inputs, '2026-11-02', new AbortController().signal);
  assert.equal(results[0].status, 'matched');
  assert.equal(results[0].fee, 5.5);
  assert.equal(results[0].reference.version, RATE_VERSION);
  assert.equal(results[0].reference.feeIncludesTax, false);
  assert.equal(results[1].status, 'quote_required');
  assert.match(results[1].reason, /官方费率存在差异/);
  assert.equal(results[1].fee, undefined);
  assert.equal(results[2].fee, 2.44);
  assert.equal(results[2].reference.version, RATE_VERSION);
  assert.equal(results[3].fee, 3.68);
  assert.equal(results[3].reference.plan, '旺季');
  assert.deepEqual(inputs, before, 'The calculator must not overwrite manual costs');

  const sites = ['CA', 'MX', 'UK', 'US', 'AU', 'AE', 'SA', 'JP', 'DE', 'FR', 'ES'];
  for (const site of sites) assert.equal(usesRemoteFba(site), true);
  const reviewInput = request => ({ site: request.marketplace, category: '普通商品',
    price: request.price.amount, unitWeightG: request.package.weight.value,
    dimensionsCm: ['length', 'width', 'height'].map(k => request.package.dimensions[k]), saleDate: 0,
    fbaFeeLocal: 999 });
  for (const sample of referenceSamples.samples.filter(s => sites.includes(s.request.marketplace))) {
    await t.test(`reviewed reference: ${sample.id}`, async () => {
      const input = reviewInput(sample.request), original = structuredClone(input);
      const [result] = await fetchFbaEstimates([input], sample.request.saleDate, new AbortController().signal);
      assert.equal(result.status, 'matched');
      assert.equal(Math.round(result.fee * (input.site === 'JP' ? 1 : 100)), sample.expected.totalMinorUnits);
      assert.equal(result.reference.feeIncludesTax, ['MX', 'JP'].includes(input.site));
      assert.equal(result.reference.version, RATE_VERSION);
      assert.deepEqual(input, original);
      if (input.site === 'US') assert.ok(Math.abs(result.shippingWeightG - input.unitWeightG) < .001);
    });
  }
  const cases = [
    ['SA', 24.99, [20, 15, 1], 100, 5.5], ['SA', 25, [20, 15, 1], 100, null],
    ['SA', 25.01, [20, 15, 1], 100, 7.5],
    ['JP', 1000, [25, 18, 2], 250, 222], ['JP', 1001, [25, 18, 2], 250, 288],
    ['JP', 1500, [150, 30, 20], 40000, 1756], ['JP', 1500, [250, 100, 50], 50000, 13950],
    ['JP', 1500, [250, 100, 50], 50001, null],
    ['UK', 25, [60, 40, 30], 1000, null],
    ['AU', 25, [120, 60, 60], 250000, null],
    ['CA', 25, [300, 50, 50], 10000, null],
  ];
  // A mixed-currency batch also verifies positional response parsing, tax flags,
  // whole-yen precision and independent quote_required results.
  await t.test('mixed-site boundaries, extended Japan tiers and explicit quote results', async () => {
    const inputs = cases.map(([site, price, dimensionsCm, unitWeightG]) => ({
      site, price, dimensionsCm, unitWeightG, category: '普通商品', saleDate: 0, fbaFeeLocal: 999,
    }));
    const copy = structuredClone(inputs);
    const results = await fetchFbaEstimates(inputs, '2026-11-16', new AbortController().signal);
    results.forEach((result, index) => {
      const fee = cases[index][4];
      assert.equal(result.status, fee === null ? 'quote_required' : 'matched', String(cases[index]));
      assert.equal(result.fee, fee === null ? undefined : fee);
    });
    assert.deepEqual(inputs, copy);
  });
});
