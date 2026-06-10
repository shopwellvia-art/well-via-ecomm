import { useRef } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { formatPrice, stockLabel } from '@/lib/utils.js';
import { hoverLift, tapPress } from '@/lib/motion.js';
import { ProductMedia } from './ProductMedia.jsx';

/**
 * Horizontally-scrolling product rail with animated arrow controls on >=md.
 * Touch users can swipe natively. Cards have Framer Motion lift + press.
 */
export function ProductRail({ title, products, isLoading }) {
  const scroller = useRef(null);

  function scrollBy(dir) {
    const el = scroller.current;
    if (!el) return;
    const step = el.firstElementChild?.getBoundingClientRect().width || 200;
    el.scrollBy({ left: dir * (step + 12), behavior: 'smooth' });
  }

  if (isLoading) {
    return (
      <section className="mt-14" aria-label={title}>
        <div className="flex items-center justify-between gap-4">
          <Skeleton className="h-6 w-36" />
          <div className="hidden gap-1.5 md:flex">
            <Skeleton variant="circle" className="size-9 rounded-full" />
            <Skeleton variant="circle" className="size-9 rounded-full" />
          </div>
        </div>
        <div className="mt-4 flex gap-3 overflow-hidden">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="w-44 shrink-0">
              <Skeleton className="aspect-square rounded-md" />
              <div className="mt-2 flex flex-col gap-1.5 p-1">
                <Skeleton variant="text" className="h-3 w-full" />
                <Skeleton variant="text" className="h-3 w-2/3" />
                <Skeleton variant="text" className="h-4 w-16" />
              </div>
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (!products || products.length === 0) return null;

  return (
    <section className="mt-14" aria-label={title}>
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-h3 tracking-tight text-ink-primary">{title}</h2>
        <div className="hidden gap-1.5 md:flex">
          <ArrowBtn onClick={() => scrollBy(-1)} dir="left" />
          <ArrowBtn onClick={() => scrollBy(1)} dir="right" />
        </div>
      </div>

      <div
        ref={scroller}
        className="mt-4 flex snap-x snap-mandatory gap-3 overflow-x-auto pb-3 [scrollbar-width:thin]"
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
    <motion.div
      whileHover={hoverLift}
      whileTap={tapPress}
      className="w-44 shrink-0 snap-start"
    >
      <Link
        to={`/products/${product.id}`}
        className="group block overflow-hidden rounded-md border border-line-subtle bg-bg-elevated shadow-sm transition-shadow hover:shadow-md focus-visible:focus-ring"
      >
        <div className="aspect-square overflow-hidden">
          <div className="size-full transition-transform duration-300 group-hover:scale-[1.04]">
            <ProductMedia product={product} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5 p-3">
          <p className="line-clamp-2 text-xs font-semibold leading-snug text-ink-primary">
            {product.name}
          </p>
          <p className="nums text-sm font-semibold text-ink-primary">
            {formatPrice(product.price)}
          </p>
          <Badge tone={stock.tone} size="sm" className="self-start text-[10px]">
            {stock.text}
          </Badge>
        </div>
      </Link>
    </motion.div>
  );
}

function ArrowBtn({ onClick, dir }) {
  const Icon = dir === 'left' ? ChevronLeft : ChevronRight;
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.92 }}
      onClick={onClick}
      aria-label={dir === 'left' ? 'Scroll left' : 'Scroll right'}
      className="grid size-9 place-items-center rounded-full border border-line-subtle bg-bg-elevated text-ink-secondary transition-colors hover:border-line-strong hover:text-ink-primary focus-visible:focus-ring"
    >
      <Icon className="size-4" aria-hidden="true" />
    </motion.button>
  );
}
