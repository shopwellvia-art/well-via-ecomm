import { create } from 'zustand';

/**
 * "Added to cart" confirmation state — client-only UI state.
 *
 * A store rather than local state in each card because there are six places
 * that add to the cart (product grids, the homepage rail, the PDP buy panel,
 * the PDP sticky bar, frequently-bought-together, and wishlist rows) and the
 * confirmation must look and behave identically from all of them. The modal is
 * mounted once in `Layout.jsx`, next to the cart drawer.
 *
 * Kept separate from `drawerStore` (the slide-in cart) deliberately: they are
 * different surfaces and must never both be open. Nothing opens both today, and
 * the modal's own CTA opens the full cart page rather than the drawer.
 *
 * Usage — fire it from a mutation's onSuccess, never before, so the popup can
 * only ever claim something that actually landed in the cart:
 *
 *   addToCart.mutate(
 *     { productId: id, quantity: 1 },
 *     { onSuccess: () => showAdded({ id, name, image_url, price, quantity: 1 }) },
 *   );
 */
export const useAddedToCartModal = create((set) => ({
  /** The line just added: { id, name, image_url, price, quantity } — null when closed. */
  item: null,
  /** Bundle adds ("frequently bought together") name one product and count the rest. */
  extraCount: 0,

  showAdded: (item, { extraCount = 0 } = {}) => {
    if (!item) return;
    set({ item, extraCount });
  },

  close: () => set({ item: null, extraCount: 0 }),
}));
