import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { cartApi } from './api.js';
import { useGuestCartStore } from './guestStore.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useProductsByIds } from '@/features/products/hooks.js';

const CART_KEY = ['cart'];

function useInvalidateCart() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: CART_KEY });
}

/**
 * Compose a server-shaped CartRead from the guest store + live product data,
 * so every cart consumer (drawer, badge, checkout gate) works unchanged for
 * signed-out visitors. Lines whose product no longer exists are dropped.
 * Guest carts have no tax/coupon — those apply after login.
 */
function buildGuestCart(lines, products) {
  const byId = new Map((products ?? []).map((p) => [p.id, p]));
  const items = lines
    .filter((l) => byId.has(l.product_id))
    .map((l) => {
      const p = byId.get(l.product_id);
      const unit = Number(p.price);
      return {
        product_id: l.product_id,
        name: p.name,
        quantity: l.quantity,
        unit_price: p.price,
        compare_at_price: p.compare_at_price,
        line_subtotal: unit * l.quantity,
        line_tax: 0,
        line_total: unit * l.quantity,
        // Extras the server cart doesn't carry but the UI can use:
        image_url: p.image_url,
        stock: p.stock,
      };
    });
  const subtotal = items.reduce((s, i) => s + i.line_subtotal, 0);
  return {
    items,
    subtotal,
    tax_amount: 0,
    discount_amount: 0,
    total: subtotal,
    coupon_code: null,
    currency: 'INR',
    is_guest: true,
  };
}

/**
 * Cart query — server cart when signed in, composed guest cart otherwise.
 * Same signature either way, so no call site changes.
 */
export function useCart() {
  const token = useAuthStore((s) => s.accessToken);
  const guestLines = useGuestCartStore((s) => s.items);

  const serverQuery = useQuery({
    queryKey: CART_KEY,
    queryFn: cartApi.get,
    // The cart is per-user — don't fire a doomed request when signed out.
    enabled: !!token,
    retry: false,
  });

  const ids = useMemo(() => guestLines.map((l) => l.product_id), [guestLines]);
  const productsQuery = useProductsByIds(!token && ids.length ? ids : []);

  const guestData = useMemo(() => {
    if (token) return undefined;
    if (!guestLines.length) return buildGuestCart([], []);
    if (!productsQuery.data) return undefined;
    return buildGuestCart(guestLines, productsQuery.data);
  }, [token, guestLines, productsQuery.data]);

  if (token) return serverQuery;
  return {
    ...productsQuery,
    data: guestData,
    isLoading: ids.length > 0 && productsQuery.isLoading,
    isError: productsQuery.isError,
    refetch: productsQuery.refetch,
  };
}

/* ── Mutations — route to the server when signed in, the guest store when not.
      Wrapped in useMutation either way so `isPending` etc. behave the same. ── */

function useTokenPresent() {
  return !!useAuthStore((s) => s.accessToken);
}

export function useAddToCart() {
  const invalidate = useInvalidateCart();
  const hasToken = useTokenPresent();
  return useMutation({
    mutationFn: ({ productId, quantity = 1 }) => {
      if (hasToken) return cartApi.addItem(productId, quantity);
      useGuestCartStore.getState().addItem(productId, quantity);
      return Promise.resolve();
    },
    onSuccess: invalidate,
  });
}

export function useRemoveFromCart() {
  const invalidate = useInvalidateCart();
  const hasToken = useTokenPresent();
  return useMutation({
    mutationFn: (productId) => {
      if (hasToken) return cartApi.removeItem(productId);
      useGuestCartStore.getState().removeItem(productId);
      return Promise.resolve();
    },
    onSuccess: invalidate,
  });
}

export function useUpdateCartQuantity() {
  const invalidate = useInvalidateCart();
  const hasToken = useTokenPresent();
  return useMutation({
    mutationFn: ({ productId, quantity }) => {
      if (hasToken) return cartApi.setQuantity(productId, quantity);
      useGuestCartStore.getState().setQuantity(productId, quantity);
      return Promise.resolve();
    },
    onSuccess: invalidate,
  });
}

/* Coupons stay server-only — the drawer shows a "log in to apply coupons"
   hint for guests instead of calling these. */

export function useApplyCoupon() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code) => cartApi.applyCoupon(code),
    // The endpoint returns the full updated cart — write it straight to cache
    // so the UI doesn't need a follow-up refetch.
    onSuccess: (data) => qc.setQueryData(CART_KEY, data),
  });
}

export function useRemoveCoupon() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => cartApi.removeCoupon(),
    onSuccess: (data) => qc.setQueryData(CART_KEY, data),
  });
}
