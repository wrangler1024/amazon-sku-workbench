import assert from 'node:assert/strict';
import test from 'node:test';
import {positionThumbnailPreview} from '../src/thumbnailPreview.ts';

const anchor = (left, top, width = 46, height = 46) => ({
  left,
  top,
  right: left + width,
  bottom: top + height,
});

test('thumbnail preview opens to the right when there is room', () => {
  assert.deepEqual(positionThumbnailPreview(anchor(24, 80), 1200, 800), {
    width: 300,
    left: 82,
    top: 80,
  });
});

test('thumbnail preview flips left near the viewport edge', () => {
  assert.deepEqual(positionThumbnailPreview(anchor(1080, 80), 1200, 800), {
    width: 300,
    left: 768,
    top: 80,
  });
});

test('thumbnail preview remains inside short and narrow viewports', () => {
  assert.deepEqual(positionThumbnailPreview(anchor(180, 330), 360, 420), {
    width: 300,
    left: 12,
    top: 78,
  });
});
