# amazon-aigc-workbench-service

FastAPI / PostgreSQL后台，提供商品资料、版本化文案与图需、制图任务、图片交付验收、独立刊登记录、AI/采集队列、Feishu身份与同步适配。

从仓库根目录阅读[开发说明](../../README.md)、[部署说明](../../docs/deployment.md)和[架构迁移](../../docs/architecture.md)。

```sh
uv sync --locked
AIGC_TEST_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:PORT/TEST_DB uv run pytest -q
```

所有测试只使用合成数据和随机独立schema。不要使用生产数据库连接。
