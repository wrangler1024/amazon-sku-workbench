# Amazon FBACC

独立的亚马逊 FBA 单件配送费计算服务，输入自定义包装、站点、本币售价和预计售出日期，无需 ASIN 或已上架 SKU。提供 JavaScript 函数、TypeScript 类型及 HTTP JSON 接口。

在线计算器：**https://fbacc.samforo.icu**；[公开 OpenAPI](https://fbacc.samforo.icu/openapi.json)。

当前版本 `0.3.0`，费率快照为 `2026-10-31.2`，支持 **AU、US、CA、MX、UK、DE、FR、IT、ES、NL、SE、PL、BE、IE 以及 AE、SA、JP 共 17 站**。运行时无第三方依赖，计算不请求外部服务。


## 网页使用

- 单站测算：填写包装三边、重量、本币售价和预计售出日期，查看费用明细、尺寸档、计费重及官方来源。
- 17 站对比：同一包装、各站独立本币售价批量测算，支持 CSV 导出。切换单站会清空售价，避免币种串用。
- 支持 cm/in 与 g/kg/lb/oz 换算；US/CA/MX隐藏低价资格选项，AU提供自动判断/不使用两项，UK/欧洲显示本币基础及扩展门槛。多站对比逐站选择资格，德国单独显示CEP；修改输入后旧结果自动失效。
- 缺少售价不计算；规则冲突或不支持的规格显示“需要官方报价”，不会显示为零元。桌面与手机布局已验证。

## 启动与调用

要求 Node.js **22.14 或以上**。无需 `npm install`。

```bash
npm run check
npm test
npm start
```

默认监听 `127.0.0.1:4318`。另一终端调用：

```bash
curl http://127.0.0.1:4318/v1/fba/calculate \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request.json
```

样例为加拿大、CAD 13.99、30×20×5 cm、200 g、2026-10-31，返回 **CAD 7.29**：旺季基础费 7.84，低价减免 0.80，燃油附加费 0.25。输出含币种、尺寸档、体积重、计费重、费用明细、费率版本、官方来源及适用限制。

直接调用：

```js
import { calculateFbaFee } from './src/index.mjs';

const result = calculateFbaFee({
  marketplace: 'CA',
  price: { amount: 13.99, currency: 'CAD' },
  package: {
    dimensions: { length: 30, width: 20, height: 5, unit: 'cm' },
    weight: { value: 200, unit: 'g' }
  },
  saleDate: '2026-10-31',
  category: 'general'
});
if (result.status === 'estimated') console.log(result.fee.total);
else console.log(result.reason);
```

后续工程可通过本地 package 依赖引用 `@samforo/fba-fee-calculator`。类型位于 `src/index.d.ts`，示例见 `examples/typed-client.ts`。纯计算函数与 HTTP 使用同一实现。

## 接口约定

| 方法与路径 | 用途 |
|---|---|
| `POST /v1/fba/calculate` | 单笔测算 |
| `POST /v1/fba/calculate-batch` | 批量测算，`{"items":[请求1,请求2]}`，1–100 笔 |
| `GET /v1/fba/marketplaces` | 17 站的币种、支持尺寸档、费率日期、来源与限制 |
| `GET /openapi.json` | OpenAPI 3.1 文档 |
| `GET /healthz` | 服务与费率版本 |
| `GET /readyz` | 17 站适配器就绪状态；退出期间返回 503 |

- `saleDate` 是站点当地日历日期，格式 `YYYY-MM-DD`，用于预计售出/履约时的费率选择；不是入库日期。接口不从服务器时区猜测日期。
- 尺寸支持 `cm/in`，重量支持 `g/kg/lb/oz`；输入为**完整销售单元包装后的尺寸和重量**。三边自动按降序排列，套装须输入整套包装数据。
- 售价必须与站点币种匹配，JPY 必须为整数日元，其他币种最多两位小数；接口不做汇率转换。UK 也接受 `GB` 别名。
- `category` 必填。`general` 表示普通、非服装、非危险品的基础费率组；不把业务品类映射成官方费用类目。特殊类目返回 `quote_required`。
- `program` 默认 `domestic`，本版不计算 EFN、跨境远程配送、Pan-EU 附加计划、SIPP、危险品或服装专属规则。
- 欧洲/英国默认 `options.lowPriceEligibility="conservative"`：采用各站低门槛，以免在未确认官方品类时错误享受扩展低价优惠。已单独确认资格可传 `confirmed_eligible`；`ineligible` 禁用低价档。AU 和 CA 采用普通费率组的公开低价规则。
- 德国默认 `options.centralEuropeProgram=false`，按 Germany-only 列计价；只有已确认 CEP 资格时才传 `true`。其余站点不接受该选项。

