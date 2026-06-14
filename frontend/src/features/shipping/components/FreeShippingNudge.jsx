import { Truck, CheckCircle2 } from 'lucide-react';
import { usePublicSettings } from '@/features/settings/public.js';
import { formatPrice } from '@/lib/utils.js';

/**
 * Banner that nudges customers toward the free-shipping threshold.
 *
 * Reads the threshold from `/settings/public`. Hides itself entirely
 * when the threshold is 0 (admin disabled). Renders two states:
 *
 *   - Below threshold:  "Add ₹X more to unlock free shipping"
 *   - At/above:         "✓ Free shipping unlocked"
 *
 * `subtotal` should be the cart's (or buy-now line's) subtotal.
 */
export default function FreeShippingNudge({ subtotal }) {
  const { data: cfg } = usePublicSettings();
  const threshold = Number(cfg?.['shipping.free_threshold'] || 0);
  if (!threshold || threshold <= 0) return null;

  const remaining = Math.max(0, threshold - Number(subtotal || 0));
  const unlocked = remaining <= 0;

  return (
    <div
      className={
        'flex items-center gap-2 rounded-sm border px-3 py-2 text-xs ' +
        (unlocked
          ? 'border-success/30 bg-success/12 text-success'
          : 'border-accent/30 bg-accent/12 text-accent')
      }
    >
      {unlocked ? (
        <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
      ) : (
        <Truck className="size-4 shrink-0" aria-hidden="true" />
      )}
      <p className="flex-1">
        {unlocked ? (
          <>
            <span className="font-semibold">Free shipping unlocked</span>{' '}
            <span className="text-ink-secondary">on prepaid orders.</span>
          </>
        ) : (
          <>
            Add <span className="font-semibold">{formatPrice(remaining)}</span>{' '}
            more to unlock <span className="font-semibold">free shipping</span>{' '}
            on prepaid orders.
          </>
        )}
      </p>
    </div>
  );
}
