import { describe, it, expect, vi, beforeEach } from 'vitest';

const { addItemMock } = vi.hoisted(() => ({ addItemMock: vi.fn() }));

// hooks.js / mergeGuestCart.js import './api.js' — same resolved module, so
// this mock intercepts both. Only addItem is exercised here.
vi.mock('@/features/cart/api.js', () => ({
  cartApi: { addItem: addItemMock },
}));

import { buildGuestCart } from '@/features/cart/hooks.js';
import { mergeGuestCart } from '@/features/cart/mergeGuestCart.js';
import { useGuestCartStore } from '@/features/cart/guestStore.js';

const PRODUCTS = [
  {
    id: 1,
    name: 'Sleep Gummies',
    price: '100.00',
    compare_at_price: '150.00',
    image_url: '/media/sleep.png',
    stock: 10,
  },
  { id: 2, name: 'Immunity Gummies', price: '49.50', compare_at_price: null, image_url: null, stock: 3 },
];

describe('buildGuestCart (server-shaped cart composed from guest lines)', () => {
  it('joins lines to live products and totals them like the server cart', () => {
    const cart = buildGuestCart(
      [
        { product_id: 1, quantity: 2 },
        { product_id: 2, quantity: 1 },
      ],
      PRODUCTS,
    );
    expect(cart.items).toHaveLength(2);
    expect(cart.items[0]).toMatchObject({
      product_id: 1,
      name: 'Sleep Gummies',
      quantity: 2,
      unit_price: '100.00',
      line_subtotal: 200,
      line_tax: 0,
      line_total: 200,
      image_url: '/media/sleep.png',
      stock: 10,
    });
    expect(cart.subtotal).toBeCloseTo(249.5);
    expect(cart.total).toBeCloseTo(249.5); // no tax/coupon for guests
    expect(cart).toMatchObject({
      tax_amount: 0,
      discount_amount: 0,
      coupon_code: null,
      currency: 'INR',
      is_guest: true,
    });
  });

  it('drops lines whose product no longer exists', () => {
    const cart = buildGuestCart(
      [
        { product_id: 1, quantity: 1 },
        { product_id: 999, quantity: 4 },
      ],
      PRODUCTS,
    );
    expect(cart.items.map((i) => i.product_id)).toEqual([1]);
    expect(cart.subtotal).toBe(100);
  });

  it('produces a valid empty cart with no lines', () => {
    const cart = buildGuestCart([], []);
    expect(cart.items).toEqual([]);
    expect(cart.subtotal).toBe(0);
    expect(cart.total).toBe(0);
  });
});

describe('mergeGuestCart (replay guest lines into the server cart on login)', () => {
  const qc = { invalidateQueries: vi.fn() };

  beforeEach(() => {
    addItemMock.mockReset();
    qc.invalidateQueries.mockReset();
    useGuestCartStore.setState({ items: [] });
  });

  it('is a no-op returning false when the guest cart is empty', async () => {
    await expect(mergeGuestCart(qc)).resolves.toBe(false);
    expect(addItemMock).not.toHaveBeenCalled();
    expect(qc.invalidateQueries).not.toHaveBeenCalled();
  });

  it('replays every line, clears the store, and invalidates the cart query', async () => {
    useGuestCartStore.setState({
      items: [
        { product_id: 1, quantity: 2 },
        { product_id: 2, quantity: 3 },
      ],
    });
    addItemMock.mockResolvedValue({});

    await expect(mergeGuestCart(qc)).resolves.toBe(true);
    expect(addItemMock.mock.calls).toEqual([
      [1, 2],
      [2, 3],
    ]);
    expect(useGuestCartStore.getState().items).toEqual([]);
    expect(qc.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['cart'] });
  });

  it('tolerates a per-line 4xx (dead product) and still merges the rest', async () => {
    useGuestCartStore.setState({
      items: [
        { product_id: 1, quantity: 1 },
        { product_id: 2, quantity: 2 },
      ],
    });
    addItemMock
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({});

    await expect(mergeGuestCart(qc)).resolves.toBe(true);
    expect(addItemMock).toHaveBeenCalledTimes(2);
    // The 404 line is dropped, not left behind to fail forever.
    expect(useGuestCartStore.getState().items).toEqual([]);
  });

  it('aborts on a 5xx, keeping unmerged lines for a later retry', async () => {
    useGuestCartStore.setState({
      items: [
        { product_id: 1, quantity: 1 },
        { product_id: 2, quantity: 2 },
      ],
    });
    addItemMock
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce({ response: { status: 500 } });

    await expect(mergeGuestCart(qc)).rejects.toBeTruthy();
    // Line 1 landed server-side and was removed locally (no double-merge on
    // retry); line 2 never landed and stays.
    expect(useGuestCartStore.getState().items).toEqual([
      { product_id: 2, quantity: 2 },
    ]);
    // Cache still invalidated so the UI reflects the partial merge.
    expect(qc.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['cart'] });
  });

  it('aborts on a network error (no response object at all)', async () => {
    useGuestCartStore.setState({ items: [{ product_id: 1, quantity: 1 }] });
    addItemMock.mockRejectedValueOnce(new Error('network down'));

    await expect(mergeGuestCart(qc)).rejects.toThrow('network down');
    expect(useGuestCartStore.getState().items).toEqual([
      { product_id: 1, quantity: 1 },
    ]);
  });
});
