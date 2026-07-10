import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';
import { mergeGuestCart } from '@/features/cart/mergeGuestCart.js';
import { useGuestCartStore } from '@/features/cart/guestStore.js';
import { captureReferralFromUrl } from '@/features/loyalty/referralCapture.js';

/**
 * One-shot effects that run on app boot:
 *  1. If we have a persisted token, refetch /auth/me so the user object
 *     reflects fresh roles/permissions/points (the persisted copy could be
 *     stale after an admin made changes).
 *  2. On every navigation, if the URL carries `?ref=<code>`, stash it in
 *     sessionStorage so a later signup will attach the referral.
 *  3. When a session appears (login/register/Google callback — any path that
 *     sets an access token) and the guest cart holds items, replay them into
 *     the server cart. Single choke point for guest-cart merging.
 *
 * Renders nothing.
 */
export default function AuthBootstrap() {
  const token = useAuthStore((s) => s.accessToken);
  const setUser = useAuthStore((s) => s.setUser);
  const logout = useAuthStore((s) => s.logout);
  const location = useLocation();
  const fetched = useRef(false);
  const queryClient = useQueryClient();
  const merging = useRef(false);

  useEffect(() => {
    if (!token || merging.current) return;
    if (!useGuestCartStore.getState().items.length) return;
    merging.current = true;
    mergeGuestCart(queryClient)
      .catch(() => {}) // network/5xx → lines stay for the next transition
      .finally(() => {
        merging.current = false;
      });
  }, [token, queryClient]);

  useEffect(() => {
    if (!token || fetched.current) return;
    fetched.current = true;
    authApi.me().then(setUser).catch((err) => {
      // 401 → the interceptor already cleared the session. Any other error,
      // we leave the persisted user in place; they can still navigate.
      if (err?.response?.status === 401) logout();
    });
  }, [token, setUser, logout]);

  // Capture ?ref= from the current location any time it changes. Safe to call
  // repeatedly — only updates sessionStorage when a code is actually present.
  useEffect(() => {
    captureReferralFromUrl(location.search);
  }, [location.search]);

  return null;
}
