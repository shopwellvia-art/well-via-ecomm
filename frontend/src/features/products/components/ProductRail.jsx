import { useRef } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';
import { Badge } from '@/components/storefront/ui/Badge.jsx';
import { formatPrice, stockLabel } from '@/lib/utils.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Horizontally-scrolling product rail — wellness palette.
 * Warm wcard cards with wline border on wpaper bg. Native touch-swipe; arrow buttons on desktop.
 *
 * `cardStyle` — when true, wraps the rail in a wcard rounded-xl2 card with a
 * header row (title + "View all ›" link) matching the mock's "You may also like"
 * section. Defaults to the plain section layout.
 */
export function ProductRail({ title, products, isLoading, cardStyle = false }) {
  const scroller = useRef(null);

  function scrollBy(dir) {
    const el = scroller.current;
    if (!el) return;
    const step = el.firstElementChild?.getBoundingClientRect().width || 200;
    el.scrollBy({ left: dir * (step + 8), behavior: 'smooth' });
  }

  if (isLoading) {
    if (cardStyle) {
      return (
        <section className="mt-3 overflow-hidden rounded-xl2 bg-wcard shadow-sm" aria-label={title}>
          <div className="flex items-center justify-between border-b border-wline px-5 py-4">
            <Skeleton className="h-5 w-36" />
            <Skeleton className="h-4 w-16" />
          </div>
          <div className="flex gap-3 overflow-hidden px-4 py-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="w-[200px] shrink-0">
                <Skeleton className="aspect-square rounded-lg" />
                <div className="mt-2 flex flex-col gap-1.5 p-1">
                  <Skeleton variant="text" className="h-3 w-full" />
                  <Skeleton variant="text" className="h-3 w-2/3" />
                  <Skeleton variant="text" className="h-4 w-14" />
                </div>
              </div>
            ))}
          </div>
        </section>
      );
    }
    return (
      <section className="mt-10" aria-label={title}>
        <div className="flex items-center justify-between gap-4 mb-3">
          <Skeleton className="h-5 w-36" />
          <div className="hidden gap-1 md:flex">
            <Skeleton className="size-8 rounded-sm" />
            <Skeleton className="size-8 rounded-sm" />
          </div>
        </div>
        <div className="flex gap-2 overflow-hidden">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="w-40 shrink-0">
              <Skeleton className="aspect-square rounded-sm" />
              <div className="mt-2 flex flex-col gap-1.5 p-1">
                <Skeleton variant="text" className="h-3 w-full" />
                <Skeleton variant="text" className="h-3 w-2/3" />
                <Skeleton variant="text" className="h-4 w-14" />
              </div>
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (!products || products.length === 0) return null;

  if (cardStyle) {
    return (
      <section className="mt-3 overflow-hidden rounded-xl2 bg-wcard shadow-sm" aria-label={title}>
        <div className="flex items-center justify-between border-b border-wline px-5 py-4">
          <h2 className="text-lg font-bold text-wink">{title}</h2>
          <Link
            to="/products"
            className="text-sm font-semibold text-wgreen hover:underline"
          >
            View all ›
          </Link>
        </div>
        <div className="relative px-4 py-4">
          <div
            ref={scroller}
            className="rail flex snap-x snap-mandatory gap-3 overflow-x-auto"
          >
            {products.map((p) => (
              <RailCard key={p.id} product={p} wide />
            ))}
          </div>
          {/* Arrow buttons — desktop only */}
          <button
            type="button"
            onClick={() => scrollBy(-1)}
            aria-label="Scroll left"
            className="absolute left-1.5 top-1/2 hidden size-9 -translate-y-1/2 place-items-center rounded-full border border-wline bg-wcard text-wink shadow-md transition hover:bg-wpaper md:grid"
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => scrollBy(1)}
            aria-label="Scroll right"
            className="absolute right-1.5 top-1/2 hidden size-9 -translate-y-1/2 place-items-center rounded-full border border-wline bg-wcard text-wink shadow-md transition hover:bg-wpaper md:grid"
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="mt-10" aria-label={title}>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-base font-semibold text-wink">{title}</h2>
        <div className="hidden gap-1 md:flex">
          <ArrowBtn onClick={() => scrollBy(-1)} dir="left" />
          <ArrowBtn onClick={() => scrollBy(1)} dir="right" />
        </div>
      </div>

      <div
        ref={scroller}
        className="flex snap-x snap-mandatory gap-2 overflow-x-auto pb-2 [scrollbar-width:thin]"
      >
        {products.map((p) => (
          <RailCard key={p.id} product={p} />
        ))}
      </div>
    </section>
  );
}

function RailCard({ product, wide = false }) {
  const stock = stockLabel(product.stock);
  return (
    <div className={wide ? 'w-[200px] shrink-0 snap-start' : 'w-40 shrink-0 snap-start'}>
      <Link
        to={`/products/${product.id}`}
        className="group block overflow-hidden rounded-xl2 border border-wline bg-wcard shadow-sm transition-shadow hover:shadow-md"
      >
        <div className="aspect-square overflow-hidden bg-wpaper">
          <div className="size-full transition-transform duration-300 group-hover:scale-[1.03]">
            <ProductMedia product={product} />
          </div>
        </div>
        <div className="flex flex-col gap-1 p-2.5">
          <p className="line-clamp-2 text-xs font-medium leading-snug text-wink">
            {product.name}
          </p>
          <p className="nums text-sm font-semibold text-wgreen">
            {formatPrice(product.price)}
          </p>
          <Badge tone={stock.tone} size="sm" className="self-start text-[10px]">
            {stock.text}
          </Badge>
        </div>
      </Link>
    </div>
  );
}

function ArrowBtn({ onClick, dir }) {
  const Icon = dir === 'left' ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={dir === 'left' ? 'Scroll left' : 'Scroll right'}
      className="grid size-8 place-items-center rounded-sm border border-wline bg-wcard text-wmuted transition-colors hover:border-wline hover:text-wink"
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}
