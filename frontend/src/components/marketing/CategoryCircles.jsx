import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { useCategories } from '@/features/categories/hooks.js';
import { useProducts } from '@/features/products/hooks.js';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, staggerContainer } from '@/lib/motion.js';

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

function CategoryCircle({ category, imageUrl }) {
  const { bg, ring } = colorFor(category.slug);
  const [imgFailed, setImgFailed] = useState(false);
  const showImage = imageUrl && !imgFailed;

  return (
    <Link
      to={`/products?category=${encodeURIComponent(category.slug)}`}
      className="group flex flex-col items-center gap-2 focus-visible:outline-none"
      aria-label={`Browse ${category.name}`}
    >
      <span
        className={cn(
          'relative grid size-16 shrink-0 place-items-center overflow-hidden rounded-full',
          'ring-1',
          'transition-[transform,box-shadow] duration-200 ease-out',
          'group-hover:-translate-y-1 group-hover:shadow-md',
          'group-focus-visible:focus-ring',
          bg,
          ring,
        )}
      >
        {showImage ? (
          <img
            src={imageUrl}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setImgFailed(true)}
            className="size-full object-cover"
            aria-hidden="true"
          />
        ) : (
          <span className="text-lg font-semibold uppercase text-ink-secondary">
            {category.name.trim().charAt(0)}
          </span>
        )}
      </span>
      <span className="max-w-[4.5rem] truncate text-center text-[11px] font-medium text-ink-secondary transition-colors group-hover:text-ink-primary">
        {category.name}
      </span>
    </Link>
  );
}

function CategoryCircleSkeleton() {
  return (
    <div className="flex flex-col items-center gap-2">
      <Skeleton className="size-16 rounded-full" />
      <Skeleton className="h-3 w-14 rounded-xs" />
    </div>
  );
}

/**
 * "Curated for You" — a row of circular category shortcuts in Flipkart style.
 * Renders nothing if categories are not loaded or the list is empty.
 */
export default function CategoryCircles() {
  const reduce = useReducedMotion();
  const { data: categories = [], isLoading: catsLoading } = useCategories();
  const { data: productsPage, isLoading: prodsLoading } = useProducts({ page: 1, page_size: 100 });

  const imageByCategoryId = useMemo(() => {
    const map = new Map();
    for (const p of productsPage?.items ?? []) {
      if (p.category_id && p.image_url && !map.has(p.category_id)) {
        map.set(p.category_id, p.image_url);
      }
    }
    return map;
  }, [productsPage]);

  const isLoading = catsLoading || prodsLoading;

  if (!isLoading && categories.length === 0) return null;

  const visible = categories.slice(0, 10);

  return (
    <section
      className="mx-auto mt-3 max-w-content px-4 sm:px-6"
      aria-labelledby="curated-heading"
    >
      <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
        {/* Header row */}
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3">
          <h2 id="curated-heading" className="text-sm font-bold text-ink-primary">
            Curated for You
          </h2>
          <Link
            to="/products"
            className="text-xs font-semibold text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
          >
            VIEW ALL ›
          </Link>
        </div>

        <div className="px-5 py-4">
          {isLoading ? (
            <div className="flex items-start justify-center gap-6 overflow-x-auto pb-1 [&::-webkit-scrollbar]:hidden">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="shrink-0">
                  <CategoryCircleSkeleton />
                </div>
              ))}
            </div>
          ) : (
            <motion.div
              className="flex flex-wrap items-start justify-center gap-5 overflow-x-auto pb-1 [&::-webkit-scrollbar]:hidden"
              variants={staggerContainer(0.05)}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.2 }}
              role="list"
            >
              {visible.map((category) => (
                <motion.div key={category.id} variants={fadeUp} role="listitem" className="shrink-0">
                  <CategoryCircle
                    category={category}
                    imageUrl={category.image_url || imageByCategoryId.get(category.id)}
                  />
                </motion.div>
              ))}
            </motion.div>
          )}
        </div>
      </div>
    </section>
  );
}
