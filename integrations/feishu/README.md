# 飞书接入

本目录只提供接入模板，不含任何企业应用凭证、真实Base、人员名单、角色权限导出或生产工作流。

## 评审台

`apps/review`可以通过`?mock=1`查看本地模拟。真实接入使用当前多维表格SDK身份，按表名读取SKU选品库、插件人员权限配置、站点税务与财务参数、FBA费率及尺寸规则。

字段契约以以下源码为准：

- `apps/review/src/repository/lark.ts`：旧版选品字段和权限解析。
- `apps/review/src/multisite/repository.ts`：多站点字段、快照及提交/审核写入。
- `apps/review/src/domain/types.ts`、`multisite/domain.ts`：数据与站点类型。

可用`VITE_REVIEW_TABLE_ID`明确旧版主表；未设置时按表名定位。原生审核状态由飞书自动化推进，需在自己的Base建立对应提交校验、审核、打回与撤回流程。当前开源版没有“一键复制企业工作流”功能；字段不匹配时必须停止接入，不能忽略检查。

评审打包：先构建，然后将`manifest.example.json`复制到私有配置，设置自己的appId/blockTypeId，再运行：

```sh
FEISHU_MANIFEST_CONFIG=/absolute/private/review-manifest.local.json npm --prefix apps/review run build:feishu
```

## 编辑台与后台

1. 在独立测试Base准备与`sync.py` / `roster_sync.py`相符的SKU字段、人员权限表及身份证明表。
2. 身份证明表包含校验摘要，以及不可由普通用户伪造的创建人、创建时间、更新人、更新时间系统字段。为服务端配置`AIGC_BASE_AUTH_FIELDS`（JSON映射）、`AIGC_BASE_AUTH_TABLE`和`AIGC_BASE_PARENT_ORIGIN`。
3. 按真实身份映射初始化人员或运行roles同步。管理员不会自动获得美工资格，离职/停用人员应撤销会话。
4. 服务端配置批准的Base和应用，开启`AIGC_BASE_LOGIN_ENABLED=true`；使用App OpenAPI凭证验证浏览器SDK写入的摘要记录。不要把人员角色或App Secret放入前端。
5. 将`editor.example.json`、`manifest.example.json`复制成私有配置，分别设置API地址、Base、证明字段和应用插件标识。

```sh
WORKBENCH_PLUGIN_CONFIG=/absolute/private/editor.local.json \
FEISHU_MANIFEST_CONFIG=/absolute/private/editor-manifest.local.json \
npm run build:feishu:editor
```

构建结果在`apps/editor/dist`。通过自己账户配置的`opdev`上传，再在飞书开发者后台发布。测试与生产使用不同配置，不能把模板中的合成标识当作真实配置。

## 权限与同步

服务端`valid_target`对Base/table做固定白名单校验，生产另外核对应用、插件origin和人员表；配置错误失败关闭。通知只发送到具备任务读取范围、仍在职的映射人员。身份验证表与人员权限配置表用途不同，不能因为员工记录数量不同直接删除身份表。

SKU业务字段与工作台之间使用版本化投影。上线前验证主表、角色、审核自动化、图片下载与通知；只看到管理员页面正常不能代替运营和美工真人验收。
