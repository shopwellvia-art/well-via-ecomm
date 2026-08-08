import { describe, it, expect, beforeEach } from 'vitest';
import { useAddedToCartModal } from '@/features/cart/addedModalStore.js';

const state = () => useAddedToCartModal.getState();

const ITEM = {
  id: 7,
  name: 'Beauty Sleep Gummies',
  image_url: '/media/products/sleep.png',
  price: '499.00',
  quantity: 2,
};

describe('addedModalStore', () => {
  beforeEach(() => {
    state().close();
  });

  it('starts closed', () => {
    expect(state().item).toBeNull();
    expect(state().extraCount).toBe(0);
  });

  it('opens with the line that was added', () => {
    state().showAdded(ITEM);
    expect(state().item).toEqual(ITEM);
  });

  it('defaults extraCount to 0 for a single-product add', () => {
    state().showAdded(ITEM);
    expect(state().extraCount).toBe(0);
  });

  it('carries extraCount for a bundle add', () => {
    state().showAdded(ITEM, { extraCount: 2 });
    expect(state().extraCount).toBe(2);
  });

  it('close clears the item AND the bundle count', () => {
    // extraCount leaking into the next add would make a single-product popup
    // claim "+2 more items".
    state().showAdded(ITEM, { extraCount: 2 });
    state().close();
    expect(state().item).toBeNull();
    expect(state().extraCount).toBe(0);
  });

  // The modal renders `item.name` and `item.price` unconditionally once open, so
  // opening on a nullish payload would blank the popup or throw.
  it('ignores an empty payload rather than opening on nothing', () => {
    state().showAdded(null);
    expect(state().item).toBeNull();
    state().showAdded(undefined);
    expect(state().item).toBeNull();
  });

  it('a second add replaces the first rather than stacking', () => {
    state().showAdded(ITEM);
    state().showAdded({ ...ITEM, id: 9, name: 'Immunity Gummies' });
    expect(state().item.id).toBe(9);
    expect(state().item.name).toBe('Immunity Gummies');
  });
});
