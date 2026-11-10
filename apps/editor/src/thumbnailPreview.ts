export type PreviewAnchor = {
  top: number;
  right: number;
  bottom: number;
  left: number;
};

export function positionThumbnailPreview(
  anchor: PreviewAnchor,
  viewportWidth: number,
  viewportHeight: number,
  targetWidth = 300,
  estimatedHeight = 330,
) {
  const margin = 12;
  const gap = 12;
  const width = Math.max(160, Math.min(targetWidth, viewportWidth - margin * 2));
  const rightSpace = viewportWidth - anchor.right - gap - margin;
  const left = rightSpace >= width
    ? anchor.right + gap
    : anchor.left - gap - width;
  const maxLeft = Math.max(margin, viewportWidth - width - margin);
  const maxTop = Math.max(margin, viewportHeight - estimatedHeight - margin);
  return {
    width,
    left: Math.max(margin, Math.min(left, maxLeft)),
    top: Math.max(margin, Math.min(anchor.top, maxTop)),
  };
}
