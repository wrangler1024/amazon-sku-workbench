import type { Marketplace } from "../multisite/domain";

const domains: Record<Marketplace, string> = {
  CA: "www.amazon.ca", MX: "www.amazon.com.mx", UK: "www.amazon.co.uk",
  US: "www.amazon.com", AU: "www.amazon.com.au", AE: "www.amazon.ae",
  SA: "www.amazon.sa", JP: "www.amazon.co.jp",
};
export interface ReferenceAsin { asin: string; site: Marketplace; href: string }
/** Only recognized Amazon product URLs or complete ASIN tokens become links. */
export function parseReferenceAsins(raw: string | null | undefined, defaultSite: Marketplace) {
  const links: ReferenceAsin[] = [];
  const invalid: string[] = [];
  const seen = new Set<string>();
  for (const token of (raw ?? "").trim().split(/[\s,，;；、]+/).filter(Boolean)) {
    let asin = token.toUpperCase();
    let site = defaultSite;
    if (/^https?:\/\//i.test(token)) {
      try {
        const url = new URL(token);
        const matchedSite = (Object.keys(domains) as Marketplace[]).find((key) =>
          [domains[key], domains[key].replace("www.", ""), domains[key].replace("www.", "m.")].includes(url.hostname));
        const match = url.pathname.match(/\/(?:dp|gp\/product|gp\/aw\/d)\/([a-z0-9]{10})(?:\/|$)/i);
        if (!matchedSite || !match || url.username || url.password || url.port) {
          invalid.push(token); continue;
        }
        site = matchedSite; asin = match[1].toUpperCase();
      } catch { invalid.push(token); continue; }
    }
    if (!/^[A-Z0-9]{10}$/.test(asin)) { invalid.push(token); continue; }
    const key = `${site}:${asin}`;
    if (!seen.has(key)) {
      seen.add(key); links.push({ asin, site, href: `https://${domains[site]}/dp/${asin}` });
    }
  }
  return { links, invalid };
}
