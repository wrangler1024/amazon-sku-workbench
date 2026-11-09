import { expect, it, vi } from "vitest";
import { IOpenSegmentType } from "@lark-opdev/block-bitable-api";
import { fullText } from "./repository";

vi.mock("@lark-opdev/block-bitable-api", () => ({ bitable: {}, IOpenSegmentType: { Text: 1 } }));

it("主管意见跨多个富文本片段时保留完整内容与换行", () => {
  expect(fullText([
    { type: IOpenSegmentType.Text, text: "请补充专利依据。\n" },
    { type: IOpenSegmentType.Text, text: "试包后重新核对配送费。" },
  ])).toBe("请补充专利依据。\n试包后重新核对配送费。");
});
