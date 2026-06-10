import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { useCategories } from '@/features/categories/hooks.js';
import { useProducts } from '@/features/products/hooks.js';
import { cn } from '@/lib/utils.js';
import { fadeUp } from '@/lib/motion.js';

/*
 * Maps a deterministic gradient per category slug.
 * Uses pairs that look premium in both dark and light themes.
 */
const GRADIENT_PAIRS = [
  { from: 'from-rose-500/80', to: 'to-orange-400/80' },
  { from: 'from-indigo-500/80', to: 'to-sky-400/80' },
  { from: 'from-emerald-500/80', to: 'to-teal-400/80' },
  { from: 'from-fuchsia-500/80', to: 'to-pink-400/80' },
  { from: 'from-amber-500/80', to: 'to-yellow-400/80' },
  { from: 'from-violet-500/80', to: 'to-indigo-400/80' },
  { from: 'from-teal-500/80', to: 'to-cyan-400/80' },
  { from: 'from-orange-500/80', to: 'to-rose-400/80' },
];

function gradientFor(slug) {
  let h = 0;
  for (let i = 0; i < slug.length; i += 1) {
    h = (h * 31 + slug.charCodeAt(i)) >>> 0;
  }
  return GRADIENT_PAIRS[h % GRADIENT_PAIRS.length];
}

function CategoryTile({ category, imageUrl, reduce }) {
  const { from, to } = gradientFor(category.slug);

  return (
    <Link
      to={`/products?category=${encodeURIComponent(category.slug)}`}
      className="group/tile flex shrink-0 flex-col items-center gap-3 focus-visible:outline-none"
      aria-label={`Browse ${category.name}`}
    >
      {/* Circle image / gradient fallback */}
      <span
        className={cn(
          'relative grid size-[5.5rem] place-items-center overflow-hidden rounded-full',
          'border border-line-subtle bg-gradient-to-br shadow-sm',
          'transition-[transform,box-shadow] duration-300 ease-out',
          'group-hover/tile:-translate-y-1.5 group-hover/tile:shadow-md',
          'group-focus-visible/tile:focus-ring',
          from,
          to,
        )}
      >
        {imageUrl ? (
          <img
            src={imageUrl}
            alt=""
            loading="lazy"
            decoding="async"
            className="size-full object-cover transition-transform duration-300 group-hover/tile:scale-105"
            aria-hidden="true"
          />
        ) : (
          <span className="text-[1.375rem] font-bold uppercase text-white/90 drop-shadow">
            {category.name.trim().charAt(0)}
          </span>
        )}

        {/* Subtle inner highlight ring */}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-full ring-1 ring-inset ring-white/20"
        />
      </span>

      {/* Label */}
      <span className="max-w-[6.5rem] truncate text-center text-xs font-medium text-ink-secondary transition-colors group-hover/tile:text-ink-primary">
        {category.name}
      </span>
    </Link>
  );
}

export default function CategoryMarquee() {
  const reduce = useReducedMotion();
  const { data: categories = [] } = useCategories();
  // Shared React Query cache with HomePage — no extra network request.
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

  // Duplicate the list for a seamless infinite loop at -50% translate.
  const loop = [...categories, ...categories];

  return (
    <section
      className="mx-auto mt-16 max-w-content sm:mt-20"
      aria-labelledby="curated-heading"
    >
      {/* Section header */}
      <motion.header
        className="px-4 text-center sm:px-6"
        variants={fadeUp}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.6 }}
      >
        <h2 id="curated-heading" className="text-h2 tracking-tight text-ink-primary">
          Curated for You
        </h2>
        <p className="mx-auto mt-2 max-w-sm text-sm text-ink-secondary">
          Discover collections shaped by what you love.
        </p>
      </motion.header>

      {/* Marquee track */}
      <div className="group relative mt-8 overflow-hidden">
        {/* Fade edges — use bg-bg-base so both themes look right */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 z-10 w-16 bg-gradient-to-r from-bg-base to-transparent sm:w-28"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 z-10 w-16 bg-gradient-to-l from-bg-base to-transparent sm:w-28"
        />

        <ul
          className={cn(
            'flex w-max items-start gap-7 px-6 py-3 will-change-transform sm:gap-10',
            !reduce && 'animate-marquee group-hover:[animation-play-state:paused]',
          )}
          role="list"
        >
          {loop.map((c, i) => (
            <li
              key={`${c.id}-${i}`}
              aria-hidden={i >= categories.length ? 'true' : undefined}
            >
              <CategoryTile
                category={c}
                imageUrl={imageByCategoryId.get(c.id)}
                reduce={reduce}
              />
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
