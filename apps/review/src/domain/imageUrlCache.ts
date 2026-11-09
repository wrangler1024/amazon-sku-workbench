// Feishu attachment links are temporary. Reuse them briefly so opening another
// queue or refreshing the same tab does not request every image again.
const TTL_MS = 2 * 60 * 1000;
const PREFIX = "amazon-review-image-url:v1:";
const memory = new Map<string, { url: string; expiresAt: number }>();
const pending = new Map<string, Promise<string | null>>();

function key(viewerId: string, table: string, recordId: string, token: string | null): string {
  return PREFIX + JSON.stringify([viewerId, table, recordId, token]);
}

function read(cacheKey: string): string | null {
  let entry = memory.get(cacheKey);
  if (!entry) {
    try {
      const saved = window.sessionStorage.getItem(cacheKey);
      entry = saved ? JSON.parse(saved) as { url: string; expiresAt: number } : undefined;
    } catch { /* Storage may be disabled inside a Feishu iframe. */ }
  }
  if (entry && entry.expiresAt > Date.now() && typeof entry.url === "string") {
    memory.set(cacheKey, entry);
    return entry.url;
  }
  memory.delete(cacheKey);
  try { window.sessionStorage.removeItem(cacheKey); } catch { /* Storage unavailable. */ }
  return null;
}

export function cachedImageUrl(viewerId: string, table: string, recordId: string, token: string | null): string | null {
  return token ? read(key(viewerId, table, recordId, token)) : null;
}

export function invalidateImageUrl(viewerId: string, table: string, recordId: string, token: string | null): void {
  const cacheKey = key(viewerId, table, recordId, token);
  memory.delete(cacheKey);
  try { window.sessionStorage.removeItem(cacheKey); } catch { /* Storage unavailable. */ }
}

export function resolveImageUrl(viewerId: string, table: string, recordId: string, token: string | null,
  fetchUrl: () => Promise<string | null>, refresh = false): Promise<string | null> {
  if (!token) return Promise.resolve(null);
  const cacheKey = key(viewerId, table, recordId, token);
  if (refresh) invalidateImageUrl(viewerId, table, recordId, token);
  else {
    const cached = read(cacheKey);
    if (cached) return Promise.resolve(cached);
  }
  const running = pending.get(cacheKey);
  if (running) return running;
  const request = fetchUrl().then((url) => {
    if (url) {
      const entry = { url, expiresAt: Date.now() + TTL_MS };
      memory.set(cacheKey, entry);
      if (/^https?:\/\//.test(url)) {
        try { window.sessionStorage.setItem(cacheKey, JSON.stringify(entry)); }
        catch { /* Storage unavailable or full. */ }
      }
    }
    return url;
  }).finally(() => { pending.delete(cacheKey); });
  pending.set(cacheKey, request);
  return request;
}
