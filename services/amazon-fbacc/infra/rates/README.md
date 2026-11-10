# 官方费率定时核验与审核发布

此组件可独立部署在 Linux 主机。它监测17站的官方来源，保留证据、生成差异报告及待审核草稿。**定时账号不能批准或发布费率**；计算器继续使用已发布版本。

**当前运行方式（2026-10-31 用户调整）：按需手动执行。** `fba-rate-monitor.timer` 已停止并禁用，没有下一次定时运行；需要核验时执行 `sudo systemctl start --no-block fba-rate-monitor.service`。恢复每日调度须由用户另行要求。

## 自动执行范围

1. 当前由维护人员按需触发。保留的定时器模板为北京时间08:30、随机延迟最多5分钟，但已禁用，不会每天运行或开机补跑。
2. 检查22个来源：欧洲10站共用官方费率PDF及英国定价页的新PDF链接；澳洲、加拿大、美国、墨西哥、阿联酋、沙特、日本的官方费率/尺寸/变更页面。
3. 卖家帮助页使用其公开网页调用的内容请求，无卖家账号、Cookie或API凭证。该请求不是承诺长期兼容的公开SP-API，正文、关键字和表格完整性检查用于发现改版。
4. 每个来源在独立子进程中读取，网页总时限55秒，PDF下载和提取合计120秒，最多并行2个；网络错误最多尝试2次。失败标为unavailable并保留上次成功时间。不会将登录页、空壳页面或下载失败判为“费率未变化”。
5. 保存压缩原文、SHA-256、提取文本/表格、含数字和规则的行、费率PDF链接及逐行差异。PDF链接替换也会触发变化。
6. 首次观察或内容变化时生成候选目录，复制当前费率为 `rates.draft.json`，建立隔离工作区并执行费率结构检查、60项现有测试和27个独立参考样例。没有变化时继续校验27个参考样例。

**候选草稿仍保留现用费率值。** 当前自动提取官方文本和表格并列出差异，尚未将任意新版页面规则全自动转成可信费率。审核人员需要核对原文，修改候选费率及相应测试/样例。不能把草稿生成或测试通过视为新费率已确认。

样例预期来自此前核验的官方表格/算例，不通过待测引擎生成。目前没有自动调用亚马逊收入计算器或卖家报价API完成在线对账；取得独立官方报价时，可作为 `official_calculator` 类型样例录入来源说明和预期值。

## 服务器路径

| 内容 | 路径 |
|---|---|
| 脚本 | `/opt/fba-rate-monitor/current/pipeline.py` |
| Python环境 | `/opt/fba-rate-monitor/venv` |
| 最近检查 | `/var/lib/fba-rate-monitor/latest.json` |
| 每次检查 | `/var/lib/fba-rate-monitor/runs/<run-id>/report.md` 及同目录JSON/diff |
| 原始资料 | `/var/lib/fba-rate-monitor/objects/<sha256>.gz`，相同原文按哈希去重 |
| 候选工作区 | `/var/lib/fba-rate-monitor/candidates/<id>/workspace/` |
| 样例 | 候选目录的 `samples.json` |
| 验证结果 | 候选目录的 `verification.json` |
| 审核记录 | `/etc/fba-rate-monitor/approvals/`，root专属写入/读取 |
| 调度 | `fba-rate-monitor.timer` / `fba-rate-monitor.service` |

状态区和原文不通过公网网页暴露。服务以独立用户 `fba-rate-monitor` 运行，只读访问计算器发布目录，内存上限256MiB、CPU30%。应用页面/JSON接口仍为0.3.0，监控组件独立编号0.2.0。

## 查看与手动检查

```bash
sudo systemctl list-timers fba-rate-monitor.timer --no-pager
sudo -u fba-rate-monitor /opt/fba-rate-monitor/venv/bin/python \
  /opt/fba-rate-monitor/current/pipeline.py status
sudo journalctl -u fba-rate-monitor.service -n 30 --no-pager
sudo systemctl start --no-block fba-rate-monitor.service
```

仅重试失败来源可执行 `pipeline.py check --retry-failed`（同上以定时账号运行）。其他来源保留原来的检查时间，标记not_rechecked，不伪造本次抓取成功。

`attention_required` 表示存在新内容、未完成审核或抓取失败；`no_observed_change` 仅表示本轮未观察到变化。`lastSuccessAt`是取得有效正文的时间，`lastReviewedAt`是管理员执行审核的时间，两者不混用。来源失败或候选验证不通过返回退出码2，报告保留具体原因。

## 审核、验证与发布

在候选工作区核对并修改 `rates/2026-10-31.2.json`、受影响的 `tests/*.test.mjs`，以及候选目录的 `samples.json`。此文件名是现行应用导入路径，实际费率版本取自JSON里的 `version`。

- 新费用必须更新快照版本、受影响站点的版本和核验日期，确认生效日期。
- 17站都保留参考样例；每个费用变化站点至少3个样例，包含边界样例。预期值从官方资料或独立官方报价取得，不能照抄程序输出。
- 应用计算代码、前端和部署代码必须与当前发布一致；需要改算法时走普通代码发布流程。
- 结构检查、全部60项以上测试、样例对账和相关官方来源完整性都通过，才可审核。

```bash
sudo -u fba-rate-monitor /opt/fba-rate-monitor/venv/bin/python \
  /opt/fba-rate-monitor/current/pipeline.py verify --candidate CANDIDATE_ID

sudo /opt/fba-rate-monitor/venv/bin/python \
  /opt/fba-rate-monitor/current/pipeline.py approve \
  --candidate CANDIDATE_ID --reviewer '审核人姓名' \
  --note '核对了哪些官方变更、受影响档位、生效日期及独立样例'

sudo /opt/fba-rate-monitor/venv/bin/python \
  /opt/fba-rate-monitor/current/pipeline.py publish --candidate CANDIDATE_ID
```

审核绑定整个工作区、样例、原文证据及验证报告的哈希。审核后文件被改动、当前正式版本变化、最近官方检查出现不同内容或失败、证据超过7天时，都要求重新检查/验证。无费率变化的候选可记录审核，但发布命令拒绝创建空变更版本。

发布命令先冻结管理员专属副本并比对审核哈希，再调用已验证的计算器安装脚本；保留版本目录、测试、就绪检查与失败回滚。发布后还应运行应用工程的 `scripts/check-deployed.mjs`，并使用更新后的独立样例检查公网结果。监控任务不会调用 `approve` 或 `publish`。

## 本次验收范围

本地和 Linux运行监控单元测试；隔离工作区跑通60项应用测试及27个参考样例。已验证页面缺正文、来源超时、金额/规则/PDF链接变化、重复观察仍保留待审核、权限隔离和验证后篡改拦截。


## 安装与停用

依赖锁定在 `requirements.txt`，安装器使用已下载并核验SHA-256的pypdf wheel，隔离到本组件venv。打包本目录时排除 `__pycache__`；安装命令：

```bash
sudo bash install.sh ARCHIVE RELEASE_ID SHA256 WHEEL_DIRECTORY
```

安装后按需手动运行并检查报告，保留定时器禁用状态。只有用户另行要求恢复每日调度时，才执行 `sudo systemctl enable --now fba-rate-monitor.timer`。停用定时调度执行 `sudo systemctl disable --now fba-rate-monitor.timer`；若需停止正在运行的检查，再停止同名service。计算器服务不受影响。

0.2.0监控组件增加AE/SA/JP公开定价、费用及尺寸页（JP另含2026变更页），允许域名和覆盖检查同步扩至17站；按需运行设置保持。
