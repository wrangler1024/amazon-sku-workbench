import type { IOpenLink } from "@lark-opdev/block-bitable-api";

export interface MasterRef { id: string; tableId: string }

export function masterLink(ref: MasterRef, code: string): IOpenLink {
  return { type: "text", text: code, recordIds: [ref.id], tableId: ref.tableId,
    record_ids: [ref.id], table_id: ref.tableId };
}

export function hasMasterLink(value: unknown, id: string): boolean {
  if (Array.isArray(value)) return value.some((item) => item && typeof item === "object" &&
    ("id" in item && item.id === id || "record_id" in item && item.record_id === id));
  return Boolean(value && typeof value === "object" &&
    (("recordIds" in value && Array.isArray(value.recordIds) && value.recordIds.includes(id)) ||
      ("record_ids" in value && Array.isArray(value.record_ids) && value.record_ids.includes(id))));
}
