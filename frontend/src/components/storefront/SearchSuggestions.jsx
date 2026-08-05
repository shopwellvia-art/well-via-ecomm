import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { cn, formatPrice } from '@/lib/utils';
import WImage from '@/components/storefront/WImage';
import { SearchIcon } from '@/components/storefront/Icons';
import { useGlobalSearch, MIN_QUERY_LENGTH } from '@/features/search/hooks.js';
import { matchPages } from '@/features/search/pages.js';

/**
 * Global-search suggestions panel.
 *
 * Renders under whichever search input is active and lists everything matching
 * what has been typed: products (relevance-ranked by the API), categories, and
 * storefront pages (matched client-side — see features/search/pages.js).
 *
 * Keyboard navigation is exposed imperatively rather than handled here, because
 * the input lives in the header (three of them, at three breakpoints) while the
 * list lives in this component. The header forwards its input's arrow/Enter keys
 * to the handle below; this component stays the single owner of `activeIndex`,
 * so the highlight can never disagree with what Enter would open.
 *
 * Handle: { moveDown(), moveUp(), openActive() -> to|null, reset(), hasActive() }
 *
 * Props:
 *   query      — raw text from the input
 *   open       — whether the panel is showing (gates fetching too)
 *   isSignedIn — hides account-only page hits from signed-out shoppers
 *   onNavigate — called with the chosen path after a click/Enter; the header
 *                uses it to close the panel and clear the box
 *   className  — positioning from the caller (each input anchors differently)
 */
