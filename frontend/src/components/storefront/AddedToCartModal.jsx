import { useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { CheckCircle2 } from 'lucide-react';
import { formatPrice } from '@/lib/utils';
import WImage from '@/components/storefront/WImage';
import { useAddedToCartModal } from '@/features/cart/addedModalStore.js';
import { useCart } from '@/features/cart/hooks';

/**
 * "Added to cart" confirmation popup.
 *
 * Adding to the cart used to be silent from a product grid — the header badge
 * ticked up and nothing else happened, so there was no way to tell a successful
 * add from a dead button. This is the confirmation: what went in, what the cart
 * now totals, and the two things a shopper wants next (keep browsing, or go pay).
 *
 * Mounted once in `Layout.jsx`; fired from anywhere via
 * `useAddedToCartModal().showAdded(...)`.
 *
 * Deliberately does NOT auto-dismiss. It carries a subtotal and two actions, and
 * a panel that vanishes mid-read is worse than one that waits — Escape, the
 * backdrop, the × and "Continue shopping" all close it, so dismissing costs one
 * keystroke. Transient "saved!" feedback that needs no decision goes through
 * `toast` instead.
 */
export default function AddedToCartModal() {
  const item = useAddedToCartModal((s) => s.item);
  const extraCount = useAddedToCartModal((s) => s.extraCount);
  const close = useAddedToCartModal((s) => s.close);
  const { data: cart } = useCart();
  const { pathname } = useLocation();

  const continueRef = useRef(null);
  // Where focus was before the popup stole it, so it can be handed back on
  // close — otherwise dismissing drops a keyboard user at the top of the page
  // instead of on the button they just pressed.
  const restoreRef = useRef(null);

  const open = !!item;

  // Escape to close, and lock the page behind the popup so a scroll gesture
  // over the backdrop does not drift the catalogue underneath.
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement;
    const onKey = (e) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Focus "Continue shopping" rather than "View cart": it is the
    // non-destructive default, so Enter-mashing cannot navigate the shopper away
    // from a catalogue they were halfway through.
    const t = setTimeout(() => continueRef.current?.focus(), 0);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
      clearTimeout(t);
      const restore = restoreRef.current;
      if (restore instanceof HTMLElement && document.contains(restore)) restore.focus();
    };
  }, [open, close]);

  // Any navigation dismisses it — including the "View cart" link below, and the
  // back button. Without this, a popup left open would reappear over whatever
  // page the shopper landed on.
  useEffect(() => {
    close();
  }, [pathname, close]);

  if (!item) return null;

  const quantity = item.quantity || 1;
  const lineTotal = Number(item.price) * quantity;
  const cartCount = (cart?.items ?? []).reduce((sum, i) => sum + (i.quantity || 0), 0);
  const cartTotal = cart?.total ?? 0;

  return (
    <div className="fixed inset-0 z-[96] flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close"
        onClick={close}
        className="absolute inset-0 cursor-default bg-wink/50 animate-dim"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="added-to-cart-title"
        className="relative w-full max-w-md rounded-xl2 border border-wline bg-wcard p-5 shadow-xl animate-rise sm:p-6"
      >
        <button
          type="button"
          onClick={close}
          aria-label="Close"
          className="absolute right-3 top-3 grid size-8 cursor-pointer place-items-center rounded-full border-0 bg-transparent text-wmuted transition-colors hover:text-wink"
        >
          ×
        </button>

        <h2
          id="added-to-cart-title"
          className="m-0 mb-4 flex items-center gap-2 pr-8 font-wserif text-[19px] font-semibold text-wink"
        >
          <CheckCircle2 className="size-5 shrink-0 text-wgreen" aria-hidden="true" />
          Added to cart
        </h2>

        {/* What went in */}
        <div className="flex items-center gap-3 rounded-xl border border-wline bg-wpaper p-3">
          <WImage
            src={item.image_url}
            alt={item.name}
            className="size-14 shrink-0 rounded-lg bg-white object-contain p-1"
          />
          <div className="min-w-0 flex-1">
            <p className="m-0 truncate text-[13.5px] font-medium text-wink">{item.name}</p>
            <p className="m-0 mt-0.5 text-[12px] text-wmuted">
              Qty {quantity}
              {extraCount > 0 && ` · +${extraCount} more item${extraCount === 1 ? '' : 's'}`}
            </p>
          </div>
          <p className="m-0 shrink-0 text-[13.5px] font-semibold text-wink">
            {formatPrice(lineTotal)}
          </p>
        </div>

        {/* Where the cart now stands. Rendered only once the cart query has
            resolved — a subtotal of ₹0.00 flashing under a successful add reads
            as a failure. */}
        {cartCount > 0 && (
          <div className="mt-3 flex items-baseline justify-between border-t border-wline pt-3">
            <span className="text-[12.5px] text-wmuted">
              Cart subtotal ({cartCount} item{cartCount === 1 ? '' : 's'})
            </span>
            <span className="text-[15px] font-semibold text-wink">{formatPrice(cartTotal)}</span>
          </div>
        )}

        <div className="mt-5 flex flex-col gap-2 sm:flex-row">
          <button
            ref={continueRef}
            type="button"
            onClick={close}
            className="flex-1 cursor-pointer rounded-full border border-[#08112C]/40 bg-transparent px-5 py-2.5 text-[13px] font-medium text-[#08112C] transition-colors hover:bg-[#08112C]/5"
          >
            Continue shopping
          </button>
          <Link
            to="/cart"
            onClick={close}
            className="flex-1 rounded-full bg-[#08112C] px-5 py-2.5 text-center text-[13px] font-semibold text-white no-underline transition-opacity hover:opacity-90"
          >
            View cart
          </Link>
        </div>
      </div>
    </div>
  );
}
