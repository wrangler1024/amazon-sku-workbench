# 官方来源与实现依据

核查日期：2026-10-31；费率版本：2026-10-31.1。

| 范围 | 官方依据 | 本地证据 |
|---|---|---|
| 欧洲/英国 10 站 | [2026-07-01 官方费率卡](https://m.media-amazon.com/images/G/02/sell/images/260630-FBA-Rate-Card-EN1.pdf)，第 5 页低价、第 6 页常规、第 7 页英国/德国旺季、第 16 页爱尔兰 | `eu-page-*-tables.json`、`eu-page-*.txt` |
| AU | [完整 FBA 配送费表](https://sellercentral.amazon.com.au/help/hub/reference/external/201112670?mons_sel_locale=en_AU)、[2026-06-10 费率变更](https://sellercentral.amazon.com.au/help/hub/reference/external/G4UU2ZQNHJNKAG5Y) | `au-fees-20261031.json`；三站上一轮核验后的普通件数据 `au-reviewed-standard-20261031.json` |
| CA | [2026 常规/旺季](https://sellercentral.amazon.ca/help/hub/reference/external/GABBX6GZPA8MSZGW)、[体积重与尺寸](https://sellercentral.amazon.ca/help/hub/reference/external/G59RXFJCSKH9E9PK)、[低价减免](https://sell.amazon.ca/pricing) | `ca-official-20261031.json`、`ca-dimensional-20261031.json`、`ca-reviewed-rates-20261031.json` |
| US | [2026 常规/旺季费表](https://sellercentral.amazon.com/help/hub/reference/external/GABBX6GZPA8MSZGW)、[体积重](https://sellercentral.amazon.com/help/hub/reference/external/G53Z9EKF8VVZVH29)、[计费重](https://sellercentral.amazon.com/help/hub/reference/external/GEVWP48HPBLEFJEY) | `us-official-20261031.json`、`us-dimensional-20261031.json`、`us-shipping-20261031.json` |
| MX | [现行配送费用](https://sellercentral.amazon.com.mx/help/hub/reference/external/G200209150?mons_sel_locale=es_MX)、[2026 变更与 7 月续重价格](https://sellercentral.amazon.com.mx/help/hub/reference/external/G201411300?mons_sel_locale=es_MX) | `mx-fees-20261031.json`、`mx-changes-20261031.json`；脚本逐项比较两表所有普通商品金额 |

欧洲费率从官方 PDF 的单元格提取，生成脚本重新映射各站列、尺寸条件和重量档。PDF 将金额小数点单独分行，脚本将金额转为整数分。每条来源包含正式 URL，欧洲/爱尔兰同时给出页码。

参考过 [Patrick-Zuo-hub/FBA-Fee-Calculator](https://github.com/Patrick-Zuo-hub/FBA-Fee-Calculator) 的站点拆分与 [ShopTrueProfit](https://github.com/PravinrajV/shoptrueprofit-fee-mcp) 的接口思路。本服务计算实现、校验、HTTP 路由与数据组织独立编写，没有将前者无许可证源码作为依赖，也没有使用后者托管服务。

本次没有照搬社区项目的加拿大低价遗漏、墨西哥旧续重价格或将美国标准件套用大件的最小宽高。英国官方 PDF 自身的小号超大件续重冲突通过明确返回需报价处理。爱尔兰当前促销价在官方第 16 页明示有效，并可能以 30 天通知调整，因此结果单独提示此来源条件。

本版不映射企业内部业务品类，不默认套用指定类目包裹优惠。欧洲扩展低价资格和德国 CEP 由明确输入控制；其余特殊计划不受支持。完整范围及税费说明见 README。

维护时应先取得更新的官方证据，再新增或替换版本化数据及相关测试预期。不得以 GitHub 最近更新时间推断全表有效，应保留旧版本以支持历史追溯。

本地证据列列出开发核验时的输入文件名；官方原文、PDF提取结果与私人部署记录不随开源仓库分发。内置数值快照可直接运行，后续维护需重新取得并审阅官方证据。

## AE / SA / JP extension (snapshot 2026-10-31.2)

- AE: [official fees and worked examples](https://sellercentral.amazon.ae/help/hub/reference/external/201112670), [size tiers](https://sellercentral.amazon.ae/help/hub/reference/external/201105770), [public pricing](https://sell.amazon.ae/pricing). Published fees effective2025-08-01; packaged unit weight selects the fee band. The general size-tier article discusses dimensional weight, but the fee article and worked examples explicitly use unit weight (including a monitor with higher dimensional weight). Do not transplant another marketplace's shipping-weight calculation. Large-envelope regular price conflicts between pricing (7.50 AED) and help (9.00 AED); this case returns quote_required. Above30kg admission/increment rules are outside this reviewed implementation.
- SA: [official fees and worked examples](https://sellercentral.amazon.sa/help/hub/reference/external/201112670), [size tiers](https://sellercentral.amazon.sa/help/hub/reference/external/201105770), [public pricing](https://sell.amazon.sa/pricing). Same unit-weight method, distinct fees. Both price columns leave exactly25 SAR undefined, so that boundary returns quote_required. AE/SA fees exclude VAT.
- JP: [current non-frozen fees](https://sellercentral.amazon.co.jp/help/hub/reference/external/201112670), [size-tier limits](https://sellercentral.amazon.co.jp/help/hub/reference/external/G201105770), [dated2026-04-01 changes](https://sellercentral.amazon.co.jp/help/hub/reference/external/GRH54FUTD3C5LWWW), [public pricing](https://sell.amazon.co.jp/pricing). All22 fee rows match the dated change table. The dated/current help tables take priority over old marketing examples. Standard individual-side limits and three-side sums are both enforced; supersize supports the officially listed400cm/50kg band, with250cm longest-side limit. Frozen fees are excluded. Charges include10% consumption tax. JPY uses exponent0 and totalMinorUnits is whole yen.

The publisher stores raw help responses/pricing pages locally outside Git. `scripts/build-ae-sa-jp.py` rebuilds the additional numeric data from reviewed extracted tables and cross-checks the Japanese change table. The original14 market definitions are preserved byte-for-byte as JSON values. Existing business registries are not migrated or written.
