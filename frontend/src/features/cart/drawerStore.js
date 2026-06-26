import { create } from 'zustand';

/**
 * Cart drawer open/close state — client-only UI state.
 * Keep separate from React Query's server cart data.
 *
 * Usage:
 *   const { isOpen, openDrawer, closeDrawer } = useCartDrawer();
 */
export const useCartDrawer = create((set) => ({
  isOpen: false,
  openDrawer: () => set({ isOpen: true }),
  closeDrawer: () => set({ isOpen: false }),
}));
