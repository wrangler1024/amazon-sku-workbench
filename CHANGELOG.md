# Changelog

## Review 0.72.0 / FBACC 0.3.1

- Add DE/EUR review entry, Amazon.de references and date-aware domestic FBA estimates through FBACC; manual FBA costs stay authoritative. Preserve the released draft identity confirmation when changing products.
- Apply the official European 2% fuel surcharge from 2026-11-02 while retaining earlier rates, German CEP/peak distinctions, and existing non-European tables.
- Validate DE financial calculations, proxy integration, response assumptions and cross-market async cancellation with synthetic fixtures.


## 0.6.0

- API 0.25.0：新增可独立启用的 Base 事件处理进程，接管经过审核的提审、结果处理、时间、撤回、寄样、ERP 校验与截止提醒规则。
- 新增持久事件、操作日志和通知队列；按提交版本去重，拒绝过期写入，通知失败独立重试。
- SQL 019 为新增表；历史记录只建立基线，保留原有 ERP/制图进度，迁移须按部署说明逐步切换。

## 0.5.0

- API 0.24.0 / editor 0.23.0：运营核对说明与面向买家的Listing正文分开返回和展示，已确认尺寸正常保留，未确认参数先省略。
- 复制前检查内部核对语句和明确待确认数值，覆盖单字段、纯文本、HTML、历史及刊登文案；保留人工采用、编辑、保存和确认。
- 提示词v14与listing-quality-2；不改写历史内容、不增加自动纠正调用或数据库迁移。

## 0.4.1

- API 0.23.1：补齐英文及西语未确认适配措辞，防止将未确认的来源型号列入保留名单。编辑前端、提示词和数据库结构保持兼容。

## 0.4.0

- API 0.23.0 / editor 0.22.0：文案与图需共同隔离来源型号、货号，保留有效规格和已确认适配型号；原始采集与规格绑定不改写。
- 五点通过小标题和说明分别生成后组合成 SOP 格式，保留英文 Title Case 与西语习惯。
- 文案候选和编辑页提示事实覆盖、未确认数值及无依据“另售”等疑点；复制来源编号前须人工处理，保存与确认保持原流程。
- 提示词 v13；无新增纠正用模型调用或数据库迁移。

## 0.3.4

- API 0.22.2：英文 Listing 标题和 Item Highlights 整条采用 Title Case，明确小词首尾、标准缩写、型号、单位及尺寸连接符的例外；墨西哥西语和其他非英语遵循当地大小写习惯。
- OpenAI 与 DeepSeek 共用提示词 v12；不新增自动改写或纠正用模型调用，已保存文案及历史版本保留。
- 编辑前端与数据库结构不变；部署时同时更新 API 和 model worker，核对等待任务的输入版本兼容性。

## 0.3.3

- api 0.22.1：将白牌图需与既有负责人交接功能整合，保留 SQL 018、人员映射及交接日志。
- editor 保持已验证的 0.21.2，生成、人工核对和复制规则不变。


## 0.3.2

- editor 0.21.2 / api 0.21.1：图需默认白牌，隔离来源标识与旧图需要求；包装不明确时不策划包装图。
- 候选与编辑稿提示疑似品牌、平台及链接；不自动修改内容、不追加模型调用，仍可采用、编辑和确认。
- 默认制作复制文本移除独立来源附录，复制前检查未处理标识；原始资料及历史版本保留。
- 本组合基于已提交的 0.3.1，用于隔离联调；不包含其他工作区尚未提交的负责人交接改动。


## Unreleased — Editor 0.21.1

- Add consistent product-thumbnail hover previews to all editor headers, product
  queues, artwork/review detail pages and publication lists.
- Keep the preview inside the viewport, close it on scroll, resize or Escape, and
  retain click-to-toggle behavior for touch and keyboard use.
- Add synthetic geometry tests and editor build verification. API, database schema,
  identities, images and production deployment remain unchanged.

## Unreleased — Planning only

- Document the ERP material-creation workflow and independent module boundary,
  including unit/cost confirmation, material reuse, partial-failure recovery,
  Lingxing capability checks and future migration away from Feishu.
- Reserve `services/amazon-erp-material-service` and `integrations/lingxing` for
  documentation only. No executable code, API, schema, UI or deployment change;
  existing package versions and runtime behavior remain unchanged.

## Bundle 0.3.0 — 2026-11-10

- Add `services/store-mail-web` as an independent cloud-mail module, version
  0.13.1 under Apache-2.0, from an allowlisted sanitized source snapshot.
- Keep its native frontend, Python HTTP service, SQLite storage, mailbox grants,
  admission, supervisor pilot restrictions and notification behavior independent.
- Remove internal release records, tenant ledger tooling and named-person wording;
  use synthetic test identities and generic configuration/deployment examples.
- Add locked dependencies, module tests/CI, installed-wheel HTTP checks, independent
  packaging and public-boundary checks for mail/database artifacts.
- Existing review/editor/API/FBACC versions remain unchanged. No live deployment,
  mailbox data or permissions change; unified login and database migration are deferred.

## Bundle 0.2.0 — 2026-11-10

- Import FBACC 0.3.0 as `services/amazon-fbacc`, retaining its MIT license,
  source provenance, independent calculator UI/API and 17-market rate snapshot.
- Record monitoring toolkit 0.2.0 and rate snapshot 2026-10-31.2 independently;
  calculation code and fee data are unchanged from the upstream baseline.
- Review 0.70.2: align the local AE proxy with FBACC port 4318 and load its
  server-side environment configuration. Production endpoint behavior is unchanged.
- Add real review-adapter/proxy/calculator integration verification, module CI,
  independent container startup and Git-only standalone release packaging.
- Editor/API remain 0.21.0. No database migration, live deployment, Feishu
  publication or rate-monitor scheduling change is included.

## Bundle 0.1.0 — 2026-11-09

First public source baseline, released under Apache-2.0.

- Review 0.70.1: product selection, marketplace cost estimates, rationale and review queues.
- Editor 0.21.0: product facts, manual/AI Listing and artwork briefs, task allocation,
  delivery/review, separate copy/image publication records and historical queries.
- API 0.21.0: PostgreSQL domain model, asynchronous generation/collection, permissions,
  versioned assets, Feishu adapters and migration/export tooling.
- Public configuration templates replace company-specific endpoints and identifiers.
- Tests use synthetic product/employee data and a disposable PostgreSQL schema.

This is a sanitized source baseline. Enterprise application release numbers are not public
package versions, and no private Git history or production records are included.
