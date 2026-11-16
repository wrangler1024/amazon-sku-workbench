// Exercise the review adapter, its real Vite proxy and the standalone calculator.
// Random loopback ports and synthetic requests only; no Feishu or production service.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createServer as createCalculator } from '../services/amazon-fbacc/src/server.mjs';
import { RATE_VERSION } from '../services/amazon-fbacc/src/index.mjs';

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
  const { fetchAeEstimates, AE_FBA_ENDPOINT } = await vite.ssrLoadModule('/src/multisite/aeFbaService.ts');
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
});
