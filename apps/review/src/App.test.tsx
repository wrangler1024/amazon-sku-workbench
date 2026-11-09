import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { calculateSelection } from "./domain/calculator";
import { SAMPLE_INPUT, SAMPLE_RATES, SAMPLE_TIERS } from "./domain/sample";
import type { CandidateRecord, SelectionRepository, SelectionSnapshot } from "./domain/types";

vi.mock("./multisite/sitePreference", () => ({
  loadSitePreference: async (userId: string) => ({ scope: { baseId: "test", userId }, storage: "cloud",
    preference: { version: 1, baseId: "test", userId, site: "CA", savedAt: 1 } }),
  saveSitePreference: async () => "cloud",
}));

const calculation = calculateSelection(SAMPLE_INPUT, SAMPLE_RATES, SAMPLE_TIERS);
const candidate: CandidateRecord = {
  recordId: "rec-review",
  skuNumber: "JP-TEST-001",
  submissionId: "submission-test",
  submittedAt: Date.now(),
  reviewCount: 1,
  status: "待审核",
  validationStatus: "通过",
  reviewPath: "",
  submitterId: "user-2",
  submitterName: "测试运营",
  productImageToken: null,
  operatingGroup: "一部一组",
  hasImage: true,
  input: SAMPLE_INPUT,
  rates: SAMPLE_RATES,
  calculation,
  managerComment: "",
};

function makeSnapshot(
  role: "运营" | "运营主管" | "管理员" | "未配置" = "运营主管",
): SelectionSnapshot {
  return {
    access: {
      userId: "user-1",
      role,
      submitGroup: role === "管理员" || role === "未配置" ? "" : "一部一组",
      reviewGroups: role === "运营主管" ? ["一部一组"] : [],
      canReviewAll: role === "管理员",
      tableEditable: true,
      canSubmit: role === "运营" || role === "运营主管",
      canReview: role === "运营主管" || role === "管理员",
    },
    integration: {
      ready: true,
      source: "mock",
      missingTables: [],
      missingFields: [],
      message: "测试连接",
    },
    rates: SAMPLE_RATES,
    fbaTiers: SAMPLE_TIERS,
    returnedRecords: [{ ...candidate, recordId: "rec-return", submitterId: "user-1", status: "打回修改", managerComment: "补充专利依据" }],
    mySubmissions: [{ ...candidate, recordId: "rec-return", submitterId: "user-1", status: "打回修改", managerComment: "补充专利依据" }],
    pendingReviews: [],
    excludedPendingCount: 0,
  };
}

function makeRepository(snapshot = makeSnapshot()): SelectionRepository {
  return {
    loadSnapshot: vi.fn(async () => snapshot),
    submitCandidate: vi.fn(async () => ({ ok: true, message: "已提交", recordId: "rec-new" })),
    resubmitCandidate: vi.fn(async () => ({ ok: true, message: "已重提", recordId: "rec-return" })),
    withdrawCandidate: vi.fn(async () => ({ ok: true, message: "已撤回", recordId: "rec-review" })),
    reviewCandidate: vi.fn(async () => ({ ok: true, message: "已审核" })),
    openRecord: vi.fn(async () => undefined),
    getProductImageUrl: vi.fn(async () => null),
    deleteReturnedRecord: vi.fn(async () => ({ ok: true, message: "已删除" })),
  };
}

const themeStorage = new Map<string, string>();
beforeEach(() => {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => themeStorage.get(key) ?? null,
      setItem: (key: string, value: string) => { themeStorage.set(key, value); },
      removeItem: (key: string) => { themeStorage.delete(key); },
    },
  });
});
afterEach(() => { cleanup(); themeStorage.clear(); });

