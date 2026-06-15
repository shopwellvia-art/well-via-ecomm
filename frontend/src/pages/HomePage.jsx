import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import {
  PackageX,
  AlertCircle,
  Truck,
  ShieldCheck,
  RefreshCcw,
  TrendingUp,
  Gamepad2,
  Sparkles,
  ArrowRight,
} from 'lucide-react';
import Hero from '@/components/marketing/Hero.jsx';
import CategoryMarquee from '@/components/marketing/CategoryMarquee.jsx';
import DealsBanner from '@/components/marketing/DealsBanner.jsx';
import BestsellersSection from '@/components/marketing/BestsellersSection.jsx';
import CommunityBand from '@/components/marketing/CommunityBand.jsx';
import { Page } from '@/components/layout/Page.jsx';
import { ProductGrid } from '@/features/products/components/ProductGrid.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { Button, buttonVariants } from '@/components/ui/Button.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { cn } from '@/lib/utils.js';

/* ── Perk strip data ────────────────────────────────────────────────────────── */

const PERK_ITEMS = [
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
    icon: RefreshCcw,
    title: 'Easy returns',
    sub: '7-day hassle-free',
  },
  {
    icon: TrendingUp,
    title: 'Best prices',
    sub: 'Guaranteed or we match',
  },
];

/* ── Perk strip — 2-col mobile / 4-col desktop, accent icon + bold title ───── */

function PerkStrip() {
  return (
    <section
      aria-label="Why shop with us"
      className="mx-auto mt-3 max-w-content px-4 sm:px-6"
    >
      <ul
        className="grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-line-subtle bg-line-subtle sm:grid-cols-4"
        role="list"
      >
        {PERK_ITEMS.map(({ icon: Icon, title, sub }) => (
          <li
            key={title}
            className="flex items-center gap-3 bg-bg-elevated px-4 py-3.5"
          >
            <span className="shrink-0 text-accent">
              <Icon className="size-6" aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-semibold text-ink-primary">{title}</p>
              <p className="text-xs text-ink-tertiary">{sub}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── Promo banners — two gradient cards side by side ───────────────────────── */

function PromoBanners() {
  return (
    <section
      aria-label="Promotional offers"
      className="mx-auto mt-3 max-w-content px-4 sm:px-6"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Gaming Week */}
        <Link
          to="/products"
          className="group relative flex items-center overflow-hidden rounded-sm p-6 shadow-sm transition-shadow hover:shadow-lift"
          style={{ background: 'linear-gradient(110deg,#123a8f,#2874F0)' }}
          aria-label="Gaming Week — Consoles and gear up to 40% off"
        >
          <div className="relative z-10">
            <p className="text-xs font-semibold uppercase tracking-wide text-white/80">
              Sponsored
            </p>
            <h3 className="mt-1 text-xl font-bold text-white">Gaming Week</h3>
            <p className="mt-1 text-sm text-white/85">Consoles &amp; gear up to 40% off</p>
            <span className="mt-4 inline-flex items-center gap-1 text-sm font-bold text-[#FFE11B]">
              Shop now
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </span>
          </div>
          {/* Decorative glyph */}
          <div className="absolute right-4 top-1/2 -translate-y-1/2 text-white/15" aria-hidden="true">
            <Gamepad2 className="size-36" />
          </div>
        </Link>

        {/* Beauty Bestsellers */}
        <Link
          to="/products"
          className="group relative flex items-center overflow-hidden rounded-sm p-6 shadow-sm transition-shadow hover:shadow-lift"
          style={{ background: 'linear-gradient(110deg,#1f7a4d,#34c47a)' }}
          aria-label="Beauty Bestsellers — Skincare and fragrance from ₹699"
        >
          <div className="relative z-10">
            <p className="text-xs font-semibold uppercase tracking-wide text-white/80">
              New &amp; trending
            </p>
            <h3 className="mt-1 text-xl font-bold text-white">Beauty Bestsellers</h3>
            <p className="mt-1 text-sm text-white/85">Skincare &amp; fragrance from ₹699</p>
            <span className="mt-4 inline-flex items-center gap-1 text-sm font-bold text-white">
              Explore
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </span>
          </div>
          {/* Decorative glyph */}
          <div className="absolute right-4 top-1/2 -translate-y-1/2 text-white/15" aria-hidden="true">
            <Sparkles className="size-36" />
          </div>
        </Link>
      </div>
    </section>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────────── */

export default function HomePage() {
  const reduce = useReducedMotion();
  const { data, isLoading, isError, refetch } = useProducts({ page: 1, page_size: 10 });
  const addToCart = useAddToCart();
  const products = data?.items ?? [];

  return (
    <Page bleed>
      {/* 1) Hero carousel */}
      <Hero />

      {/* 2) Perk strip — 4-up icons: free delivery / secure checkout / easy returns / best prices */}
      <PerkStrip />

      {/* 3) Category marquee — Shop by Category */}
      <CategoryMarquee />

      {/* 4) Deals of the Day — orange header + countdown + horizontal deal rail */}
      <DealsBanner />

      {/* 5) Bestsellers rail */}
      <BestsellersSection />

      {/* 6) Promo banners — Gaming Week + Beauty Bestsellers */}
      <PromoBanners />

      {/* 7) Recommended for you — full product grid */}
      <section className="mx-auto mt-3 max-w-content px-4 sm:px-6">
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 16 }}
          whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.1 }}
          transition={{ duration: 0.4 }}
          className="overflow-hidden rounded-sm bg-bg-elevated p-5 shadow-sm"
        >
          {/* Header */}
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-bold text-ink-primary">Recommended for you</h2>
            <Link
              to="/products"
              className="text-sm font-semibold text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
            >
              View all ›
            </Link>
          </div>

          {isError ? (
            <EmptyState
              icon={AlertCircle}
              iconTone="danger"
              title="Could not load products"
              description="There was a problem fetching the catalog. Please try again."
              size="sm"
              action={
                <Button size="sm" onClick={() => refetch()}>
                  Retry
                </Button>
              }
            />
          ) : !isLoading && products.length === 0 ? (
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
              skeletonCount={10}
              columns={5}
              onQuickAdd={(p) => addToCart.mutate({ productId: p.id })}
            />
          )}
        </motion.div>
      </section>

      {/* 8) Newsletter */}
      <CommunityBand />
    </Page>
  );
}
