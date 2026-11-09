import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { loadSitePreference, saveSitePreference, sitePreferenceKey } from "./sitePreference";

const sdk = vi.hoisted(() => ({ getData: vi.fn(), setData: vi.fn(), getSelection: vi.fn() }));
vi.mock("@lark-opdev/block-bitable-api", () => ({ bitable: {
  bridge: { getData: sdk.getData, setData: sdk.setData }, base: { getSelection: sdk.getSelection },
} }));
const scope = { baseId: "base-1", userId: "operator-1" };
const preference = (site = "AU", savedAt = 100) => ({ ...scope, version: 1, site, savedAt });
let cloud: Map<string, unknown>;
let local: Map<string, string>;
const storage = { getItem: vi.fn(), setItem: vi.fn(), clear: vi.fn() };
beforeEach(() => {
  vi.resetAllMocks(); cloud = new Map(); local = new Map();
  storage.getItem.mockImplementation((key: string) => local.get(key) ?? null);
  storage.setItem.mockImplementation((key: string, value: string) => { local.set(key, value); });
  storage.clear.mockImplementation(() => local.clear());
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
  sdk.getSelection.mockResolvedValue({ baseId: scope.baseId });
  sdk.getData.mockImplementation(async key => cloud.get(key));
  sdk.setData.mockImplementation(async (key, value) => { cloud.set(key, value); return true; });
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("站点偏好持久化，不使用任何表字段或记录写入", () => {
  it("普通账号跨设备读取云端记忆，并隔离其他账号和Base", async () => {
    expect(await saveSitePreference(scope, "AU")).toBe("cloud");
    window.localStorage.clear();
    expect((await loadSitePreference(scope.userId)).preference?.site).toBe("AU");
    expect((await loadSitePreference("operator-2")).preference).toBeNull();
    sdk.getSelection.mockResolvedValue({ baseId: "base-2" });
    expect((await loadSitePreference(scope.userId)).preference).toBeNull();
  });
  it("两个运营分别保存，不覆盖另一人的站点", async () => {
    await saveSitePreference(scope, "AU");
    await saveSitePreference({ ...scope, userId: "operator-2" }, "UK");
    expect((await loadSitePreference(scope.userId)).preference?.site).toBe("AU");
    expect((await loadSitePreference("operator-2")).preference?.site).toBe("UK");
    expect(cloud.size).toBe(2);
  });
  it.each([undefined, {}, preference("INVALID"), { ...preference(), userId: "other" },
    { ...preference(), baseId: "other" }, { ...preference(), savedAt: NaN },
    { ...preference(), version: 99 }])("忽略空值、损坏值和错误归属 %j", async value => {
    sdk.getData.mockResolvedValue(value);
    expect((await loadSitePreference(scope.userId)).preference).toBeNull();
  });
  it("云端权限拒绝时本地兜底，不向业务流程抛错", async () => {
    sdk.setData.mockRejectedValue(new Error("permission denied"));
    expect(await saveSitePreference(scope, "AU")).toBe("local");
    sdk.getData.mockRejectedValue(new Error("permission denied"));
    expect(await loadSitePreference(scope.userId)).toMatchObject({ storage: "local", preference: { site: "AU" } });
  });
  it("本地较新时不被云端旧值覆盖", async () => {
    sdk.setData.mockResolvedValue(false);
    await saveSitePreference(scope, "UK");
    cloud.set(sitePreferenceKey(scope), preference("AU", 1));
    expect((await loadSitePreference(scope.userId)).preference?.site).toBe("UK");
  });
  it("接口返回成功但回读未确认时不误报云端同步成功", async () => {
    sdk.setData.mockResolvedValue(true);
    expect(await saveSitePreference(scope, "AU")).toBe("local");
  });
  it("云端较新时更新本地缓存", async () => {
    window.localStorage.setItem(sitePreferenceKey(scope), JSON.stringify(preference("CA", 1)));
    cloud.set(sitePreferenceKey(scope), preference("AU", 2));
    expect((await loadSitePreference(scope.userId)).storage).toBe("cloud");
    expect(JSON.parse(window.localStorage.getItem(sitePreferenceKey(scope))!).site).toBe("AU");
  });
  it("浏览器禁用本地存储仍能使用云端", async () => {
    storage.setItem.mockImplementation(() => { throw new Error("blocked"); });
    storage.getItem.mockImplementation(() => { throw new Error("blocked"); });
    expect(await saveSitePreference(scope, "AU")).toBe("cloud");
    expect((await loadSitePreference(scope.userId)).preference?.site).toBe("AU");
    sdk.setData.mockRejectedValue(new Error("denied"));
    expect(await saveSitePreference(scope, "UK")).toBe("none");
  });
  it("请求超时能结束，不永久阻止新建选品", async () => {
    vi.useFakeTimers(); sdk.getData.mockReturnValue(new Promise(() => {}));
    const loading = loadSitePreference(scope.userId);
    await vi.advanceTimersByTimeAsync(2501);
    expect(await loading).toMatchObject({ scope, preference: null });
  });
  it("身份或Base缺失时不写入公共默认键", async () => {
    expect(await saveSitePreference(null, "AU")).toBe("none");
    expect(await loadSitePreference("")).toMatchObject({ scope: null });
    sdk.getSelection.mockRejectedValue(new Error("no base"));
    expect(await loadSitePreference(scope.userId)).toMatchObject({ scope: null });
    expect(sdk.setData).not.toHaveBeenCalled();
  });
});
