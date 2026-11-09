import { describe, expect, it } from "vitest";
import { hasMasterLink, masterLink } from "./link";

describe("实体 SKU 双向关联", () => {
  it("按插件 SDK 的关联字段格式写入，并识别 Base 回读", () => {
    const link = masterLink({ id: "rec-master", tableId: "tbl-master" }, "DEMO-ITEM-004");
    expect(link.recordIds).toEqual(["rec-master"]);
    expect(link.tableId).toBe("tbl-master");
    expect(hasMasterLink(link, "rec-master")).toBe(true);
    expect(hasMasterLink([{ id: "rec-master" }], "rec-master")).toBe(true);
    expect(hasMasterLink(null, "rec-master")).toBe(false);
  });
});
