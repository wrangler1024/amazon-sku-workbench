import { describe, expect, it } from "vitest";
import { firstAttachmentToken } from "./attachment";

describe("产品图片附件回读", () => {
  it("识别插件 SDK 和飞书记录接口返回的附件标识", () => {
    expect(firstAttachmentToken([{ token: "sdk-image" }])).toBe("sdk-image");
    expect(firstAttachmentToken([{ file_token: "base-image" }])).toBe("base-image");
    expect(firstAttachmentToken([])).toBe("");
  });
});
