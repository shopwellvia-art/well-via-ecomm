import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowRight, PackageX, TrendingUp, ShieldCheck, Truck, HeartHandshake } from 'lucide-react';
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
import { fadeUp, staggerContainer } from '@/lib/motion.js';

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

/* ── Trust strip ────────────────────────────────────────────────────────────── */

function TrustStrip() {
  return (
    <section
      aria-label="Why shop with us"
      className="mx-auto mt-16 max-w-content px-4 sm:px-6"
    >
      <motion.ul
        variants={staggerContainer(0.06)}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.3 }}
        className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4"
        role="list"
      >
        {TRUST_ITEMS.map(({ icon: Icon, title, sub }) => (
          <motion.li
            key={title}
            variants={fadeUp}
            className="flex items-center gap-3 rounded-md border border-line-subtle bg-bg-elevated p-4 shadow-sm"
          >
            <span className="grid size-10 shrink-0 place-items-center rounded-md bg-accent-soft text-accent">
              <Icon className="size-5" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-ink-primary leading-snug">
                {title}
              </p>
              <p className="truncate text-xs text-ink-tertiary mt-0.5 leading-snug">{sub}</p>
            </div>
          </motion.li>
        ))}
      </motion.ul>
    </section>
  );
}

/* ── Section header ─────────────────────────────────────────────────────────── */

function SectionHeader({ heading, sub, viewAllTo, viewAllLabel = 'View all' }) {
  return (
    <motion.header
      className="flex items-end justify-between gap-4"
      variants={fadeUp}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount: 0.5 }}
    >
      <div>
        <h2 className="text-h2 tracking-tight text-ink-primary">{heading}</h2>
        {sub && (
          <p className="mt-1 text-sm text-ink-secondary">{sub}</p>
        )}
      </div>
      {viewAllTo && (
        <Link
          to={viewAllTo}
          className={cn(
            buttonVariants({ variant: 'ghost', size: 'sm' }),
            'shrink-0 gap-1',
          )}
        >
          {viewAllLabel}
          <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      )}
    </motion.header>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────────── */

export default function HomePage() {
  const { data, isLoading } = useProducts({ page: 1, page_size: 8 });
  const addToCart = useAddToCart();
  const products = data?.items ?? [];

  return (
    <Page bleed>
      {/* A) Hero carousel — product showcase + mega-sale countdown */}
      <Hero />

      {/* B) Trust strip — beneath the fold on mobile, immediately after hero */}
      <TrustStrip />

      {/* C) Category marquee — "Curated for You" scrolling carousel */}
      <CategoryMarquee />

      {/* D) Deals banner — biggest sale of the season */}
      <DealsBanner />

      {/* E) Bestsellers rail */}
      <BestsellersSection />

      {/* F) Featured products grid */}
      <section className="mx-auto mt-20 max-w-content px-4 sm:px-6">
        <SectionHeader
          heading="Featured"
          sub="Hand-picked for you this week."
          viewAllTo="/products"
          viewAllLabel="View all"
        />

        <div className="mt-8">
          {!isLoading && products.length === 0 ? (
            <EmptyState
              icon={PackageX}
              title="No products yet"
              description="The catalog is being stocked. Check back shortly."
              size="sm"
              action={
                <Link to="/products" className={cn(buttonVariants({ size: 'sm' }))}>
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
      </section>

      {/* G) Join the community — newsletter band */}
      <CommunityBand />
    </Page>
  );
}
