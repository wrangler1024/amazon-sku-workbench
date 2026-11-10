# Amazon SKU Workbench

亚马逊选品评审、内容编辑与制图协作工作台，包含独立 FBA 费用计算和云邮箱模块。中文界面，主体采用 Apache-2.0，FBACC 模块保留 MIT 许可证。

当前版本从已有业务工作台整理而来：评审前端通过飞书多维表格 SDK 工作；编辑工作台使用独立的 FastAPI / PostgreSQL 服务，通过飞书接入身份和业务数据。**当前还不是完全脱离飞书的一体化 SKU 系统**，迁移边界见 [架构与迁移路线](docs/architecture.md)。

## 功能

- **选品评审**：多站点选品、成本及利润测算、选品说明、提审、打回重提、主管评审；财务参考不会覆盖运营手填成本。
- **内容编辑**：产品资料、多个1688基础商品与规格组合、手动 Listing / 图需编辑、OpenAI / DeepSeek 生成、套图策划建议。
- **制图协作**：公共池、两条未开工自主认领上限、负责人指派、开工、原图交付、逐图返修、验收与取消误验收。
- **刊登记录**：文案与图片独立登记、撤回、刊登时间、复制五点、长描述HTML、原图整套下载和历史版本。
- **后台服务**：岗位与数据范围、并发任务、来源采集、图片缓存、协作通知、审计记录及迁移导出。
- **FBACC 计算器**：17站普通商品 FBA 费用测算、独立网页、纯计算函数、HTTP API、费率来源核验工具；可单独运行，不依赖飞书或工作台数据库。
- **云邮箱**：独立的聚合收件箱、风险看板、整邮箱授权、飞书认证和个人提醒；保留独立登录及SQLite存储，尚未与工作台统一登录。

手动文案和图需可直接确认；AI生成仍需先确认产品资料。列表使用服务端分页，制图列表每10秒静默刷新，不覆盖正在编辑的草稿、返修意见或图片上传。

## 仓库结构

```text
apps/
  review/                          选品评审前端（React / TypeScript）
  editor/                          编辑、制图及刊登前端（React / TypeScript）
services/
  amazon-aigc-workbench-service/    独立 API、任务处理、SQL迁移、Python测试
  amazon-fbacc/                    独立 FBA 计算器、网页、API和费率核验工具（MIT）
  store-mail-web/                  云邮箱网页、服务、SQLite和独立授权
  amazon-erp-material-service/     ERP自动建料模块规划（仅文档，未实现）
integrations/feishu/               飞书接入说明和配置模板
integrations/lingxing/             领星能力与接入边界规划（未实现）
scripts/                          打包清单、版本与公开边界检查
deploy/                          本地/自建部署模板
docs/                            架构、接入、发布及迁移说明
```

采用单仓库、多模块版本管理。每个模块可以独立测试、发布和部署；`versions.json`记录经过验证的组合，详见 [版本管理](docs/versioning.md)。仓库不包含企业凭证、员工名单、商品记录、原图、生产备份或私有Git历史。

## 本地开发

需要 Node 22.18+、Python 3.12+、uv，以及 Docker 或本地 PostgreSQL。

```sh
npm run install:apps
npm test
npm run build

# 评审台本地模拟数据（不写飞书）
npm run dev:review
# 浏览器打开 http://127.0.0.1:5173/?mock=1

# 另一个终端启动独立计算器；评审台AE本地代理默认连接此端口
npm run dev:fbacc
# 计算器页面 http://127.0.0.1:4318

# 云邮箱离线演示：临时数据库、虚构邮件，不连接真实邮箱/飞书
npm run dev:mail
# http://127.0.0.1:8791  demo / demo-local-only
```

评审模拟入口只演示本地可用页面；实时多站点数据和流程仍需按 [飞书接入说明](integrations/feishu/README.md) 配置。费率 JSON 是有日期的测试/参考样本，部署时应配置自己的生效费率，不能当作持续更新的官方报价。

后台启动：

```sh
cp .env.example .env
# 编辑 .env，设置本地数据库密码；不要提交此文件

docker compose --env-file .env -f deploy/compose.yaml up -d db
cd services/amazon-aigc-workbench-service
uv sync --locked
# 将 .env 中的 DATABASE_URL 设置到当前终端
export DATABASE_URL='postgresql://workbench:YOUR_PASSWORD@127.0.0.1:15439/workbench'
uv run python -m amazon_aigc.cli migrate
uv run uvicorn amazon_aigc.api:create_app --factory --host 127.0.0.1 --port 8798
# GET http://127.0.0.1:8798/health
```

另一个终端执行 `npm run dev:editor`。业务登录需要配置飞书 OAuth 或原生插件身份验证及人员映射；空数据库没有默认管理员，也没有公开通用密码。完整容器构建可执行：

```sh
docker compose --env-file .env -f deploy/compose.yaml up -d --build api
```

模型、采集 worker、飞书同步和通知默认不启动。完成自己的接入配置后，再按需启用 `workers`、`feishu`、`notifications` profiles。通知必须先在管理后台启用；勿对真实员工进行自动化测试。

## 测试

前端：`npm test`。后台测试使用独立随机schema，不截断业务表：

```sh
cd services/amazon-aigc-workbench-service
export AIGC_TEST_DATABASE_URL='postgresql://workbench:YOUR_PASSWORD@127.0.0.1:15439/workbench'
uv run pytest -q
```

只允许本地测试数据库地址。未设置测试数据库时，依赖数据库的测试会跳过；完整CI提供临时PostgreSQL并运行全部测试。

```sh
python3 scripts/check_versions.py
python3 scripts/check_public.py  # 检查Git追踪的文件
npm run check:fbacc
npm run test:fbacc:integration  # 真实计算器 + 评审适配器 + Vite代理
npm run test:mail              # 临时SQLite、模拟IMAP/飞书
npm run build:mail             # 独立wheel及源码包
```

## 接入与部署

- [飞书接入和字段约定](integrations/feishu/README.md)
- [架构、数据归属和独立系统迁移](docs/architecture.md)
- [版本、发布与回退](docs/versioning.md)
- [部署说明](docs/deployment.md)
- [FBACC 独立模块与接入](docs/fbacc.md)
- [云邮箱独立模块与集成边界](docs/cloud-mail.md)
- [ERP自动建料模块规划（未实现）](docs/erp-materials.md)
- [安全说明](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [更新记录](CHANGELOG.md)

OpenAI、DeepSeek和采集服务的API密钥只保存在服务端。源站访问权限、供应商配额、生成质量及亚马逊内容合规需由使用者自行核对；AI候选不等于已确认商品事实或最终成品图。

## 许可证

除另有标注的模块外，使用 [Apache License 2.0](LICENSE)。`services/amazon-fbacc` 保留原 [MIT 许可证](services/amazon-fbacc/LICENSE)及[来源说明](services/amazon-fbacc/NOTICE)，相关贡献按 MIT 发布。依赖项使用各自许可证。项目与 Amazon、Feishu/Lark、OpenAI、DeepSeek 没有官方隶属关系。
