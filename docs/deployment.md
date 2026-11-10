# 部署说明

`deploy/compose.yaml`是新安装模板，绑定本机回环端口。生产需自行配置HTTPS反向代理、数据库备份、磁盘监控、任务worker资源限制和外网访问策略。

## 私有配置

- `.env`：数据库连接、应用凭证、站点地址及服务开关。
- `targets.local.json`：`integrations/feishu/targets.example.json`的私有副本，通过`AIGC_TARGETS_FILE`加载；包含integration与production各自批准的Base/table/app/plugin/人员表标识。启动即固定，不接受用户请求修改。
- 插件构建配置：`editor.local.json`、`manifest.local.json`。浏览器只获得公开的应用/表格标识与API地址，不能获得App Secret或模型密钥。
- PostgreSQL与`/data`需一起备份。`/data/private/provider-master.key`用于解密数据库中存储的供应商凭证，不能丢失或提交到Git。

需要挂载`targets.local.json`时，在私有compose override里把文件挂载到`/run/config/targets.local.json:ro`，为所有相关容器设置相同的`AIGC_TARGETS_FILE`。原生插件配置与服务端配置必须对应同一环境。

## 服务进程

- `api`：HTTP和前端，启动时执行已有SQL迁移。
- `model` / `collector`：异步生成及素材采集。通过设置模型/来源并发控制容量，不通过刷新页面重复提交任务。
- `sync` / `roles` / `images`：飞书投影、人员同步和商品图片缓存。
- `notify`：事务outbox通知。请在接入验收后显式启用。

示例compose不会自动运行这些worker，也不包含任何真实模型密钥。默认模型总日次数为0（不限次数），并发仍有限；使用方应自行设置供应商账单和容量。

## 模型与采集

OpenAI可直接接入受支持的API端点，或部署`amazon_aigc.openai_relay`受控中转；中转使用独立鉴权令牌、模型白名单和并发控制，不能做公开代理。DeepSeek配置在服务端管理页，具体模型名以使用方账户可用模型为准。

采集实现包括TMAPI、Firecrawl和原生网页/渲染采集。配置服务商后使用自己的合法素材验证；读取失败支持手动补充。图片视觉输入仅作生成补充参考，失败不应阻塞纯文字生成。

## 数据迁移

SQL迁移位于`services/amazon-aigc-workbench-service/sql`，使用校验和防止已执行文件被修改。历史接管脚本默认应先预览并核对输入、身份、状态及图片归属后再提交，不要在真实记录上运行测试。生产备份不放进本开源仓库。

## 独立 FBACC 服务

FBACC 不需要业务数据库或模型凭证。`npm run dev:fbacc`可独立启动；容器使用单独的compose文件，无需配置工作台`.env`或启动其他服务：

```sh
docker compose -f deploy/fbacc.compose.yaml up -d --build
curl http://127.0.0.1:4318/readyz
docker compose -f deploy/fbacc.compose.yaml down
```

容器仅绑定本机4318端口。独立服务器安装包用`npm run package:fbacc -- fbacc/v0.3.0`导出；安装步骤见[模块Linux部署说明](../services/amazon-fbacc/deploy/linux/README.md)。不要把整个monorepo归档传给模块安装器。生产反向代理、访问控制和服务地址由部署环境配置；不会将企业端点写入公开代码。详细接入和版本边界见[FBACC说明](fbacc.md)。
