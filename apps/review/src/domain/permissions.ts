import { OPERATING_GROUPS, type OperatingGroup, type ReviewPath, type UserAccess } from "./types";

/** 仅本组主管本人提交走免评审；管理员、总监及运营沿用常规审核。 */
export function reviewPathForSubmission(
  access: UserAccess,
  group: OperatingGroup,
): ReviewPath {
  return access.role === "运营主管" && access.reviewGroups.includes(group)
    ? "主管自提免评审"
    : "";
}

export function canReviewOperatingGroup(
  access: UserAccess,
  group: OperatingGroup | "",
): boolean {
  if (!access.canReview) return false;
  if (access.canReviewAll) return true;
  return Boolean(group) && access.reviewGroups.includes(group as OperatingGroup);
}

export function accessScopeLabel(access: UserAccess): string {
  if (access.canReviewAll) return "全部小组";
  if (access.reviewGroups.length) return access.reviewGroups.join("、");
  return access.submitGroup || "未分组";
}

/** 旧全局组名 → 新（部,组）复合组名；过渡期兼容 */
const LEGACY_GROUP_MAP: Record<string, OperatingGroup> = {
  运营一组: "一部一组",
  运营二组: "一部二组",
  运营三组: "二部一组",
  运营四组: "二部二组",
};

export function normalizeGroup(value: string): OperatingGroup | "" {
  if ((OPERATING_GROUPS as readonly string[]).includes(value)) {
    return value as OperatingGroup;
  }
  return LEGACY_GROUP_MAP[value] ?? "";
}

export type ParsedRole =
  | { kind: "admin" }
  | { kind: "operator"; group: OperatingGroup }
  | { kind: "supervisor"; group: OperatingGroup }
  | { kind: "director"; groups: OperatingGroup[] };

/** 解析复合角色文本：管理员 / X部X组运营|主管 / X部总监（部级，管本部全部组）；兼容组级总监与旧格式 */
export function parseRoleText(
  text: string,
  legacyGroupText = "",
): ParsedRole | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (trimmed === "管理员") return { kind: "admin" };
  const departmentDirector = trimmed.match(/^([一二])部总监$/);
  if (departmentDirector) {
    const prefix = trimmed.slice(0, 2);
    const groups = OPERATING_GROUPS.filter((group) =>
      group.startsWith(prefix),
    );
    return { kind: "director", groups };
  }
  const composite = trimmed.match(/^[一二]部[一二]组(运营|主管|总监)$/);
  if (composite) {
    const group = trimmed.slice(0, 4) as OperatingGroup;
    if (composite[1] === "运营") return { kind: "operator", group };
    if (composite[1] === "主管") return { kind: "supervisor", group };
    return { kind: "director", groups: [group] };
  }
  const legacyGroup = normalizeGroup(legacyGroupText);
  if (trimmed === "运营主管" && legacyGroup) {
    return { kind: "supervisor", group: legacyGroup };
  }
  if (trimmed === "运营" && legacyGroup) {
    return { kind: "operator", group: legacyGroup };
  }
  return null;
}

export interface MergedAccess {
  role: "运营" | "运营主管" | "运营总监" | "管理员" | "未配置";
  submitGroup: OperatingGroup | "";
  reviewGroups: OperatingGroup[];
  canSubmit: boolean;
  canReview: boolean;
  canReviewAll: boolean;
}

/** 多行合并：管理员 > 总监 > 主管 > 运营；提交组取首个非管理员行的组；审批组=主管/总监行并集 */
export function mergeRoleRows(
  rows: { text: string; legacyGroup?: string }[],
): MergedAccess {
  const parsed = rows
    .map((row) => parseRoleText(row.text, row.legacyGroup ?? ""))
    .filter((item): item is ParsedRole => item !== null);
  const isAdmin = parsed.some((item) => item.kind === "admin");
  let hasOperator = false;
  let hasSupervisor = false;
  let hasDirector = false;
  let submitGroup: OperatingGroup | "" = "";
  const reviewGroups = new Set<OperatingGroup>();
  for (const item of parsed) {
    if (item.kind === "admin") continue;
    if (!submitGroup) {
      submitGroup = item.kind === "director" ? item.groups[0] : item.group;
    }
    if (item.kind === "operator") hasOperator = true;
    if (item.kind === "supervisor") {
      hasSupervisor = true;
      reviewGroups.add(item.group);
    }
    if (item.kind === "director") {
      hasDirector = true;
      for (const group of item.groups) reviewGroups.add(group);
    }
  }
  const role = isAdmin
    ? "管理员"
    : hasDirector
      ? "运营总监"
      : hasSupervisor
        ? "运营主管"
        : hasOperator
          ? "运营"
          : "未配置";
  return {
    role,
    submitGroup,
    reviewGroups: [...reviewGroups],
    canSubmit: Boolean(submitGroup),
    canReview: isAdmin || reviewGroups.size > 0,
    canReviewAll: isAdmin,
  };
}
