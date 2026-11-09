import type { SelectionRepository } from "../domain/types";

export async function createSelectionRepository(): Promise<SelectionRepository> {
  const params = new URLSearchParams(window.location.search);
  if (params.get("mock") === "1") {
    const { createMockSelectionRepository } = await import("./mock");
    return createMockSelectionRepository();
  }
  const { createLarkSelectionRepository } = await import("./lark");
  return createLarkSelectionRepository();
}
