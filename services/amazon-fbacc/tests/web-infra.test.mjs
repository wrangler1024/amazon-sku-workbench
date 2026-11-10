import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "../src/server.mjs";
import request from "../examples/request.json" with { type: "json" };

async function start(t, options = {}) {
  const server = createServer(options);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  return { server, url: `http://127.0.0.1:${server.address().port}` };
}

test("calculator page and assets load without authentication; calculations still honor configured key", async (t) => {
  const { url } = await start(t, { apiKey: "test-only" });
  const page = await fetch(url);
  assert.equal(page.status, 200);
  assert.match(page.headers.get("content-type"), /text\/html/);
  const html = await page.text();
  assert.match(html, /FBA 配送费计算器/);
  assert.match(html, /calculator-form/);
  assert.match(
    page.headers.get("content-security-policy"),
    /script-src 'self'/,
  );
  assert.equal(page.headers.get("x-content-type-options"), "nosniff");
  for (const name of ["app.js", "app.css", "model.mjs", "logo.svg"])
    assert.equal((await fetch(url + "/assets/" + name)).status, 200);
  const api = await fetch(url + "/v1/fba/calculate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  assert.equal(api.status, 401);
});

test("static whitelist blocks source, credentials, evidence and traversal", async (t) => {
  const { url } = await start(t);
  for (const path of [
    "/src/server.mjs",
    "/package.json",
    "/.env",
    "/evidence/verification.json",
    "/assets/..%2f..%2fpackage.json",
    "/assets/%2e%2e/%2e%2e/package.json",
  ]) {
    const result = await fetch(url + path);
    assert.equal(result.status, 404, path);
    assert.equal((await result.json()).error.code, "NOT_FOUND");
  }
});

test("ETags revalidate static assets and HEAD never sends the content", async (t) => {
  const { url } = await start(t);
  const first = await fetch(url + "/assets/app.css");
  const tag = first.headers.get("etag");
  assert.ok(tag);
  const cached = await fetch(url + "/assets/app.css", {
    headers: { "if-none-match": tag },
  });
  assert.equal(cached.status, 304);
  assert.equal(await cached.text(), "");
  const head = await fetch(url + "/", { method: "HEAD" });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), "");
  assert.equal(head.headers.get("cache-control"), "no-cache");
  assert.equal((await fetch(url + "/", { method: "POST" })).status, 405);
});

test("readiness covers all 17 adapters and draining rejects new calculations", async (t) => {
  const { url, server } = await start(t);
  const ready = await fetch(url + "/readyz");
  assert.equal(ready.status, 200);
  assert.equal((await ready.json()).marketplaces, 17);
  server.startDrain();
  assert.equal((await fetch(url + "/readyz")).status, 503);
  const calc = await fetch(url + "/v1/fba/calculate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  assert.equal(calc.status, 503);
  assert.equal((await calc.json()).error.code, "SERVICE_DRAINING");
  assert.equal((await fetch(url + "/healthz")).status, 200);
});

test("request IDs are unique; structured logs omit bodies, query strings and authorization", async (t) => {
  const logs = [];
  const { url } = await start(t, {
    accessLog: true,
    logger: { log: (value) => logs.push(JSON.parse(value)), error: () => {} },
  });
  const first = await fetch(url + "/missing?secret=PRIVATE_QUERY_MARKER", {
    headers: { authorization: "Bearer PRIVATE_AUTH_MARKER" },
  });
  const second = await fetch(url + "/v1/fba/calculate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...request, secret: "PRIVATE_BODY_MARKER" }),
  });
  assert.match(first.headers.get("x-request-id"), /^[0-9a-f-]{36}$/);
  assert.notEqual(
    first.headers.get("x-request-id"),
    second.headers.get("x-request-id"),
  );
  assert.equal(second.headers.get("x-fba-rate-version"), "2026-10-31.2");
  assert.equal(logs.length, 2);
  assert.equal(logs[0].route, "unknown");
  assert.doesNotMatch(JSON.stringify(logs), /PRIVATE_|secret|authorization/);
  assert.ok(
    logs.every((row) => Number.isFinite(row.durationMs) && row.requestId),
  );
});

test("production OpenAPI advertises the actual configured HTTPS endpoint", async (t) => {
  const { url } = await start(t, { publicUrl: "https://fbacc.samforo.icu" });
  const spec = await (await fetch(url + "/openapi.json")).json();
  assert.deepEqual(spec.servers, [{ url: "https://fbacc.samforo.icu" }]);
  assert.ok(spec.paths["/readyz"]);
  assert.throws(() =>
    createServer({ publicUrl: "https://user:password@example.test" }),
  );
  assert.throws(() => createServer({ publicUrl: "javascript:alert(1)" }));
});
