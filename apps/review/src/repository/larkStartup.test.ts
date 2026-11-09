import { describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({ calls: [] as string[] }));
vi.mock("@lark-opdev/block-bitable-api", () => ({
  FieldType: { Text: 1, AutoNumber: 2, User: 3, SingleSelect: 4, Checkbox: 5, DateTime: 6 },
  IOpenSegmentType: { Text: 1 }, OperationType: { Editable: 1 }, PermissionEntity: { Table: 1 },
  ToastType: {},
  bitable: { bridge: { getUserId: async () => "supervisor" }, base: {
    getTableById: async () => { throw new Error("old id"); },
    getPermission: async () => true,
    getTableByName: async (name: string) => {
      sdk.calls.push(name);
      if (name === "SKU选品库") return { id: "main", getFieldMetaList: async () => [
        { name: "记录序号", id: "sequence", type: 2 }, { name: "币种", id: "currency", type: 1 }] };
      if (name === "插件人员权限配置") return { getFieldMetaList: async () => [
        { name: "成员", id: "member", type: 3 }, { name: "角色", id: "role", type: 4 },
        { name: "启用状态", id: "enabled", type: 5 }, { name: "生效日期", id: "date", type: 6 }],
        getRecords: async () => ({ hasMore: false, records: [{ recordId: "access", fields: {
          member: [{ id: "supervisor" }], role: { text: "一部一组主管" }, enabled: true, date: 0,
        } }] }) };
      throw new Error(`retired or unavailable table: ${name}`);
    },
  } },
}));

import { createLarkSelectionRepository } from "./lark";
describe("统一评审台启动", () => {
  it("删除旧测算参数表后仍能加载权限，不再校验旧加拿大财务参数", async () => {
    const snapshot = await createLarkSelectionRepository().loadSnapshot();
    expect(snapshot.integration.ready).toBe(true);
    expect(snapshot.access).toMatchObject({ canSubmit: true, canReview: true, submitGroup: "一部一组" });
    expect(new Set(sdk.calls)).toEqual(new Set(["SKU选品库", "插件人员权限配置"]));
  });
});