结果分两类，均返回 HTTP 200：

- `estimated`：有金额，`fee.totalMinorUnits` 为整数最小货币单位（JPY 为日元，其余为分），`fee.total` 为本币金额；附带假设与警示。
- `quote_required`：没有可可靠计算的适用档位或规则，`fee=null`，必须处理 `reason`，不能把它当作零元。

参数错误返回 HTTP 422，错误 JSON 为 400，媒体类型错误为 415，超过 128 KiB 为 413。批量按输入顺序返回：`ok=false` 表示该笔输入错误；`ok=true` 的业务结果仍可能是 `quote_required`。

公开网关对 `/v1/` 按来源 IP 限制 8 请求/秒，允许 24 个突发请求；超限返回 429 和 `Retry-After: 2`。请优先使用批量接口并按响应头退避。每个应用响应带 `X-Request-Id` 和 `X-FBA-Rate-Version`。当前页面同源调用，未开放跨域浏览器访问；其他业务应通过服务端接入。

## 已支持范围与口径

| 站点 | 计费及范围 |
|---|---|
| AE / SA | 按完整销售包装实重匹配信封、标准包裹、超大件，支持至30kg。AE售价≤25 AED使用低价列；SA售价<25 SAR使用低价列，恰好25 SAR需官方报价。费用未含VAT。AE大信封售价>25 AED时官方来源金额冲突，返回需报价。 |
| JP | 普通非冷冻商品；小型、标准薄件及三边合计分档，大型至200cm/40kg，特大型至400cm/50kg且最长边≤250cm。标准件同时满足45×35×20cm限制。售价≤1,000 JPY自动适用对应列，费用含10%消费税。 |
| AU | 信封、包裹、小/标准/大超大件、Extra large；普通商品 `<AUD 13` 与常规价格。信封实重，其他取实重/体积重较大值，除数 4000，按克进位；Extra large 实重须 `<250 kg`。超普通入仓限重/尺寸单独提示资格核验。 |
| CA | 信封、标准件、小/中/大超大件；除数 5000，标准件按 100 g、超大件按 500 g 进位，低于 CAD 14 先减 0.80 再加燃油。特殊超大件需报价。 |
| US | Small/Large standard、Small/Large bulky、Extra-large 至 150 lb；三档售价 `<10`、`10–50`、`>50`。体积重除数 139；普通大号标准件不强加 2 英寸最小宽高，大件计算体积重采用该下限。Overmax 与 Extra-large 150+ lb 需报价。 |
| MX | 信封、标准件、大件，售价 `<150`、`150–<299`、`299–<499`、`≥499 MXN`；使用 2026-07-06 生效续重价格。≤0.5 kg 的信封/标准件按实重，更重及大件取实重/体积重较大值，除数 5000。费用已含 IVA，不重复加税或美国远程配送附加费。 |
| UK / DE / FR / IT / ES / NL / SE / PL / BE | 普通信封、标准包裹及普通超大件，保留单独体积重上限；信封和低价按实重，普通包裹/超大件除数 5000。英国和德国读取已公布旺季列；不把其旺季金额扩散到其他站点。特殊超大件需报价。 |
| IE | 独立的爱尔兰信封、标准包裹、小/标准超大件，使用官方当前有效的促销国内费率。大/特殊超大件未纳入，返回需报价。 |

**已知官方冲突**：英国小号超大件续重单价在同一官方 PDF 第 6、7 页分别为 GBP 0.25/kg 与 0.22/kg。本版仅该档计费重量超过 760 g 时返回 `OFFICIAL_SOURCE_CONFLICT`；不擅自取一个数。760 g 内基础费可正常计算。

