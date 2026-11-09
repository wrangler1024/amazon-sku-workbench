import { describe, expect, it } from "vitest";

import {
  accessScopeLabel,
  canReviewOperatingGroup,
  mergeRoleRows,
  normalizeGroup,
  parseRoleText,
  reviewPathForSubmission,
} from "./permissions";
import type { UserAccess } from "./types";

function access(overrides: Partial<UserAccess>): UserAccess {
  return {
    userId: "user-1",
    role: "运营",
    submitGroup: "一部一组",
    reviewGroups: [],
    canReviewAll: false,
    canSubmit: true,
    canReview: false,
    tableEditable: true,
    ...overrides,
  };
}

describe("分组审批权限", () => {
  it("仅主管本人提交所管小组走免评审路径", () => {
    const supervisor = access({ role: "运营主管", reviewGroups: ["一部一组"], canReview: true });
    expect(reviewPathForSubmission(supervisor, "一部一组")).toBe("主管自提免评审");
    expect(reviewPathForSubmission(supervisor, "一部二组")).toBe("");
    expect(reviewPathForSubmission(access({}), "一部一组")).toBe("");
    expect(reviewPathForSubmission(access({ role: "运营总监", reviewGroups: ["一部一组"], canReview: true }), "一部一组")).toBe("");
    expect(reviewPathForSubmission(access({ role: "管理员", reviewGroups: ["一部一组"], canReview: true }), "一部一组")).toBe("");
  });
  it("运营主管只能审批配置给自己的小组", () => {
    const supervisor = access({
      role: "运营主管",
      reviewGroups: ["一部二组"],
      canReview: true,
    });

    expect(canReviewOperatingGroup(supervisor, "一部二组")).toBe(true);
    expect(canReviewOperatingGroup(supervisor, "二部一组")).toBe(false);
    expect(canReviewOperatingGroup(supervisor, "")).toBe(false);
    expect(accessScopeLabel(supervisor)).toBe("一部二组");
  });

  it("管理员拥有显式跨组审批权限", () => {
    const administrator = access({
      role: "管理员",
      submitGroup: "",
      canSubmit: false,
      canReview: true,
      canReviewAll: true,
    });

    expect(canReviewOperatingGroup(administrator, "一部一组")).toBe(true);
    expect(canReviewOperatingGroup(administrator, "二部二组")).toBe(true);
    expect(canReviewOperatingGroup(administrator, "")).toBe(true);
    expect(accessScopeLabel(administrator)).toBe("全部小组");
  });

  it("普通运营不能审批任何小组", () => {
    const operator = access({});
    expect(canReviewOperatingGroup(operator, "一部一组")).toBe(false);
  });
});

describe("复合角色解析与多行合并", () => {
  it("解析部组职能三级复合角色", () => {
    expect(parseRoleText("管理员")).toEqual({ kind: "admin" });
    expect(parseRoleText("一部一组运营")).toEqual({ kind: "operator", group: "一部一组" });
    expect(parseRoleText("一部二组主管")).toEqual({ kind: "supervisor", group: "一部二组" });
    expect(parseRoleText("二部一组总监")).toEqual({ kind: "director", groups: ["二部一组"] });
    expect(parseRoleText("运营一组")).toBeNull();
    expect(parseRoleText("")).toBeNull();
  });

  it("旧格式（角色+所属小组）回退解析并映射新组名", () => {
    expect(parseRoleText("运营主管", "运营一组")).toEqual({ kind: "supervisor", group: "一部一组" });
    expect(parseRoleText("运营", "运营三组")).toEqual({ kind: "operator", group: "二部一组" });
    expect(parseRoleText("运营主管", "")).toBeNull();
    expect(normalizeGroup("运营四组")).toBe("二部二组");
    expect(normalizeGroup("一部一组")).toBe("一部一组");
    expect(normalizeGroup("未知")).toBe("");
  });

  it("多行合并：管理员叠加运营行获得提交能力", () => {
    const merged = mergeRoleRows([{ text: "管理员" }, { text: "一部一组运营" }]);
    expect(merged.role).toBe("管理员");
    expect(merged.submitGroup).toBe("一部一组");
    expect(merged.canReviewAll).toBe(true);
    expect(merged.reviewGroups).toEqual([]);
  });

  it("部级总监管辖本部全部组", () => {
    expect(parseRoleText("一部总监")).toEqual({
      kind: "director",
      groups: ["一部一组", "一部二组"],
    });
    expect(parseRoleText("二部总监")).toEqual({
      kind: "director",
      groups: ["二部一组", "二部二组"],
    });
    const merged = mergeRoleRows([{ text: "一部总监" }]);
    expect(merged.role).toBe("运营总监");
    expect(merged.reviewGroups).toEqual(["一部一组", "一部二组"]);
    expect(merged.submitGroup).toBe("一部一组");
  });

  it("多行合并：主管+运营取并集组，总监层级高于主管", () => {
    const merged = mergeRoleRows([
      { text: "一部一组主管" },
      { text: "一部二组运营" },
    ]);
    expect(merged.role).toBe("运营主管");
    expect(merged.submitGroup).toBe("一部一组");
    expect(merged.reviewGroups).toEqual(["一部一组"]);

    const director = mergeRoleRows([{ text: "二部二组总监" }]);
    expect(director.role).toBe("运营总监");
    expect(director.reviewGroups).toEqual(["二部二组"]);
    expect(director.canSubmit).toBe(true);
  });

  it("空行与无法识别的文本归为未配置", () => {
    expect(mergeRoleRows([]).role).toBe("未配置");
    expect(mergeRoleRows([{ text: "运营", legacyGroup: "" }]).role).toBe("未配置");
  });
});
