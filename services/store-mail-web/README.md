# 云邮箱独立模块

版本0.13.1，Apache-2.0。多店铺163邮箱聚合、邮件分类与风险看板、整邮箱授权、飞书登录和个人提醒、Agent HTTP API。源码和前端由本模块独立维护，可单独运行、测试、打包及部署。

本模块使用Python标准库HTTP服务、原生ES模块网页和SQLite，运行依赖为`cryptography`。不依赖编辑后台的PostgreSQL，也未对接workbench统一登录。现有部署的数据、密码、主密钥、会话和邮箱授权不会因代码导入自动发生变化。

## 本地离线演示

```sh
# 在本模块目录执行；uv管理隔离环境及锁定依赖
uv sync --locked --python 3.12
uv run --locked python scripts/dashboard_demo.py --port 8791
```

打开`http://127.0.0.1:8791`，使用`demo` / `demo-local-only`。演示只使用临时数据库、生成的密钥和虚构邮箱，不启动IMAP同步或飞书通知；退出后清理临时数据。它不读取部署环境的`.env`。也可在monorepo根目录执行`npm run dev:mail`。

## 能力与权限

- 聚合收件箱、邮件分类、搜索、独立已读/收藏标记及HTML邮件沙箱展示。
- 店铺邮件风险看板、处理记录及资金到账核对；邮件信号不能代替卖家后台或银行核验。
- 163 IMAP增量同步、UID去重、串行拉取、抖动及失败退避。
- 飞书认证、加入申请、审批、账号绑定、成员停用与会话撤销。
- 运营、主管、总监、管理员四类角色；功能权限和整邮箱授权由服务端校验。
- `supervisor_access_only=1`是已有的环境级试用设置，限制访问/审批/提醒范围；公开源码不默认开启或关闭任何现有环境的该设置。
- 管理员全局管理范围与个人通知邮箱列表分别控制；成为管理员不自动订阅所有邮箱。
- 个人提醒按邮箱授权、成员状态和功能权限投递，包含去重、持久化队列与重试；撤权后不继续发送，历史邮件不补推。
- Agent API使用独立API Key，详见[OpenAPI](openapi.yaml)。不增加与编辑工作台共用会话的能力。

旧群通知通道保留兼容代码；配置专用飞书应用时沿用原有停用群通道的规则。已移除的站点分类授权不在本次恢复。创建邮箱、手动同步和发送提醒都属于实际业务操作，不是离线演示步骤。

## 新环境配置（不是现有环境迁移步骤）

```sh
cp .env.example .env
uv run --locked store-mail-web genkey
uv run --locked store-mail-web hash-password
# 将生成值仅写入私有.env；配置独立数据库路径和监听端口
uv run --locked store-mail-web doctor
uv run --locked store-mail-web serve --no-scheduler
```

`--no-scheduler`仅关闭IMAP定时拉取，**不会停用其他后台通知任务**。无外部副作用的预览应使用前面的离线演示。正式接入需要自己的邮箱授权码、管理员配置和飞书应用；不要把示例凭证或演示账号用于正式环境。

`STORE_MAIL_WEB_SECRET_KEY`用于加密邮箱授权码及会话签名。已有数据必须使用原主密钥；本地测试应使用新的临时数据库，不能连接部署数据库。SQLite、邮件、授权码、主密钥、日志和备份不进入Git或安装包。

飞书的App ID、App Secret、tenant及回调地址通过私有环境配置；不同应用身份不会自动映射。统一登录、店铺主数据整合和数据库迁移另行实施。

## 测试与打包

```sh
uv run --locked python -m unittest discover -s tests -q
uv build --out-dir dist
```

测试使用临时SQLite、合成身份/邮件、模拟IMAP和飞书，以及随机回环HTTP端口，不读取真实邮箱库。前端为原生ES模块，没有npm依赖或前端构建步骤；网页、图片资源和OpenAPI随wheel打包。

monorepo根目录提供`npm run test:mail`、`npm run build:mail`、`npm run package:mail -- REF`。后者仅归档指定Git提交的本模块，附SHA-256和来源commit，排除未追踪数据与私有配置。

容器和systemd模板见[部署说明](deploy/linux/README.md)。在本模块目录执行`docker compose config`检查自己的私有配置；容器发布需要显式启动，不随workbench主服务启动。

## 版本与来源

0.13.1是独立模块的公开整理版本：清理企业专属文档和称呼，使用合成测试身份，补齐许可、锁定依赖及打包。权限、通知、同步和存储算法保留原有逻辑；不执行上线或数据迁移。[来源与导入范围](UPSTREAM.md)

模块许可为[Apache-2.0](LICENSE)，依赖保留各自许可，品牌和第三方商标说明见[NOTICE](NOTICE)。
