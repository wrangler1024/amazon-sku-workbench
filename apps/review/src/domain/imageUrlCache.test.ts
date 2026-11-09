import { beforeEach, describe, expect, it, vi } from "vitest";
import { cachedImageUrl, invalidateImageUrl, resolveImageUrl } from "./imageUrlCache";

beforeEach(() => { window.sessionStorage.clear(); });

describe("评审图片临时链接", () => {
  it("同一附件并发只请求一次，切换列表可立即复用", async () => {
    const fetchUrl = vi.fn(async () => "https://example.com/image-a");
    const [first, second] = await Promise.all([
      resolveImageUrl("user-a", "站点评审", "record-a", "token-a", fetchUrl),
      resolveImageUrl("user-a", "站点评审", "record-a", "token-a", fetchUrl),
    ]);
    expect(first).toBe(second);
    expect(fetchUrl).toHaveBeenCalledOnce();
    expect(cachedImageUrl("user-a", "站点评审", "record-a", "token-a")).toBe(first);
    await resolveImageUrl("user-a", "站点评审", "record-a", "token-a", fetchUrl);
    expect(fetchUrl).toHaveBeenCalledOnce();
  });

  it("用户、记录及图片版本隔离；失效后重新取得链接", async () => {
    const fetchUrl = vi.fn().mockResolvedValueOnce("https://example.com/old")
      .mockResolvedValueOnce("https://example.com/new");
    await resolveImageUrl("user-d", "站点评审", "record-d", "token-d", fetchUrl);
    expect(cachedImageUrl("user-b", "站点评审", "record-d", "token-d")).toBeNull();
    expect(cachedImageUrl("user-d", "站点评审", "record-d", "token-b")).toBeNull();
    invalidateImageUrl("user-d", "站点评审", "record-d", "token-d");
    expect(await resolveImageUrl("user-d", "站点评审", "record-d", "token-d", fetchUrl))
      .toBe("https://example.com/new");
    expect(fetchUrl).toHaveBeenCalledTimes(2);
  });

  it("刷新后可从会话缓存读取，过期链接不使用", async () => {
    const fetchUrl = vi.fn(async () => "https://example.com/image-c");
    await resolveImageUrl("user-c", "SKU选品库", "record-c", "token-c", fetchUrl);
    const cacheKey = Object.keys(window.sessionStorage)[0];
    expect(cacheKey).toBeTruthy();
    const entry = JSON.parse(window.sessionStorage.getItem(cacheKey)!);
    entry.expiresAt = Date.now() - 1;
    window.sessionStorage.setItem(cacheKey, JSON.stringify(entry));
    invalidateImageUrl("user-c", "SKU选品库", "record-c", "token-c");
    window.sessionStorage.setItem(cacheKey, JSON.stringify(entry));
    expect(cachedImageUrl("user-c", "SKU选品库", "record-c", "token-c")).toBeNull();
    await resolveImageUrl("user-c", "SKU选品库", "record-c", "token-c", fetchUrl);
    expect(fetchUrl).toHaveBeenCalledTimes(2);
  });
});
