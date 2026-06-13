import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useReducedMotion } from 'framer-motion';
import { useCategories } from '@/features/categories/hooks.js';
import { useProducts } from '@/features/products/hooks.js';
import { cn } from '@/lib/utils.js';

/*
 * Flipkart-style category strip — white bar with circular icon + label tiles.
 * Each tile links to /products?category=<slug>.
 */

const BG_COLORS = [
  'bg-rose-50',
  'bg-indigo-50',
  'bg-emerald-50',
  'bg-fuchsia-50',
  'bg-amber-50',
  'bg-violet-50',
  'bg-teal-50',
  'bg-orange-50',
];

const RING_COLORS = [
  'ring-rose-200',
  'ring-indigo-200',
  'ring-emerald-200',
  'ring-fuchsia-200',
  'ring-amber-200',
  'ring-violet-200',
  'ring-teal-200',
  'ring-orange-200',
];

function colorFor(slug) {
  let h = 0;
  for (let i = 0; i < slug.length; i += 1) {
    h = (h * 31 + slug.charCodeAt(i)) >>> 0;
  }
  const idx = h % BG_COLORS.length;
  return { bg: BG_COLORS[idx], ring: RING_COLORS[idx] };
}

function CategoryTile({ category, imageUrl }) {
  const { bg, ring } = colorFor(category.slug);

  return (
    <Link
      to={`/products?category=${encodeURIComponent(category.slug)}`}
      className="group/tile flex shrink-0 flex-col items-center gap-2 focus-visible:outline-none"
      aria-label={`Browse ${category.name}`}
    >
      {/* Circle */}
      <span
        className={cn(
          'relative grid size-16 place-items-center overflow-hidden rounded-full',
          'ring-1',
          'transition-[transform,box-shadow] duration-200 ease-out',
          'group-hover/tile:-translate-y-1 group-hover/tile:shadow-md',
          'group-focus-visible/tile:focus-ring',
          bg,
          ring,
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

export default function CategoryMarquee() {
  const reduce = useReducedMotion();
  const { data: categories = [] } = useCategories();
  const { data: productsPage } = useProducts({ page: 1, page_size: 100 });

  const imageByCategoryId = useMemo(() => {
    const map = new Map();
    for (const p of productsPage?.items ?? []) {
      if (p.category_id && p.image_url && !map.has(p.category_id)) {
        map.set(p.category_id, p.image_url);
      }
    }
    return map;
  }, [productsPage]);

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

          <ul
            className={cn(
              'flex w-max items-start gap-6 px-5 will-change-transform',
              !reduce && 'animate-marquee group-hover:[animation-play-state:paused]',
            )}
            role="list"
          >
            {loop.map((c, i) => (
              <li
                key={`${c.id}-${i}`}
                aria-hidden={i >= categories.length ? 'true' : undefined}
                className="shrink-0"
              >
                <CategoryTile
                  category={c}
                  imageUrl={imageByCategoryId.get(c.id)}
                />
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
