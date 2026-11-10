import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const files = [
  ["/", "index.html", "text/html; charset=utf-8"],
  ["/index.html", "index.html", "text/html; charset=utf-8"],
  ["/assets/app.css", "app.css", "text/css; charset=utf-8"],
  ["/assets/app.js", "app.js", "text/javascript; charset=utf-8"],
  ["/assets/model.mjs", "model.mjs", "text/javascript; charset=utf-8"],
  ["/assets/logo.svg", "logo.svg", "image/svg+xml"],
];
const assets = new Map(
  files.map(([route, file, contentType]) => {
    const content = readFileSync(new URL(`../public/${file}`, import.meta.url));
    return [
      route,
      {
        content,
        contentType,
        etag: '"' + createHash("sha256").update(content).digest("hex") + '"',
        html: contentType.startsWith("text/html"),
      },
    ];
  }),
);
export const securityHeaders = {
  "content-security-policy":
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "permissions-policy": "camera=(), microphone=(), geolocation=()",
};
export function serveAsset(request, response, pathname) {
  const asset = assets.get(pathname);
  if (!asset) return false;
  if (!["GET", "HEAD"].includes(request.method)) {
    response.writeHead(405, {
      "content-type": "application/json; charset=utf-8",
      allow: "GET, HEAD",
    });
    response.end(
      JSON.stringify({
        error: { code: "METHOD_NOT_ALLOWED", message: "Method not allowed" },
      }),
    );
    return true;
  }
  const headers = {
    "content-type": asset.contentType,
    "cache-control": asset.html ? "no-cache" : "public, max-age=3600",
    etag: asset.etag,
  };
  if (request.headers["if-none-match"] === asset.etag) {
    response.writeHead(304, headers);
    response.end();
    return true;
  }
  response.writeHead(200, {
    ...headers,
    "content-length": asset.content.length,
  });
  response.end(request.method === "HEAD" ? undefined : asset.content);
  return true;
}
