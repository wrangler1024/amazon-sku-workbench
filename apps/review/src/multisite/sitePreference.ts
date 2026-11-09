import { MARKETPLACES, type Marketplace } from "./domain";

export interface SitePreferenceScope { baseId: string; userId: string }
export interface SitePreference extends SitePreferenceScope {
  version: 1;
  site: Marketplace;
  savedAt: number;
}
export type PreferenceStorage = "cloud" | "local" | "none";
export interface LoadedSitePreference {
  scope: SitePreferenceScope | null;
  preference: SitePreference | null;
  storage: PreferenceStorage;
}

const TIMEOUT_MS = 2500;
export const sitePreferenceKey = ({ baseId, userId }: SitePreferenceScope) =>
  `amazon-selection:last-site:v1:${baseId}:${userId}`;

async function bounded<T>(operation: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([operation, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("站点偏好请求超时")), TIMEOUT_MS);
    })]);
  } finally { clearTimeout(timer); }
}

function valid(value: unknown, scope: SitePreferenceScope): SitePreference | null {
  if (!value || typeof value !== "object") return null;
  const p = value as Partial<SitePreference>;
  return p.version === 1 && p.baseId === scope.baseId && p.userId === scope.userId &&
    typeof p.site === "string" && Object.hasOwn(MARKETPLACES, p.site) &&
    typeof p.savedAt === "number" && Number.isFinite(p.savedAt) && p.savedAt > 0
    ? p as SitePreference : null;
}

function readLocal(scope: SitePreferenceScope): SitePreference | null {
  try { return valid(JSON.parse(window.localStorage.getItem(sitePreferenceKey(scope)) ?? "null"), scope); }
  catch { return null; }
}

function writeLocal(preference: SitePreference): boolean {
  try {
    window.localStorage.setItem(sitePreferenceKey(preference), JSON.stringify(preference));
    return true;
  } catch { return false; }
}

export async function loadSitePreference(userId: string): Promise<LoadedSitePreference> {
  const empty: LoadedSitePreference = { scope: null, preference: null, storage: "none" };
  if (!userId) return empty;
  let scope: SitePreferenceScope;
  let bitable: typeof import("@lark-opdev/block-bitable-api").bitable;
  try {
    ({ bitable } = await import("@lark-opdev/block-bitable-api"));
    const { baseId } = await bounded(bitable.base.getSelection());
    if (!baseId) return empty;
    scope = { baseId, userId };
  } catch { return empty; }
  const local = readLocal(scope);
  let cloud: SitePreference | null = null;
  try { cloud = valid(await bounded(bitable.bridge.getData(sitePreferenceKey(scope))), scope); }
  catch { /* Ordinary operators may have no plugin-data permission. */ }
  // A failed cloud write must not replace a newer local save on reopen.
  const preference = cloud && (!local || cloud.savedAt >= local.savedAt) ? cloud : local;
  if (preference === cloud && cloud) writeLocal(cloud);
  return { scope, preference, storage: preference ? preference === cloud ? "cloud" : "local" : "none" };
}

export async function saveSitePreference(scope: SitePreferenceScope | null,
  site: Marketplace): Promise<PreferenceStorage> {
  if (!scope?.baseId || !scope.userId || !Object.hasOwn(MARKETPLACES, site)) return "none";
  const preference: SitePreference = { ...scope, version: 1, site, savedAt: Date.now() };
  const localSaved = writeLocal(preference);
  try {
    const { bitable } = await import("@lark-opdev/block-bitable-api");
    if (await bounded(bitable.bridge.setData(sitePreferenceKey(scope), preference))) {
      const stored = valid(await bounded(bitable.bridge.getData(sitePreferenceKey(scope))), scope);
      if (stored?.site === site && stored.savedAt === preference.savedAt) return "cloud";
    }
  } catch { /* A preference failure must never fail a saved product's submission. */ }
  return localSaved ? "local" : "none";
}
