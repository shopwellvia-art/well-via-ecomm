import { describe, it, expect } from 'vitest';
import {
  moveTo,
  sameOrder,
  restoreOrder,
} from '@/components/admin/ProductImageManager.jsx';

const IMAGES = [{ id: 4 }, { id: 7 }, { id: 9 }, { id: 11 }];
const ids = (list) => list.map((img) => img.id);

describe('moveTo', () => {
  it('moves an image forward, shifting the ones it passes back', () => {
    expect(ids(moveTo(IMAGES, 4, 2))).toEqual([7, 9, 4, 11]);
  });

  it('moves an image backward', () => {
    expect(ids(moveTo(IMAGES, 11, 0))).toEqual([11, 4, 7, 9]);
  });

  it('never mutates the input — the pre-drag order stays undo-able', () => {
    const before = ids(IMAGES);
    moveTo(IMAGES, 4, 3);
    expect(ids(IMAGES)).toEqual(before);
  });

  it('returns the same reference for a no-op, so callers can skip the request', () => {
    expect(moveTo(IMAGES, 7, 1)).toBe(IMAGES); // already there
    expect(moveTo(IMAGES, 4, -1)).toBe(IMAGES); // past the front
    expect(moveTo(IMAGES, 11, 4)).toBe(IMAGES); // past the end
    expect(moveTo(IMAGES, 999, 0)).toBe(IMAGES); // unknown id
  });
});

describe('restoreOrder', () => {
  it('puts a cancelled or rejected drag back the way it was', () => {
    const dragged = moveTo(IMAGES, 11, 0);
    expect(ids(restoreOrder(dragged, ids(IMAGES)))).toEqual([4, 7, 9, 11]);
  });

  it('keeps images the saved order never mentioned instead of dropping them', () => {
    // e.g. an upload landed while the reorder request was in flight.
    const withNew = [...IMAGES, { id: 20 }];
    expect(ids(restoreOrder(withNew, [9, 4, 7, 11]))).toEqual([9, 4, 7, 11, 20]);
  });

  it('skips ids that no longer exist', () => {
    expect(ids(restoreOrder(IMAGES, [9, 999, 4, 7, 11]))).toEqual([9, 4, 7, 11]);
  });
});

describe('sameOrder', () => {
  it('is true only when the ids match position for position', () => {
    expect(sameOrder([4, 7, 9], [4, 7, 9])).toBe(true);
    expect(sameOrder([4, 7, 9], [4, 9, 7])).toBe(false);
    expect(sameOrder([4, 7], [4, 7, 9])).toBe(false);
  });
});
