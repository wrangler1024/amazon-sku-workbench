import http from "node:http";
import { pathToFileURL } from "node:url";
import { realpathSync } from "node:fs";
import { timingSafeEqual, randomUUID } from "node:crypto";
import {
  calculateFbaFee,
  calculateBatch,
  getMarketplaces,
  InputError,
  VERSION,
  RATE_VERSION,
} from "./index.mjs";
import { object } from "./validation.mjs";
import { openapi } from "./openapi.mjs";
import { serveAsset, securityHeaders } from "./web.mjs";

const MAX_BODY = 128 * 1024;

function send(response, status, data, headers = {}) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    ...headers,
  });
  response.end(JSON.stringify(data));
}

async function readJson(request) {
  if (
    !/^application\/json(?:\s*;|$)/i.test(request.headers["content-type"] ?? "")
  )
    throw Object.assign(
      new InputError("Content-Type must be application/json"),
      { httpStatus: 415 },
    );
  if (Number(request.headers["content-length"]) > MAX_BODY)
    throw Object.assign(new InputError("Request body exceeds 128 KiB"), {
      httpStatus: 413,
    });
  let length = 0;
  const chunks = [];
  for await (const chunk of request) {
    length += chunk.length;
    if (length > MAX_BODY)
      throw Object.assign(new InputError("Request body exceeds 128 KiB"), {
        httpStatus: 413,
      });
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw Object.assign(new InputError("Malformed JSON"), { httpStatus: 400 });
  }
}

export function createServer({
  apiKey = "",
  logger = console,
  accessLog = false,
  publicUrl = "",
} = {}) {
  if (publicUrl) {
    const url = new URL(publicUrl);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      throw new Error("Invalid public URL");
  }
  const catalog = getMarketplaces();
  const ready =
    catalog.length === 17 &&
    catalog.every((m) => {
      const probe = calculateFbaFee({
        marketplace: m.marketplace,
        price: { amount: 30, currency: m.currency },
        package: {
          dimensions: { length: 20, width: 15, height: 1, unit: "cm" },
          weight: { value: 50, unit: "g" },
        },
        saleDate: m.verifiedAt,
        category: "general",
      });
      return (
        probe.status === "estimated" &&
        Number.isFinite(probe.fee.total) &&
        probe.fee.total > 0
      );
    });
  let draining = false;
  const server = http.createServer(
    { requestTimeout: 10_000, headersTimeout: 10_000 },
    async (request, response) => {
      const requestId = randomUUID(),
        started = performance.now();
      response.setHeader("x-request-id", requestId);
      response.setHeader("x-fba-rate-version", RATE_VERSION);
      for (const [key, value] of Object.entries(securityHeaders))
        response.setHeader(key, value);
      let logRoute = "unknown";
      if (accessLog)
        response.once("finish", () => {
          if (!["/healthz", "/readyz"].includes(logRoute))
            logger.log(
              JSON.stringify({
                event: "http",
                requestId,
                method: request.method,
                route: logRoute,
                status: response.statusCode,
                durationMs:
                  Math.round((performance.now() - started) * 100) / 100,
              }),
            );
        });
      try {
        const pathname = new URL(request.url, "http://localhost").pathname;
        if (serveAsset(request, response, pathname)) {
          logRoute = pathname;
          return;
        }
        if (apiKey && !["/healthz", "/readyz"].includes(pathname)) {
          const expected = Buffer.from(`Bearer ${apiKey}`);
          const supplied = Buffer.from(request.headers.authorization ?? "");
          if (
            expected.length !== supplied.length ||
            !timingSafeEqual(expected, supplied)
          ) {
            send(response, 401, {
              error: {
                code: "UNAUTHORIZED",
                message: "A valid bearer token is required.",
              },
            });
            return;
          }
        }
        const routes = {
          "/healthz": "GET",
          "/readyz": "GET",
          "/v1/fba/marketplaces": "GET",
          "/openapi.json": "GET",
          "/v1/fba/calculate": "POST",
          "/v1/fba/calculate-batch": "POST",
        };
        if (!Object.hasOwn(routes, pathname)) {
          send(response, 404, {
            error: { code: "NOT_FOUND", message: "Unknown endpoint" },
          });
          return;
        }
        logRoute = pathname;
        if (request.method !== routes[pathname]) {
          send(
            response,
            405,
            {
              error: {
                code: "METHOD_NOT_ALLOWED",
                message: "Method not allowed",
              },
            },
            { allow: routes[pathname] },
          );
          return;
        }
        if (pathname === "/healthz")
          send(response, 200, {
            status: "ok",
            version: VERSION,
            rateVersion: RATE_VERSION,
            marketplaces: getMarketplaces().length,
          });
        else if (pathname === "/readyz")
          send(response, ready && !draining ? 200 : 503, {
            status: ready && !draining ? "ready" : "not_ready",
            version: VERSION,
            rateVersion: RATE_VERSION,
            marketplaces: catalog.length,
          });
        else if (pathname === "/v1/fba/marketplaces")
          send(response, 200, { marketplaces: getMarketplaces() });
        else if (pathname === "/openapi.json")
          send(response, 200, {
            ...openapi,
            ...(apiKey ? { security: [{ bearerAuth: [] }] } : {}),
            ...(publicUrl ? { servers: [{ url: publicUrl }] } : {}),
          });
        else {
          if (draining) {
            send(response, 503, {
              error: {
                code: "SERVICE_DRAINING",
                message: "Service is restarting; retry shortly.",
              },
            });
            return;
          }
          const body = await readJson(request);
          if (pathname === "/v1/fba/calculate")
            send(response, 200, calculateFbaFee(body));
          else {
            object(body, ["items"]);
            send(response, 200, { results: calculateBatch(body.items) });
          }
        }
      } catch (error) {
        if (response.destroyed || response.headersSent) return;
        if (error instanceof InputError)
          send(response, error.httpStatus ?? 422, {
            error: {
              code: error.code,
              path: error.path,
              message: error.message,
            },
          });
        else {
          // Do not log request data or credentials.
          logger.error("FBA calculator internal error:", error.name);
          send(response, 500, {
            error: {
              code: "INTERNAL_ERROR",
              message: "Calculation could not be completed.",
            },
          });
        }
      }
    },
  );
  server.startDrain = () => {
    draining = true;
  };
  return server;
}

let isEntrypoint = false;
try {
  if (process.argv[1]) isEntrypoint = import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;
} catch {
  // Imports from stdin/eval have no on-disk entry point.
}
if (isEntrypoint) {
  const host = process.env.FBA_CALCULATOR_HOST ?? "127.0.0.1";
  const port = Number(process.env.FBA_CALCULATOR_PORT ?? 4318);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error("Invalid FBA_CALCULATOR_PORT");
  const server = createServer({
    apiKey: process.env.FBA_CALCULATOR_API_KEY ?? "",
    publicUrl: process.env.FBA_CALCULATOR_PUBLIC_URL ?? "",
    accessLog: process.env.FBA_CALCULATOR_ACCESS_LOG === "1",
  });
  server.listen(port, host, () =>
    console.log(
      `FBA calculator ${VERSION} listening on http://${host}:${port}`,
    ),
  );
  server.on("error", (error) => {
    console.error(`Server failed: ${error.code}`);
    process.exitCode = 1;
  });
  const stop = () => {
    server.startDrain();
    server.close(() => process.exit(0));
    setTimeout(() => {
      server.closeAllConnections();
      process.exit(1);
    }, 10000).unref();
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
}
