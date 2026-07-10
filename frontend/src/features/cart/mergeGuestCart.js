import { cartApi } from './api.js';
import { useGuestCartStore } from './guestStore.js';

/**
 * Replay the guest cart into the server cart after login.
 *
 * POST /cart/items increments the existing line (Redis hincrby server-side),
 * which is exactly merge semantics. Per-item 4xx (product deleted, out of
 * stock rules) are tolerated so one dead line can't block the rest. The
 * guest store is cleared only after the loop completes, and the caller's
 * React Query cache is invalidated so the header badge/drawer refresh.
 */
export async function mergeGuestCart(queryClient) {
  const store = useGuestCartStore.getState();
  const items = [...store.items];
  if (!items.length) return false;

  try {
    for (const line of items) {
      try {
        await cartApi.addItem(line.product_id, line.quantity);
      } catch (err) {
        const status = err?.response?.status;
        // 4xx → this line is no longer addable; drop it. Anything else
        // (network, 5xx) → abort; unmerged lines stay for a later retry.
        if (!(status >= 400 && status < 500)) throw err;
      }
      // Remove each line as it lands so an abort mid-way can't replay
      // (and double) the already-merged lines on the next attempt.
      useGuestCartStore.getState().removeItem(line.product_id);
    }
  } finally {
    queryClient?.invalidateQueries({ queryKey: ['cart'] });
  }
  return true;
}
