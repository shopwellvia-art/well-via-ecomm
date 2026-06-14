import { useMemo, useState } from 'react';
import { Plus, ShoppingBag, Check, Loader2 } from 'lucide-react';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Frequently bought together — source product plus up to 2 companions
 * pre-checked. User can deselect; combined price + action update accordingly.
 * Flat Amazon/Flipkart style card, no glass/shadow effects.
 */
export function FrequentlyBoughtTogether({ product, related, isLoading }) {
  const user = useAuthStore((s) => s.user);
  const addToCart = useAddToCart();
  const [doneAt, setDoneAt] = useState(0);
  const [addError, setAddError] = useState(null);

  const companions = useMemo(
    () => (related || []).filter((p) => p.stock > 0).slice(0, 2),
    [related],
  );

  const [selected, setSelected] = useState(() => new Set([product.id]));
  useMemo(() => {
    setSelected((prev) => {
      const next = new Set(prev);
      companions.forEach((p) => next.add(p.id));
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companions.length]);

  const all = [product, ...companions];
  const total = all
    .filter((p) => selected.has(p.id))
    .reduce((acc, p) => acc + Number(p.price), 0);
  const selectedCount = all.filter((p) => selected.has(p.id)).length;

  if (isLoading) {
    return <Skeleton className="mt-10 h-40 rounded-sm" />;
  }
  if (companions.length === 0) return null;

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function addAll() {
    if (!user) {
      window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
      return;
    }
    setAddError(null);
    const ids = all.filter((p) => selected.has(p.id)).map((p) => p.id);
    try {
      await Promise.all(ids.map((productId) => addToCart.mutateAsync({ productId, quantity: 1 })));
      setDoneAt(Date.now());
    } catch {
      setAddError('Couldn’t add items — please try again.');
    }
  }

  return (
    <section className="mt-10">
      <h2 className="mb-3 text-base font-semibold text-ink-primary">
        Frequently bought together
      </h2>

      <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated">
        {/* Product tiles row */}
        <div className="flex items-center gap-3 overflow-x-auto pb-1 sm:flex-wrap sm:gap-4 sm:overflow-visible border-b border-line-subtle p-4">
          {all.map((p, i) => (
            <div key={p.id} className="flex shrink-0 items-center gap-3">
              <BundleTile
                product={p}
                isSource={i === 0}
                selected={selected.has(p.id)}
                onToggle={() => i !== 0 && toggle(p.id)}
              />
              {i < all.length - 1 && (
                <Plus
                  className="size-4 shrink-0 text-ink-tertiary"
                  aria-hidden="true"
                />
              )}
            </div>
          ))}
        </div>

        {/* Total + CTA row */}
        <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-3">
          <div>
            <p className="text-xs text-ink-tertiary">
              Total {selectedCount > 1 ? `(${selectedCount} items)` : ''}
            </p>
            <p className="mt-0.5 text-xl font-semibold text-accent tabular-nums">
              {formatPrice(total)}
            </p>
          </div>
          <button
            type="button"
            onClick={addAll}
            disabled={selectedCount === 0 || addToCart.isPending}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-sm bg-accent px-5 text-sm font-semibold text-white transition-[background-color] hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40 disabled:pointer-events-none"
          >
            {addToCart.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Adding…
              </>
            ) : doneAt ? (
              <>
                <Check className="size-4" aria-hidden="true" />
                Added to Cart
              </>
            ) : (
              <>
                <ShoppingBag className="size-4" aria-hidden="true" />
                Add all to Cart
              </>
            )}
          </button>
        </div>
        {addError && (
          <p className="px-4 pb-3 text-xs text-danger">{addError}</p>
        )}
      </div>
    </section>
  );
}

function BundleTile({ product, isSource, selected, onToggle }) {
  return (
    <label
      className={[
        'flex cursor-pointer items-center gap-3 rounded-sm border p-2 transition-colors',
        selected
          ? 'border-accent/50 bg-accent/5'
          : 'border-line-subtle bg-bg-sunken hover:border-line-strong',
        isSource ? 'cursor-default' : '',
      ].join(' ')}
    >
      <input
        type="checkbox"
        checked={selected}
        disabled={isSource}
        onChange={onToggle}
        aria-label={`Include ${product.name} in bundle`}
        className="size-4 shrink-0 accent-accent"
      />
      <div className="size-14 shrink-0 overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated">
        <ProductMedia product={product} />
      </div>
      <div className="min-w-0 max-w-[160px]">
        <p className="line-clamp-2 text-xs font-medium text-ink-primary">{product.name}</p>
        <p className="mt-0.5 text-xs font-semibold text-accent tabular-nums">
          {formatPrice(product.price)}
        </p>
        {isSource && (
          <p className="text-[10px] font-semibold uppercase tracking-wide text-accent">
            This item
          </p>
        )}
      </div>
    </label>
  );
}
