import { IOpenSegmentType, type ITable, type IOpenCellValue } from "@lark-opdev/block-bitable-api";

function valueText(value: IOpenCellValue): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((part) =>
    part && typeof part === "object" && "text" in part ? String(part.text) : "").join("");
  if (value && typeof value === "object" && "text" in value) return String(value.text);
  return "";
}

/** Only request fields are written by the user's SDK; the Base workflow owns the transition. */
export async function requestWithdrawal(
  table: ITable, fields: Map<string, string>, recordId: string,
  expected: { userId: string; submittedAt?: number },
): Promise<void> {
  const field = (name: string) => {
    const id = fields.get(name);
    if (!id) throw new Error("撤回配置尚未就绪，请刷新登记表；仍异常请联系管理员");
    return id;
  };
  const names = ["审核状态", "提交校验状态", "提交运营ID", "插件提交ID", "最近提交审核时间",
    "撤回请求ID", "撤回提交版本", "撤回处理ID", "撤回处理结果"];
  names.forEach(field);
  const latest = await table.getRecordById(recordId);
  const text = (name: string) => valueText(latest.fields[field(name)]);
  if (text("提交运营ID") !== expected.userId || text("审核状态") !== "待审核" ||
      text("提交校验状态") !== "通过") throw new Error("仅可撤回本人待审核且校验通过的记录，请刷新后核对");
  const rawTime = latest.fields[field("最近提交审核时间")];
  const submittedAt = typeof rawTime === "number" ? rawTime : Date.parse(valueText(rawTime));
  if (expected.submittedAt !== undefined && submittedAt !== expected.submittedAt)
    throw new Error("该记录的提交版本已变化，请刷新后重试");
  const version = text("插件提交ID");
  if (!version) throw new Error("本次提交版本缺失，请联系管理员核对后撤回");
  const requestId = crypto.randomUUID();
  const rich = (text: string): IOpenCellValue => [{ type: IOpenSegmentType.Text, text }];
  try {
    await table.setRecord(recordId, { fields: {
      [field("撤回提交版本")]: rich(version), [field("撤回请求ID")]: rich(requestId),
    } });
  } catch (error) {
    // The request may have reached Base even if the client lost the response.
    // Readback below resolves the result without rewriting or duplicating the request.
    if (!(error instanceof Error)) throw error;
  }
  for (let attempt = 0; attempt < 30; attempt++) {
    const row = await table.getRecordById(recordId);
    const get = (name: string) => valueText(row.fields[field(name)]);
    if (get("撤回处理ID") === requestId) {
      const result = get("撤回处理结果");
      if (result !== "已撤回") throw new Error(result || "撤回未通过，请刷新后核对");
      if (get("审核状态") === "草稿" && get("提交校验状态") === "未提交" &&
          !get("插件提交ID")) return;
    }
    if (get("撤回请求ID") !== requestId)
      throw new Error("撤回请求未保存或已被新请求替换，请刷新后核对权限与记录状态");
    if (["审核通过", "拒绝", "打回修改"].includes(get("审核状态")))
      throw new Error("主管已处理该记录，本次撤回不再适用，请刷新后查看");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("撤回请求已提交，系统尚未确认。请稍后刷新，确认进入草稿后再编辑，勿重复提交");
}
