import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useReducedMotion } from 'framer-motion';
import { useCategories } from '@/features/categories/hooks.js';
import { useProducts } from '@/features/products/hooks.js';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn, mediaUrl } from '@/lib/utils.js';

/*
 * Flipkart-style category strip — white bar with circular icon + label tiles.
 * Each tile links to /products?category=<slug>.
 */

// Finding 6: replaced hardcoded palette arrays with a single token-based
// accent tint pair — consistent with Flipkart design language and the token system.
const CIRCLE_BG   = 'bg-accent/8';
const CIRCLE_RING = 'ring-accent/20';

// Finding 4 + 5: added `py-1` for touch-target height; moved `focus-visible:focus-ring`
// onto the <Link> itself; removed group-focus-visible/tile:focus-ring from inner span.
// Finding 2: accepts `isHidden` prop and applies `tabIndex={-1}` to the <Link>
// so keyboard users cannot reach duplicate (aria-hidden) tiles.
function CategoryTile({ category, imageUrl, isHidden }) {
  return (
    <Link
      to={`/products?category=${encodeURIComponent(category.slug)}`}
      className="group/tile flex shrink-0 flex-col items-center gap-2 py-1 focus-visible:outline-none focus-visible:focus-ring"
      aria-label={`Browse ${category.name}`}
      tabIndex={isHidden ? -1 : undefined}
    >
      {/* Circle */}
      <span
        className={cn(
          'relative grid size-16 place-items-center overflow-hidden rounded-full',
          'ring-1',
          'transition-[transform,box-shadow] duration-200 ease-out',
          'group-hover/tile:-translate-y-1 group-hover/tile:shadow-md',
          CIRCLE_BG,
          CIRCLE_RING,
        )}
      >
        {imageUrl ? (
          <img
            src={imageUrl}
            alt=""
            loading="lazy"
            decoding="async"
            className="size-full object-cover transition-transform duration-200 group-hover/tile:scale-105"
            aria-hidden="true"
          />
        ) : (
          <span className="text-lg font-bold uppercase text-ink-secondary">
            {category.name.trim().charAt(0)}
          </span>
        )}
      </span>

      {/* Label */}
      <span className="max-w-[4.5rem] truncate text-center text-[11px] font-medium text-ink-secondary transition-colors group-hover/tile:text-ink-primary">
        {category.name}
      </span>
    </Link>
  );
}

// Skeleton tile — matches the real tile dimensions so layout does not shift.
function CategoryTileSkeleton() {
  return (
    <div className="flex shrink-0 flex-col items-center gap-2 py-1">
      <Skeleton className="size-16 rounded-full" />
      <Skeleton className="h-3 w-14 rounded-xs" />
    </div>
  );
}

export default function CategoryMarquee() {
  const reduce = useReducedMotion();
  // Finding 1 + 7: destructure isLoading so we can render a skeleton strip
  // instead of returning null — prevents layout shift on initial load.
  const { data: categories = [], isLoading } = useCategories();
  // Finding 3: reduced page_size from 100 → 20 to cut initial-load payload.
  const { data: productsPage } = useProducts({ page: 1, page_size: 20 });

  // Fallback image source: the first product seen per category. Used only when
  // a category has no image of its own.
  const imageByCategoryId = useMemo(() => {
    const map = new Map();
    for (const p of productsPage?.items ?? []) {
      if (p.category_id && p.image_url && !map.has(p.category_id)) {
        map.set(p.category_id, p.image_url);
      }
    }
    return map;
  }, [productsPage]);

  // Show skeleton strip while loading to avoid layout shift.
  if (isLoading) {
    return (
      <section
        className="mx-auto mt-3 max-w-content px-4 sm:px-6"
        aria-labelledby="category-strip-heading"
        aria-busy="true"
      >
        <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
          <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3">
            <h2 id="category-strip-heading" className="text-sm font-bold text-ink-primary">
              Shop by Category
            </h2>
          </div>
          <div className="flex items-start gap-6 overflow-hidden px-5 py-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <CategoryTileSkeleton key={i} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  if (!categories.length) return null;

  // Duplicate for seamless infinite loop
  const loop = [...categories, ...categories];

  return (
    <section
      className="mx-auto mt-3 max-w-content px-4 sm:px-6"
      aria-labelledby="category-strip-heading"
    >
      <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
        {/* Header row */}
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3">
          <h2 id="category-strip-heading" className="text-sm font-bold text-ink-primary">
            Shop by Category
          </h2>
          <Link
            to="/products"
            className="text-xs font-semibold text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
          >
            VIEW ALL ›
          </Link>
        </div>

        {/* Marquee track */}
        <div className="group relative overflow-hidden py-4">
          {/* Fade edges */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 z-10 w-12 bg-gradient-to-r from-bg-elevated to-transparent"
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 z-10 w-12 bg-gradient-to-l from-bg-elevated to-transparent"
          />

          {/* Finding 5: added aria-label to the list for AT context */}
          <ul
            className={cn(
              'flex w-max items-start gap-6 px-5 will-change-transform',
              !reduce && 'animate-marquee group-hover:[animation-play-state:paused]',
            )}
            role="list"
            aria-label="Product categories"
          >
            {loop.map((c, i) => {
              // Finding 2: duplicate tiles are aria-hidden at the <li> level;
              // isHidden passes through so CategoryTile sets tabIndex={-1} on
              // the <Link>, keeping them out of the keyboard tab order.
              const isDuplicate = i >= categories.length;
              return (
                <li
                  key={`${c.id}-${i}`}
                  aria-hidden={isDuplicate ? 'true' : undefined}
                  className="shrink-0"
                >
                  <CategoryTile
                    category={c}
                    imageUrl={mediaUrl(c.image_url) || mediaUrl(imageByCategoryId.get(c.id))}
                    isHidden={isDuplicate}
                  />
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </section>
  );
}