describe("亚马逊选品评审台", () => {
  it("只展示统一的选品录入与评审页签，保留粗算和正式提审门槛", async () => {
    render(<App repository={makeRepository()} />);
    const navigation = await screen.findByRole("navigation", { name: "工作区" });
    expect(within(navigation).getByRole("button", { name: "选品录入与评审" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).queryByRole("button", { name: "运营初选" })).not.toBeInTheDocument();
    expect(within(navigation).queryByRole("button", { name: "多站点评审" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "快速机会粗算" })).toBeInTheDocument();
    expect(screen.getByLabelText("售价（CAD）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /收起粗算/ }));
    expect(screen.queryByLabelText("售价（CAD）")).not.toBeInTheDocument();
    expect(screen.getByLabelText("目标站点")).toBeInTheDocument();
    expect(screen.getByText(/仅供初选 · 不保存/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "提交选品（免审核）" }));
    expect(screen.getByRole("dialog", { name: "提交审核前请补齐" })).toBeInTheDocument();
    expect(screen.getByLabelText("选品SKU")).toHaveAttribute("aria-invalid", "false");
  });

  it("多站点粗算带入售价、采购、包装和手填 FBA", async () => {
    render(<App repository={makeRepository()} />);
    await screen.findByLabelText("售价（CAD）");
    const site = screen.getByLabelText("目标站点");
    fireEvent.change(site, { target: { value: "UK" } });
    fireEvent.change(screen.getByLabelText("售价（GBP）"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("采购成本（¥）"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("包装长（cm）"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("包装宽（cm）"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("包装高（cm）"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("包装重量（g）"), { target: { value: "60" } });
    fireEvent.change(screen.getByLabelText("FBA 费用（GBP）"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "带入完整表单" }));
    expect(screen.getByLabelText("目标站点")).toHaveValue("UK");
    expect(screen.getByLabelText("售价（GBP） *")).toHaveValue(20);
    expect(screen.getByLabelText("采购单价（¥） *")).toHaveValue(30);
    expect(screen.getByLabelText("包装长（cm） *")).toHaveValue(15);
    expect(screen.getByLabelText("包装宽（cm） *")).toHaveValue(10);
    expect(screen.getByLabelText("包装高（cm） *")).toHaveValue(1);
    expect(screen.getByLabelText("包装重量（g） *")).toHaveValue(60);
    expect(screen.getByLabelText("包装加工费（¥）")).toHaveValue(null);
    expect(screen.getByLabelText("FBA 费用（GBP） *")).toHaveValue(3);
  });

  it("切换站点清空本币金额并保留人民币采购成本", async () => {
    render(<App repository={makeRepository()} />);
    await screen.findByLabelText("售价（CAD）");
    const site = screen.getByLabelText("目标站点");
    fireEvent.change(site, { target: { value: "UK" } });
    fireEvent.change(screen.getByLabelText("售价（GBP）"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("采购成本（¥）"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "US" } });
    expect(screen.getByLabelText("售价（USD）")).toHaveValue(null);
    expect(screen.getByLabelText("采购成本（¥）")).toHaveValue(30);
    expect(screen.queryByLabelText("销售税率（小数）")).not.toBeInTheDocument();
  });

  it("运营主管可查看待审核队列并执行审核", async () => {
    const snapshot = makeSnapshot();
    snapshot.pendingReviews = [candidate];
    const repository = makeRepository(snapshot);
    render(<App repository={repository} />);
    const navigation = await screen.findByRole("navigation", { name: "工作区" });
    fireEvent.click(within(navigation).getByRole("button", { name: /^主管审核/ }));
    expect(within(navigation).getByRole("button", { name: /^主管审核/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: SAMPLE_INPUT.productNameCn })).toBeInTheDocument();
    expect(document.querySelector(".review-thumb")).toBeInTheDocument();
    expect(screen.getByText(/回款占比/)).toBeInTheDocument();
    expect(screen.getByText(/侵权规避/)).toBeInTheDocument();
    expect(document.querySelector(".row-mid")).toBeInTheDocument();
    expect(screen.getAllByText("测试运营").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => expect(repository.reviewCandidate).toHaveBeenCalledWith(candidate, "approve", "", false));
  });

  it("主管可在风险提示后填写原因并例外通过旧加拿大记录", async () => {
    const snapshot = makeSnapshot();
    snapshot.pendingReviews = [candidate];
    const repository = makeRepository(snapshot);
    vi.mocked(repository.reviewCandidate)
      .mockResolvedValueOnce({ ok: false, message: "毛利率低于目标", overrideRequired: true,
        overrideReasons: ["毛利率达标"] })
      .mockResolvedValueOnce({ ok: true, message: "已审核" });
    render(<App repository={repository} />);
    fireEvent.click(await screen.findByRole("button", { name: /^主管审核/ }));
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    const dialog = await screen.findByRole("dialog", { name: "例外通过确认" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "例外通过原因" }),
      { target: { value: "主管确认可测试销售" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认例外通过" }));
    await waitFor(() => expect(repository.reviewCandidate).toHaveBeenLastCalledWith(
      candidate, "approve", "主管确认可测试销售", true));
  });

  it("产品图片预览卡三态常显并支持移除", async () => {
    render(<App repository={makeRepository()} />);
    fireEvent.click(await screen.findByRole("button", { name: /我的打回/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改并重提" }));
    expect(screen.getByText(/原记录产品图片/)).toBeInTheDocument();
    const file = new File(["image-data"], "主图_01.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText(/产品图片/), { target: { files: [file] } });
    expect(await screen.findByText(/主图_01\.png/)).toBeInTheDocument();
    expect(screen.getByText(/已就绪，提交时上传/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(screen.getByText(/原记录产品图片/)).toBeInTheDocument();
  });

  it("普通运营不显示主管审核入口", async () => {
    render(<App repository={makeRepository(makeSnapshot("运营"))} />);
    await screen.findByRole("button", { name: "选品录入与评审" });
    const navigation = screen.getByRole("navigation", { name: "工作区" });
    expect(within(navigation).queryByRole("button", { name: /^主管审核/ })).not.toBeInTheDocument();
  });

  it("可从我的打回记录载入原始数据并使用最新参数重提", async () => {
    render(<App repository={makeRepository()} />);
    fireEvent.click(await screen.findByRole("button", { name: /我的打回/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改并重提" }));
    expect(screen.getByRole("heading", { name: `修改候选产品 · ${SAMPLE_INPUT.productNameCn}` })).toBeInTheDocument();
    expect(screen.queryByLabelText("售价(CAD)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("产品中文名称 *")).toHaveValue(SAMPLE_INPUT.productNameCn);
    expect(screen.getByLabelText("供应商名称")).toHaveValue(SAMPLE_INPUT.supplierName);
    expect(screen.getByRole("button", { name: "修改并重新提交" })).toBeEnabled();
  });

  it("我的提交和我的打回分开显示，重提后回到我的提交", async () => {
    const snapshot = makeSnapshot();
    const approved: CandidateRecord = { ...candidate, recordId: "rec-approved", submitterId: "user-1", status: "审核通过", reviewPath: "主管自提免评审" };
    snapshot.mySubmissions.push(approved);
    const repository = makeRepository(snapshot);
    vi.mocked(repository.resubmitCandidate).mockImplementation(async () => {
      snapshot.returnedRecords = [];
      snapshot.mySubmissions = [approved, { ...snapshot.mySubmissions[0], status: "待审核" }];
      return { ok: true, message: "已重提", recordId: "rec-return" };
    });
    render(<App repository={repository} />);
    fireEvent.click(await screen.findByRole("button", { name: /我的提交/ }));
    expect(screen.queryByText("审核通过")).not.toBeInTheDocument();
    expect(screen.queryByText("补充专利依据")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /我的打回/ }));
    expect(screen.getByText("主管意见：补充专利依据")).toBeInTheDocument();
    expect(screen.queryByText("审核通过")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改并重提" }));
    fireEvent.click(screen.getByRole("button", { name: "修改并重新提交" }));
    expect(await screen.findByText("提交已写入登记表")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我的提交" })).toBeInTheDocument();
    expect(screen.getByText("待审核")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开本次记录" }));
    expect(repository.openRecord).toHaveBeenCalledWith("rec-return");
    fireEvent.click(screen.getByRole("button", { name: /我的打回/ }));
    expect(screen.getByText("当前没有需要修改的记录")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选品录入与评审" }));
    expect(screen.queryByText("已重提")).not.toBeInTheDocument();
  });

  it("审核页区分可审核队列和校验异常的待审核记录", async () => {
    const snapshot = makeSnapshot();
    snapshot.excludedPendingCount = 4;
    render(<App repository={makeRepository(snapshot)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^主管审核/ }));
    expect(screen.getByRole("heading", { name: "主管审核" })).toBeInTheDocument();
    expect(screen.getByText(/有 4 条记录缺少有效提交校验/)).toBeInTheDocument();
  });

  it("加拿大历史打回仍可按原口径录入小数", async () => {
    render(<App repository={makeRepository()} />);
    fireEvent.click(await screen.findByRole("button", { name: /我的打回/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改并重提" }));
    const purchase = screen.getByLabelText("采购单价(¥) *");
    fireEvent.focus(purchase);
    fireEvent.change(purchase, { target: { value: "0.5" } });
    expect(purchase).toHaveValue(0.5);

  });

  it("我的提交只搜索待审核记录，不展示审核历史", async () => {
    const snapshot = makeSnapshot("运营");
    snapshot.mySubmissions = [
      { ...candidate, recordId: "rec-a", skuNumber: "JP-100", status: "审核通过", submitterId: "user-1", input: { ...SAMPLE_INPUT, productNameCn: "猫咪梳", referenceAsins: "B012ABC" } },
      { ...candidate, recordId: "rec-b", skuNumber: "JP-200", status: "待审核", submitterId: "user-1", input: { ...SAMPLE_INPUT, productNameCn: "桌面灯", referenceAsins: "B099XYZ" } },
    ];
    render(<App repository={makeRepository(snapshot)} />);
    fireEvent.click(await screen.findByRole("button", { name: /我的提交/ }));
    const search = screen.getByLabelText("查找我的提交");
    fireEvent.change(search, { target: { value: "  b099xyz " } });
    expect(screen.getByText("找到 1 条匹配记录")).toBeInTheDocument();
    expect(screen.getByText("桌面灯")).toBeInTheDocument();
    expect(screen.queryByText("猫咪梳")).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "JP-100" } });
    expect(screen.queryByText("猫咪梳")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清空搜索" }));
    expect(screen.getByText("共 1 条待审批记录")).toBeInTheDocument();
  });

  it("待审批的加拿大记录先撤回，再以原记录重新编辑", async () => {
    const snapshot = makeSnapshot("运营");
    snapshot.mySubmissions = [{ ...candidate, recordId: "rec-own-pending", submitterId: "user-1" }];
    const repository = makeRepository(snapshot);
    vi.mocked(repository.withdrawCandidate).mockImplementation(async () => {
      snapshot.mySubmissions = [];
      return { ok: true, message: "已撤回", recordId: "rec-own-pending" };
    });
    render(<App repository={repository} />);
    fireEvent.click(await screen.findByRole("button", { name: /我的提交/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    await waitFor(() => expect(repository.withdrawCandidate).toHaveBeenCalledWith(
      expect.objectContaining({ recordId: "rec-own-pending" })));
    expect(await screen.findByRole("heading", { name: `修改候选产品 · ${SAMPLE_INPUT.productNameCn}` })).toBeInTheDocument();
  });

  it("主管图片可悬停放大、点击固定并用 Esc 关闭", async () => {
    const snapshot = makeSnapshot();
    snapshot.pendingReviews = [{ ...candidate, productImageToken: "mock-image" }];
    const repository = makeRepository(snapshot);
    vi.mocked(repository.getProductImageUrl).mockResolvedValue("data:image/png;base64,iVBORw0KGgo=");
    render(<App repository={repository} />);
    fireEvent.click(await screen.findByRole("button", { name: /^主管审核/ }));
    const trigger = await screen.findByRole("button", { name: /预览产品图片/ });
    fireEvent.pointerEnter(trigger, { pointerType: "mouse" });
    expect(await screen.findByRole("dialog", { name: /产品图片预览/ })).toBeInTheDocument();
    fireEvent.pointerLeave(trigger, { pointerType: "mouse" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /产品图片预览/ })).not.toBeInTheDocument());
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: /产品图片预览/ })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /产品图片预览/ })).not.toBeInTheDocument();
  });

  it("图片链接失效时明确提示加载失败", async () => {
    const snapshot = makeSnapshot();
    snapshot.pendingReviews = [{ ...candidate, productImageToken: "invalid-image" }];
    render(<App repository={makeRepository(snapshot)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^主管审核/ }));
    expect(await screen.findByText("加载失败")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /预览产品图片/ })).not.toBeInTheDocument();
  });

  it("六种主题可切换，偏好按当前用户保存在浏览器", async () => {
    const repository = makeRepository();
    const { unmount } = render(<App repository={repository} />);
    const picker = await screen.findByRole("group", { name: "主题色" });
    expect(within(picker).getAllByRole("button")).toHaveLength(6);
    fireEvent.click(within(picker).getByRole("button", { name: "暗黑" }));
    expect(within(picker).getByRole("button", { name: "暗黑" })).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("amazon-selection-theme:user-1")).toBe("dark");
    unmount();
    render(<App repository={repository} />);
    expect(await screen.findByRole("button", { name: "暗黑" })).toHaveAttribute("aria-pressed", "true");
  });
});
