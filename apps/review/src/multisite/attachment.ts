export function firstAttachmentToken(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const first: unknown = value[0];
  if (!first || typeof first !== "object") return "";
  const attachment = first as { token?: unknown; file_token?: unknown };
  if (typeof attachment.token === "string") return attachment.token;
  return typeof attachment.file_token === "string" ? attachment.file_token : "";
}