燃油附加费按已公布日期启用，US/CA 为 3.5%，欧洲/英国为 1.5%；2026 旺季日期固定为 2026-10-15 至 2027-01-14。旺季结束回到现有常规表，**不会把今年旺季自动套用到下一年**。未来日期会提示 `FUTURE_DATE`；2027-01-15 以后额外提示 `NEW_YEAR_RATE_REVIEW`，并不代表已核验未来新费率。

只计算单件配送费和已适用燃油附加费，不计算佣金、仓储、入库配置、退货、移除、低库存等费用。MX 费表已含 IVA、JP 费表已含10%消费税；其余站点不另外加 VAT/GST；以 `fee.includesTax` 为准。新品结果基于输入包装，亚马逊后续实测可能改变尺寸档和实际结算。

## 数据来源与维护

费率数字、尺寸条件和生效时间来自亚马逊官方来源。社区项目只作为结构与核查方向的参考，未打包或执行无许可证项目的计算代码。详见 [来源与维护说明](docs/sources.md)。

- `rates/2026-10-31.2.json`：运行时唯一费率快照，整数最小货币单位存价（JPY 为日元，其他币种为分）；服务离线可计算。
- `evidence/`：官方页面提取、欧洲 PDF 表格、三站既有核验数据与本次验证记录。
- `scripts/build-reviewed-rates.py`：从已审阅的本地证据重新组织数据。原始官方页面/PDF不随仓库分发；该维护脚本需要自备其列出的输入文件，运行计算器不需要这些文件。
- `scripts/check-rates.mjs`：检查站点、金额、单位、分档顺序与来源；修改费率后运行该检查及 `npm test`。
- `infra/rates/`：独立来源官方来源巡检、原文/表格差异、待审核草稿、隔离验证与管理员审核发布脚本，详见[定时核验说明](infra/rates/README.md)。该流程自动检查来源，费率值仍须审阅后更新。

费用更新应新增快照版本、更新生效日期和官方证据、增加变化档位的独立预期值测试，再发布服务版本。定时任务只写独立证据和候选目录，没有正式费率发布权限。运行时不会从社区仓库拉取或自动覆盖费率。

## 运行配置

| 环境变量 | 默认值 / 用途 |
|---|---|
| `FBA_CALCULATOR_HOST` | `127.0.0.1` |
| `FBA_CALCULATOR_PORT` | `4318`；示例部署为 `8791` |
| `FBA_CALCULATOR_PUBLIC_URL` | 空；设置后写入 OpenAPI 的服务地址 |
| `FBA_CALCULATOR_ACCESS_LOG` | 默认关闭；`1` 启用包含请求 ID、路由、状态与耗时的结构化日志 |
| `FBA_CALCULATOR_API_KEY` | 默认空；设置后，除健康/就绪检查外的 JSON 接口要求 Bearer 认证 |

示例部署为公开、无状态的计算器，密钥为空，前端不存放 API 凭证。日志不记录请求正文、认证头或查询参数。私有接入应另配服务端凭证，不把密钥填进浏览器代码。

服务使用独立 systemd 用户，绑定回环地址，经 Nginx HTTPS 提供网页和 API。具备开机启动、失败重启、内存/CPU 限额、日志轮转、证书自动续期、版本目录与回滚机制。详见 [部署与回滚说明](deploy/linux/README.md)。Dockerfile 作为备用部署方式，本次实际运行 Node.js 22.22.2 + systemd。

本地与 Linux均通过 60 项测试；公网 17 站批量结果与本地一致、5 个页面资源哈希匹配，单站/对比/CSV/移动端及异常输入经过验证。公网检查可重用 `node scripts/check-deployed.mjs https://fbacc.samforo.icu`。费率仍采用已审阅的官方快照，未接入官方实时费率 API，也未启用自动费率同步。

## 开源与贡献

代码采用 [MIT](LICENSE) 许可证。费率来源、第三方依赖和商标说明见 [NOTICE](NOTICE)。本项目不是亚马逊官方产品。欢迎通过 Issue/PR 提交包含官方来源、生效日期和独立预期值的修正。

三站扩展来自重新核对的官方费表与算例，未读取业务系统作为运行依赖。原14站快照仍保存在 `rates/2026-10-31.json`；新增快照逐项保留其费率定义。维护扩展数据使用 `scripts/build-ae-sa-jp.py`，需自行取得其声明的官方证据输入。
