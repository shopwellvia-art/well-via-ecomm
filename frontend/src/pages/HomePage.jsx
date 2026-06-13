import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { PackageX, Truck, ShieldCheck, HeartHandshake, TrendingUp } from 'lucide-react';
import Hero from '@/components/marketing/Hero.jsx';
import CategoryMarquee from '@/components/marketing/CategoryMarquee.jsx';
import DealsBanner from '@/components/marketing/DealsBanner.jsx';
import BestsellersSection from '@/components/marketing/BestsellersSection.jsx';
import CommunityBand from '@/components/marketing/CommunityBand.jsx';
import { Page } from '@/components/layout/Page.jsx';
import { ProductGrid } from '@/features/products/components/ProductGrid.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { buttonVariants } from '@/components/ui/Button.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { cn } from '@/lib/utils.js';

/* ── Trust strip data ──────────────────────────────────────────────────────── */

const TRUST_ITEMS = [
  {
    icon: Truck,
    title: 'Free delivery',
    sub: 'On orders over ₹499',
  },
  {
    icon: ShieldCheck,
    title: 'Secure checkout',
    sub: '256-bit SSL encryption',
  },
  {
    icon: HeartHandshake,
    title: 'Easy returns',
    sub: '30-day hassle-free returns',
  },
  {
    icon: TrendingUp,
    title: 'Best prices',
    sub: 'Guaranteed or we match it',
  },
];

/* ── Trust strip — thin white icon+label bar ────────────────────────────────── */

function TrustStrip() {
  return (
    <section
      aria-label="Why shop with us"
      className="mx-auto mt-3 max-w-content px-4 sm:px-6"
    >
      <ul
        className="grid grid-cols-2 divide-x divide-line-subtle overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm sm:grid-cols-4"
        role="list"
      >
        {TRUST_ITEMS.map(({ icon: Icon, title, sub }) => (
          <li
            key={title}
            className="flex items-center gap-2.5 px-4 py-3"
          >
            <span className="grid size-8 shrink-0 place-items-center rounded-xs bg-accent/10 text-accent">
              <Icon className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-ink-primary leading-snug">
                {title}
              </p>
              <p className="truncate text-[11px] text-ink-tertiary leading-snug">{sub}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── Section header — Flipkart-style bold left title + VIEW ALL right ───────── */

function SectionHeader({ heading, viewAllTo, viewAllLabel = 'VIEW ALL' }) {
  return (
    <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3">
      <h2 className="text-sm font-bold text-ink-primary">{heading}</h2>
      {viewAllTo && (
        <Link
          to={viewAllTo}
          className="text-xs font-semibold text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
        >
          {viewAllLabel} ›
        </Link>
      )}
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────────── */

export default function HomePage() {
  const reduce = useReducedMotion();
  const { data, isLoading } = useProducts({ page: 1, page_size: 8 });
  const addToCart = useAddToCart();
  const products = data?.items ?? [];

  return (
    <Page bleed>
      {/* A) Hero carousel — full-width banner with dots + arrows */}
      <Hero />

      {/* B) Category strip — circular icons + labels */}
      <CategoryMarquee />

      {/* C) Trust strip — thin icon+label bar */}
      <TrustStrip />

      {/* D) Deals of the Day — orange header + horizontal product rail */}
      <DealsBanner />

      {/* E) Bestsellers rail */}
      <BestsellersSection />

      {/* F) Featured products grid */}
      <section className="mx-auto mt-3 max-w-content px-4 sm:px-6">
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 16 }}
          whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.1 }}
          transition={{ duration: 0.4 }}
          className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm"
        >
          <SectionHeader
            heading="Featured Products"
            viewAllTo="/products"
            viewAllLabel="VIEW ALL"
          />

          <div className="px-4 pb-5 pt-4">
            {!isLoading && products.length === 0 ? (
              <EmptyState
                icon={PackageX}
                title="No products yet"
                description="The catalog is being stocked. Check back shortly."
                size="sm"
                action={
                  <Link to="/products" className={cn(buttonVariants({ variant: 'primary', size: 'sm' }))}>
                    Browse the shop
                  </Link>
                }
              />
            ) : (
              <ProductGrid
                products={products}
                loading={isLoading}
                onQuickAdd={(p) => addToCart.mutate({ productId: p.id })}
              />
            )}
          </div>
        </motion.div>
      </section>

      {/* G) Newsletter band */}
      <CommunityBand />
    </Page>
  );
}
