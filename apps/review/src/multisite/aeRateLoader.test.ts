import { describe, expect, it, vi } from "vitest";

vi.mock("@lark-opdev/block-bitable-api", () => ({
  IOpenSegmentType: { Text: 1 },
  bitable: { base: { getTableByName: async (name: string) => {
    if (["阿联酋FBA费率表", "沙特FBA费率表", "日本FBA费率表"].includes(name)) throw new Error("not migrated");
    const fields = name === "加拿大FBA费率表"
      ? ["FBA附加费率", "低价门槛(本币)", "低价折扣(本币)", "旺季配送费(CAD)"]
      : ["档位键", "配送费(本币)"];
    return {
      getFieldMetaList: async () => fields.map((field) => ({ name: field, id: field })),
      getRecords: async () => ({ hasMore: false, records: name === "英国FBA费率表"
        ? [{ recordId: "uk-card", fields: { "档位键": [{ type: 1, text: "UK-1" }], "配送费(本币)": 5 } }]
        : [] }),
    };
  } } },
}));
import { loadFbaRateCards } from "./repository";

describe("新站参考表扩展过渡", () => {
  it("新增参考表尚未迁移时仍返回原站点费率", async () => {
    expect(await loadFbaRateCards()).toEqual([expect.objectContaining({ site: "UK", key: "UK-1", baseFee: 5 })]);
  });
});
