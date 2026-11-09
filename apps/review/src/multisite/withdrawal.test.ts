import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ITable } from "@lark-opdev/block-bitable-api";
vi.mock("@lark-opdev/block-bitable-api", () => ({ IOpenSegmentType: { Text: 1 } }));
import { requestWithdrawal } from "./withdrawal";
const names = ["审核状态", "提交校验状态", "提交运营ID", "插件提交ID", "最近提交审核时间", "撤回请求ID", "撤回提交版本", "撤回处理ID", "撤回处理结果"];
const fields = new Map(names.map((n) => [n, n]));
let row: Record<string, unknown>;
let mode: "success" | "pending" | "denied" | "rejected" | "lost-response";
const getText = (v: unknown) => Array.isArray(v) ? v.map((x) => x.text).join("") : v;
const setRecord = vi.fn(async (_id: string, p: { fields: Record<string, unknown> }) => {
  if (Object.keys(p.fields).some((n) => !["撤回请求ID", "撤回提交版本"].includes(n)) || mode === "denied") throw new Error("record permission denied error");
  Object.assign(row, p.fields);
  if (mode === "success" || mode === "lost-response") Object.assign(row, { 审核状态: "草稿", 提交校验状态: "未提交", 插件提交ID: null, 撤回处理ID: getText(p.fields.撤回请求ID), 撤回处理结果: "已撤回" });
  if (mode === "rejected") Object.assign(row, { 撤回处理ID: getText(p.fields.撤回请求ID), 撤回处理结果: "本次提交版本已变化" });
  if (mode === "lost-response") throw new Error("network response lost");
});
const table = { getRecordById: vi.fn(async () => ({ fields: { ...row } })), setRecord } as unknown as ITable;
const run = () => requestWithdrawal(table, fields, "record-1", { userId: "operator", submittedAt: 1000 });
beforeEach(() => { vi.clearAllMocks(); mode = "success"; row = { 审核状态: "待审核", 提交校验状态: "通过", 提交运营ID: "operator", 插件提交ID: "record-1-v1", 最近提交审核时间: 1000 }; });
afterEach(() => vi.useRealTimers());
describe("普通运营受控撤回", () => {
  it("仅写请求字段，审核状态只读仍可等待系统退回草稿", async () => {
    await run(); expect(Object.keys(setRecord.mock.calls[0][1].fields).sort()).toEqual(["撤回提交版本", "撤回请求ID"]);
    expect(getText(setRecord.mock.calls[0][1].fields.撤回提交版本)).toBe("record-1-v1"); expect(row.审核状态).toBe("草稿");
  });
  it.each([["他人记录", { 提交运营ID: "other" }], ["已审核", { 审核状态: "审核通过" }], ["未校验", { 提交校验状态: "校验中" }], ["过期卡片", { 最近提交审核时间: 2000 }], ["缺版本", { 插件提交ID: "" }]])("%s不发送请求", async (_label, patch) => {
    Object.assign(row, patch); await expect(run()).rejects.toThrow(); expect(setRecord).not.toHaveBeenCalled();
  });
  it("权限失败明确提示", async () => { mode = "denied"; await expect(run()).rejects.toThrow("撤回请求未保存"); expect(row.审核状态).toBe("待审核"); });
  it("服务端版本拒绝呈现", async () => { mode = "rejected"; await expect(run()).rejects.toThrow("本次提交版本已变化"); });
  it("写响应丢失但已确认时保留成功且不重发", async () => { mode = "lost-response"; await run(); expect(setRecord).toHaveBeenCalledTimes(1); });
  it("超时或旧回执不冒充成功", async () => {
    vi.useFakeTimers(); mode = "pending"; row.撤回处理ID = "old-request"; row.撤回处理结果 = "已撤回";
    const pending = expect(run()).rejects.toThrow("系统尚未确认"); await vi.runAllTimersAsync(); await pending; expect(setRecord).toHaveBeenCalledTimes(1);
  });
});
