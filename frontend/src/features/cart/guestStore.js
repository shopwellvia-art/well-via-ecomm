import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * Guest cart — client-side cart for signed-out visitors, persisted to
 * localStorage. Holds only {product_id, quantity} lines; product data is
 * joined at render time via useProductsByIds so prices/stock are never stale.
 *
 * On login, mergeGuestCart() replays these lines into the server cart
 * (POST /cart/items has increment semantics) and clears this store.
 */
export const useGuestCartStore = create(
  persist(
    (set, get) => ({
      items: [], // [{ product_id: number, quantity: number }]

      addItem: (productId, quantity = 1) =>
        set((s) => {
          const existing = s.items.find((i) => i.product_id === productId);
          if (existing) {
            return {
              items: s.items.map((i) =>
                i.product_id === productId
                  ? { ...i, quantity: Math.min(999, i.quantity + quantity) }
                  : i,
              ),
            };
          }
          return { items: [...s.items, { product_id: productId, quantity }] };
        }),

      setQuantity: (productId, quantity) =>
        set((s) => ({
          items:
            quantity <= 0
              ? s.items.filter((i) => i.product_id !== productId)
              : s.items.map((i) =>
                  i.product_id === productId
                    ? { ...i, quantity: Math.min(999, quantity) }
                    : i,
                ),
        })),

      removeItem: (productId) =>
        set((s) => ({ items: s.items.filter((i) => i.product_id !== productId) })),

      clear: () => set({ items: [] }),

      count: () => get().items.reduce((sum, i) => sum + i.quantity, 0),
    }),
    { name: 'guest-cart' },
  ),
);
