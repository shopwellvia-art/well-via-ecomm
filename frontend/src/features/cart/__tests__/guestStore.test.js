import { describe, it, expect, beforeEach } from 'vitest';
import { useGuestCartStore } from '@/features/cart/guestStore.js';

const store = () => useGuestCartStore.getState();

beforeEach(() => {
  useGuestCartStore.setState({ items: [] });
});

describe('guest cart store', () => {
  it('adds a new line and increments an existing one', () => {
    store().addItem(1, 2);
    store().addItem(2); // default quantity 1
    store().addItem(1, 3);
    expect(store().items).toEqual([
      { product_id: 1, quantity: 5 },
      { product_id: 2, quantity: 1 },
    ]);
  });

  it('clamps a runaway quantity at 999', () => {
    store().addItem(1, 998);
    store().addItem(1, 50);
    expect(store().items).toEqual([{ product_id: 1, quantity: 999 }]);
  });

  it('setQuantity replaces (not increments) and clamps at 999', () => {
    store().addItem(1, 2);
    store().setQuantity(1, 7);
    expect(store().items).toEqual([{ product_id: 1, quantity: 7 }]);
    store().setQuantity(1, 5000);
    expect(store().items[0].quantity).toBe(999);
  });

  it('setQuantity to zero or below removes the line', () => {
    store().addItem(1, 2);
    store().addItem(2, 1);
    store().setQuantity(1, 0);
    expect(store().items).toEqual([{ product_id: 2, quantity: 1 }]);
    store().setQuantity(2, -3);
    expect(store().items).toEqual([]);
  });

  it('removeItem drops only the matching line; clear empties everything', () => {
    store().addItem(1, 1);
    store().addItem(2, 4);
    store().removeItem(1);
    expect(store().items).toEqual([{ product_id: 2, quantity: 4 }]);
    store().clear();
    expect(store().items).toEqual([]);
  });

  it('count sums quantities across lines (header badge semantics)', () => {
    expect(store().count()).toBe(0);
    store().addItem(1, 2);
    store().addItem(2, 3);
    expect(store().count()).toBe(5);
  });
});