const SearchSuggestions = forwardRef(function SearchSuggestions(
  { query, open, isSignedIn = false, onNavigate, className },
  ref,
) {
  const { data, term, isPending, isTooShort } = useGlobalSearch(query, {
    limit: 6,
    enabled: open,
  });
  const [activeIndex, setActiveIndex] = useState(-1);

  // Memoized on `data` rather than read inline: `?? []` mints a fresh array on
  // every render while no results are loaded, which would re-run the `flat`
  // memo and re-create the imperative handle on each keystroke.
  const products = useMemo(() => data?.products ?? [], [data]);
  const categories = useMemo(() => data?.categories ?? [], [data]);
  const productTotal = data?.product_total ?? 0;
  const pages = useMemo(
    () => matchPages(term, { isSignedIn, limit: 4 }),
    [term, isSignedIn],
  );

  // One flat list in visual order — arrow keys walk this, so it must be built
  // from the same arrays the sections render, in the same order.
  const flat = useMemo(
    () => [
      ...products.map((p) => ({ kind: 'product', to: `/products/${p.id}`, key: `p${p.id}` })),
      ...categories.map((c) => ({
        kind: 'category',
        to: `/products?category_ids=${c.id}`,
        key: `c${c.id}`,
      })),
      ...pages.map((pg) => ({ kind: 'page', to: pg.to, key: `g${pg.to}` })),
    ],
    [products, categories, pages],
  );

  // Reset the highlight whenever the result set changes. Without this, holding
  // position at index 4 while a new (shorter) result list arrives would leave
  // Enter opening whatever happened to land there — a product the shopper never
  // looked at.
  useEffect(() => {
    setActiveIndex(-1);
  }, [term, flat.length]);

  useImperativeHandle(
    ref,
    () => ({
      moveDown: () => setActiveIndex((i) => (flat.length ? (i + 1) % flat.length : -1)),
      moveUp: () =>
        setActiveIndex((i) => (flat.length ? (i <= 0 ? flat.length - 1 : i - 1) : -1)),
      // Returns the path to open, or null when nothing is highlighted — the
      // header then falls through to its normal "submit the whole query"
      // behaviour instead of swallowing the Enter.
      openActive: () => (activeIndex >= 0 ? flat[activeIndex]?.to ?? null : null),
      reset: () => setActiveIndex(-1),
      hasActive: () => activeIndex >= 0,
    }),
    [flat, activeIndex],
  );

  if (!open || !query.trim()) return null;

  const isActive = (index) => index === activeIndex;
  const rowCls = (index) =>
    cn(
      'flex items-center gap-3 px-4 py-2.5 no-underline transition-colors',
      isActive(index) ? 'bg-wline/50' : 'hover:bg-wline/30',
    );
  const sectionLabel = 'px-4 pt-3 pb-1.5 text-[10.5px] uppercase tracking-[0.14em] text-wmuted';

  const panel = (children) => (
    <div
      className={cn(
        'absolute z-50 mt-2 w-full max-h-[70vh] overflow-y-auto overscroll-contain',
        'rounded-xl2 border border-wline bg-white shadow-xl',
        className,
      )}
      // Keep focus in the input on mousedown. Safari does not focus an <a> when
      // it is clicked, so without this the input's blur handler would close the
      // panel between mousedown and click and the suggestion would never open.
      // preventDefault here blocks the focus shift only — the click still fires.
      onMouseDown={(e) => e.preventDefault()}
      // Not a listbox/combobox: the panel contains links, and announcing them as
      // options would promise selection semantics the header does not implement.
      // The live region below is what tells a screen reader the count changed.
      role="region"
      aria-label="Search suggestions"
    >
      <p className="sr-only" role="status" aria-live="polite">
        {flat.length
          ? `${flat.length} suggestion${flat.length === 1 ? '' : 's'} for ${term}`
          : `No suggestions for ${term}`}
      </p>
      {children}
    </div>
  );

  if (isTooShort) {
    return panel(
      <p className="px-4 py-3 text-[12.5px] text-wmuted">
        Keep typing — at least {MIN_QUERY_LENGTH} characters.
      </p>,
    );
  }

  if (isPending) {
    return panel(
      <div className="px-4 py-3" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-3 py-2">
            <div className="h-10 w-10 shrink-0 animate-pulse rounded-lg bg-wline/60" />
            <div className="flex-1 space-y-1.5">
              <div className="h-3 w-2/3 animate-pulse rounded bg-wline/60" />
              <div className="h-2.5 w-1/3 animate-pulse rounded bg-wline/40" />
            </div>
          </div>
        ))}
      </div>,
    );
  }

  if (!flat.length) {
    return panel(
      <div className="px-4 py-4">
        <p className="m-0 text-[13px] text-wink">
          No matches for <span className="font-semibold">“{term}”</span>
        </p>
        <p className="m-0 mt-1 text-[12px] text-wmuted">
          Try a goal like “sleep” or “immunity”, or{' '}
          <Link
            to="/products"
            onClick={() => onNavigate?.('/products')}
            className="text-wgreen underline"
          >
            browse everything
          </Link>
          .
        </p>
      </div>,
    );
  }

  let index = -1;

  return panel(
    <>
      {products.length > 0 && (
        <>
          <p className={sectionLabel}>Products</p>
          {products.map((p) => {
            const i = ++index;
            const discounted =
              p.compare_at_price != null && Number(p.compare_at_price) > Number(p.price);
            return (
              <Link
                key={p.id}
                to={`/products/${p.id}`}
                onClick={() => onNavigate?.(`/products/${p.id}`)}
                onMouseEnter={() => setActiveIndex(i)}
                className={rowCls(i)}
              >
                <WImage
                  src={p.image_url}
                  alt={p.name}
                  className="h-10 w-10 shrink-0 rounded-lg"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-wink">{p.name}</span>
                  <span className="block truncate text-[11px] text-wmuted">
                    {p.category_name || p.flavour || p.brand || 'Wellvia'}
                    {p.stock <= 0 && ' · Out of stock'}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  <span className="block text-[12.5px] font-semibold text-wink">
                    {formatPrice(p.price)}
                  </span>
                  {discounted && (
                    <span className="block text-[10.5px] text-wmuted line-through">
                      {formatPrice(p.compare_at_price)}
                    </span>
                  )}
                </span>
              </Link>
            );
          })}
        </>
      )}

      {categories.length > 0 && (
        <>
          <p className={sectionLabel}>Categories</p>
          {categories.map((c) => {
            const i = ++index;
            const to = `/products?category_ids=${c.id}`;
            return (
              <Link
                key={c.id}
                to={to}
                onClick={() => onNavigate?.(to)}
                onMouseEnter={() => setActiveIndex(i)}
                className={rowCls(i)}
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-wline/40 text-wgreen">
                  <SearchIcon size={15} strokeWidth={1.8} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-wink">{c.name}</span>
                  <span className="block text-[11px] text-wmuted">
                    {c.product_count} product{c.product_count === 1 ? '' : 's'}
                  </span>
                </span>
              </Link>
            );
          })}
        </>
      )}

      {pages.length > 0 && (
        <>
          <p className={sectionLabel}>Pages</p>
          {pages.map((pg) => {
            const i = ++index;
            return (
              <Link
                key={pg.to}
                to={pg.to}
                onClick={() => onNavigate?.(pg.to)}
                onMouseEnter={() => setActiveIndex(i)}
                className={rowCls(i)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-wink">{pg.label}</span>
                  {pg.blurb && (
                    <span className="block truncate text-[11px] text-wmuted">{pg.blurb}</span>
                  )}
                </span>
              </Link>
            );
          })}
        </>
      )}

      {productTotal > products.length && (
        <Link
          to={`/products?q=${encodeURIComponent(term)}`}
          onClick={() => onNavigate?.(`/products?q=${encodeURIComponent(term)}`)}
          className="block border-t border-wline px-4 py-2.5 text-center text-[12.5px] font-semibold text-wgreen no-underline hover:bg-wline/30"
        >
          See all {productTotal} results for “{term}”
        </Link>
      )}
    </>,
  );
});

export default SearchSuggestions;
