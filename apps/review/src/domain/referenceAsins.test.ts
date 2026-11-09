import { describe, expect, it } from "vitest";
import { parseReferenceAsins } from "./referenceAsins";

describe("参考 ASIN 跳转", () => {
  it.each([['CA','amazon.ca'],['MX','amazon.com.mx'],['UK','amazon.co.uk'],['US','amazon.com'],['AU','amazon.com.au'],['AE','amazon.ae'],['SA','amazon.sa'],['JP','amazon.co.jp']] as const)("纯 ASIN 使用 %s 目标站点", (site, domain) => {
    expect(parseReferenceAsins('b0test0005', site).links[0].href).toBe(`https://www.${domain}/dp/B0TEST0005`);
  });
  it("兼容中英文分隔符并按站点和 ASIN 去重", () => {
    const parsed = parseReferenceAsins('B0TEST0005，b0test0005\nB0TEST0006; B0TEST0004、B000000002', 'CA');
    expect(parsed.links.map(x => x.asin)).toEqual(['B0TEST0005','B0TEST0006','B0TEST0004','B000000002']);
    expect(parsed.invalid).toEqual([]);
  });
  it("完整链接保留竞品站点，并移除追踪参数", () => {
    const parsed = parseReferenceAsins('https://www.amazon.com/title/dp/B0TEST0005/ref=xx?tag=abc https://amazon.co.uk/gp/product/B0TEST0006 https://m.amazon.com.au/gp/aw/d/B0TEST0004/', 'CA');
    expect(parsed.links.map(x => [x.site,x.href])).toEqual([
      ['US','https://www.amazon.com/dp/B0TEST0005'],['UK','https://www.amazon.co.uk/dp/B0TEST0006'],['AU','https://www.amazon.com.au/dp/B0TEST0004']]);
  });
  it("异常、非商品地址和仿冒域名不生成链接", () => {
    const bad=['B0BAD','https://amazon.com.evil.test/dp/B0TEST0005','javascript:alert(1)','https://www.amazon.com/s?k=B0TEST0005','https://a@www.amazon.com/dp/B0TEST0005','https://www.amazon.de/dp/B0TEST0005'];
    expect(parseReferenceAsins(bad.join(' '),'CA')).toEqual({links:[],invalid:bad});
  });
  it("空值不生成链接，同一 ASIN 跨站仍保留两条", () => {
    expect(parseReferenceAsins('  ', 'CA')).toEqual({links:[],invalid:[]});
    expect(parseReferenceAsins('B0TEST0005 https://amazon.com/dp/B0TEST0005', 'CA').links).toHaveLength(2);
  });
});
