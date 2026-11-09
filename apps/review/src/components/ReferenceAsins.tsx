import { parseReferenceAsins } from "../domain/referenceAsins";
import { MARKETPLACES, type Marketplace } from "../multisite/domain";

export default function ReferenceAsins({ value, site }: { value: string; site: Marketplace }) {
  const { links, invalid } = parseReferenceAsins(value, site);
  return <aside className="review-reference-asins" aria-label="参考竞品">
    <div className="review-reference-list">
      {links.map((link, index) => <a key={`${link.site}:${link.asin}`} href={link.href}
        target="_blank" rel="noopener noreferrer" className={link.site !== site ? "cross-site" : undefined}
        title={`参考竞品 · ${MARKETPLACES[link.site].label}${link.site !== site ? "（跨站参考）" : ""} · ${link.asin}；新标签页打开`}>
        <span className="reference-site">{link.site}</span><span className="reference-ordinal">{index + 1}</span><code>{link.asin}</code><span aria-hidden="true">↗</span>
      </a>)}
      {invalid.length > 0 && <span className="reference-invalid" title={invalid.join("\n")}>格式待核对</span>}
      {!links.length && !invalid.length && <span className="reference-empty">竞品未填写</span>}
    </div>
  </aside>;
}
