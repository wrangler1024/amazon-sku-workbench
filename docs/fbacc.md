# FBACC 独立模块

`services/amazon-fbacc`将计算器、HTTP API、网页和费率核验工具放在同一模块内，独立于编辑后台的Python进程、数据库、飞书身份和商品数据。当前应用版本0.3.0、核验工具0.2.0、费率快照2026-10-31.2；来源为原公开仓库的确定提交，详见[来源记录](../services/amazon-fbacc/UPSTREAM.md)。

## 本地使用

```sh
npm run dev:fbacc
# http://127.0.0.1:4318/ 计算器网页
# http://127.0.0.1:4318/readyz 就绪状态
# http://127.0.0.1:4318/openapi.json 接口定义
```

也可进入模块目录运行`npm start`，或使用[独立Docker配置](deployment.md#独立-fbacc-服务)。Node 22.18+即可从仓库运行，计算器无第三方运行时npm依赖。纯计算函数可从模块`src/index.mjs`导入；类型声明是`src/index.d.ts`。

## 与评审台的关系

- 开发时启动FBACC及`npm run dev:review`。评审台`/__ae-fba`通过Vite服务端代理到`http://127.0.0.1:4318`；可在`apps/review/.env.local`设置`FBACC_SERVICE_URL`覆盖。
- 发布构建用`VITE_FBACC_SERVICE_URL`指定浏览器可达的服务地址，或同源反向代理`/v1/fba`。跨域部署需由网关精确配置CORS及飞书服务域名授权。这里不包含企业的域名或网关配置。
- `FBA_CALCULATOR_API_KEY`只允许放服务器私有环境；不要放入`VITE_*`变量。启用鉴权时，可信网关/服务端代理应负责附加令牌，浏览器不能携带长期密钥。
- 当前评审适配器仅用于AE参考预估。FBACC支持17站，并不代表其他站点的评审计算逻辑已切换。费用冲突返回`quote_required`，不填0，不覆盖运营手填FBA费用或历史利润。
- 未来独立SKU系统可通过HTTP复用该模块，或由Node服务调用纯函数；无须依赖飞书记录ID。

## 验证

```sh
npm run check:fbacc
npm run test:fbacc
npm run test:fbacc:integration
cd services/amazon-fbacc
python3 -m unittest discover -s infra/rates/tests -v
node infra/rates/verify-samples.mjs . infra/rates/samples.json
```

核验工具测试需要Python3.12+。参考样例来自已审核快照，不代表实时官方报价。HTTP联调使用随机本地端口和合成数据，运行真实评审适配器、Vite代理与计算器，验证金额、费用冲突和人工费用不被改写。以上测试不会抓取官方来源、启动定时器、写飞书或访问生产服务。

## 发布与维护

后续修改统一在本仓库进行，FBACC可单独升级为`fbacc/vX.Y.Z`，不用同时升级编辑后台。`versions.json`记录应用、核验工具和费率版本。原`amazon-fbacc`仓库保留历史，不做双向自动同步。

```sh
# 必须先提交需要发布的代码，再打包指定提交或标签
npm run package:fbacc -- fbacc/v0.3.0
```

输出在`dist/packages`，包含模块根目录结构的`.tar.gz`、SHA-256及来源commit清单。只导出指定Git提交，不夹带工作区私有配置，也不包含未提交修改。可在全新目录解压后独立测试、启动；不要将monorepo整体归档交给模块安装器。

模块保留MIT许可证，根项目采用Apache-2.0。费率和官方来源仍需按生效日期人工审核；合并代码不代表发布新费率、部署服务或启用监测调度。
