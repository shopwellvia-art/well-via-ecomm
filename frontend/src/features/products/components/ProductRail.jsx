import { useRef } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { formatPrice, stockLabel } from '@/lib/utils.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Horizontally-scrolling product rail — Flipkart style.
 * White cards with flat border on grey bg. Native touch-swipe; arrow buttons on desktop.
 */
export function ProductRail({ title, products, isLoading }) {
  const scroller = useRef(null);

  function scrollBy(dir) {
    const el = scroller.current;
    if (!el) return;
    const step = el.firstElementChild?.getBoundingClientRect().width || 200;
    el.scrollBy({ left: dir * (step + 8), behavior: 'smooth' });
  }

  if (isLoading) {
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

  return (
    <section className="mt-10" aria-label={title}>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-base font-semibold text-ink-primary">{title}</h2>
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

function RailCard({ product }) {
  const stock = stockLabel(product.stock);
  return (
    <div className="w-40 shrink-0 snap-start">
      <Link
        to={`/products/${product.id}`}
        className="group block overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <div className="aspect-square overflow-hidden bg-bg-sunken">
          <div className="size-full transition-transform duration-300 group-hover:scale-[1.03]">
            <ProductMedia product={product} />
          </div>
        </div>
        <div className="flex flex-col gap-1 p-2.5">
          <p className="line-clamp-2 text-xs font-medium leading-snug text-ink-primary">
            {product.name}
          </p>
          <p className="nums text-sm font-semibold text-accent">
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
      className="grid size-8 place-items-center rounded-sm border border-line-subtle bg-bg-elevated text-ink-secondary transition-colors hover:border-line-strong hover:text-ink-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}
