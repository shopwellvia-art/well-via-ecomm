import { useNavigate } from 'react-router-dom';
import { Heart } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { buttonPress, attentionPulse } from '@/lib/motion.js';
import {
  useAddToWishlist,
  useIsInWishlist,
  useRemoveFromWishlist,
} from './hooks.js';

/**
 * Reusable heart button. Two visual variants:
 *
 * - "overlay" — circular, glassy, semi-transparent. Sits over product imagery
 *   (e.g. top-right of a ProductCard). Stops link navigation on click.
 *
 * - "inline" — flat row with a label, used inside panels like BuyBox.
 *
 * Signed-out users are bounced to /login with a `next` param back to the page
 * they came from. We never silently fail.
 */
export function WishlistButton({
  productId,
  variant = 'overlay',
  size = 'md',
  className,
  redirectTo,
}) {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isSaved = useIsInWishlist(productId);
  const add = useAddToWishlist();
  const remove = useRemoveFromWishlist();
  const reducedMotion = useReducedMotion();

  const pending = add.isPending || remove.isPending;

  function handleClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!user) {
      const next = redirectTo || window.location.pathname + window.location.search;
      navigate(`/login?next=${encodeURIComponent(next)}`);
      return;
    }
    if (isSaved) remove.mutate(productId);
    else add.mutate(productId);
  }

  const label = isSaved ? 'Remove from wishlist' : 'Save to wishlist';

  if (variant === 'inline') {
    return (
      <motion.button
        type="button"
        onClick={handleClick}
        aria-pressed={isSaved}
        aria-label={label}
        disabled={pending}
        whileTap={reducedMotion ? undefined : buttonPress}
        className={cn(
          'inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg border text-sm font-medium transition-colors',
          'focus-visible:focus-ring',
          'disabled:opacity-50 disabled:pointer-events-none',
          isSaved
            ? 'border-danger/30 bg-danger/8 text-danger hover:bg-danger/12'
            : 'border-line-subtle bg-bg-sunken text-ink-secondary hover:border-line-strong hover:text-ink-primary',
          className,
        )}
      >
        <motion.span
          key={`heart-inline-${isSaved}`}
          animate={!reducedMotion && isSaved ? attentionPulse : undefined}
          className="flex items-center"
        >
          <Heart
            className={cn('size-4 transition-transform', isSaved && 'fill-current')}
            aria-hidden="true"
          />
        </motion.span>
        {isSaved ? 'Saved to wishlist' : 'Save to wishlist'}
      </motion.button>
    );
  }

  // overlay — circular glass button
  const sizeClass = size === 'sm' ? 'size-8' : 'size-9';
  const iconSize = size === 'sm' ? 'size-4' : 'size-[18px]';

  return (
    <motion.button
      type="button"
      onClick={handleClick}
      aria-pressed={isSaved}
      aria-label={label}
      disabled={pending}
      whileTap={reducedMotion ? undefined : buttonPress}
      className={cn(
        'grid place-items-center rounded-full',
        'bg-bg-elevated/85 backdrop-blur border border-line-subtle shadow-sm',
        'transition-[background-color,color,border-color] duration-150',
        'focus-visible:focus-ring',
        'disabled:opacity-60 disabled:pointer-events-none',
        isSaved
          ? 'border-danger/30 text-danger hover:bg-danger/12'
          : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
        sizeClass,
        className,
      )}
    >
      <motion.span
        key={`heart-overlay-${isSaved}`}
        animate={!reducedMotion && isSaved ? attentionPulse : undefined}
        className="flex items-center"
      >
        <Heart
          className={cn(iconSize, 'transition-transform', isSaved && 'fill-current')}
          aria-hidden="true"
        />
      </motion.span>
    </motion.button>
  );
}
