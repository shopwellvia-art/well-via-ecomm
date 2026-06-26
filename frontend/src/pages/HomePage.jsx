import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { useProducts, useBestsellers } from '@/features/products/hooks.js';
import HeroSection from '@/components/storefront/HeroSection';
import ProductGrid from '@/components/storefront/ProductGrid';
import WImage from '@/components/storefront/WImage';
import { Check, Stars } from '@/components/storefront/Icons';

/* ── Static content ─────────────────────────────────────────────────────────── */

const STORY_POINTS = [
  'Science-led formulations',
  'Designed for daily use',
  'Made for modern lifestyles',
];

const TRUST_STATS = [
  { value: '12.4k+', label: 'Five-star reviews' },
  { value: '100%',   label: 'Clean ingredients' },
  { value: 'GMP',    label: 'Certified manufacturing' },
  { value: 'FSSAI',  label: 'Approved & compliant' },
];

const REVIEWS = [
  {
    quote: 'My cycles are regular and my skin has never looked better. A gentle ritual that works.',
    name: 'Aditi K.',
    role: 'Verified Buyer',
  },
  {
    quote: 'Finally a supplement that feels luxurious, not clinical. The taste is genuinely lovely.',
    name: 'Sara M.',
    role: 'Verified Buyer',
  },
  {
    quote: 'Two months in and my energy through the day is steadier. Beautifully simple to keep up.',
    name: 'Priya R.',
    role: 'Verified Buyer',
  },
];

/* ── Loading skeleton for product grid ─────────────────────────────────────── */

function ProductSkeleton({ count = 4 }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3.5 lg:gap-[22px]">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="bg-wcard border border-wline rounded-xl2 overflow-hidden animate-pulse"
        >
          <div className="w-full h-[200px] bg-wcanvas" />
          <div className="p-[18px] space-y-3">
            <div className="h-2.5 bg-wcanvas rounded w-3/4" />
            <div className="h-5 bg-wcanvas rounded w-full" />
            <div className="h-2.5 bg-wcanvas rounded w-1/2" />
            <div className="mt-4 h-10 bg-wcanvas rounded-full w-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Inline error state ─────────────────────────────────────────────────────── */

function InlineError({ onRetry }) {
  return (
    <div className="py-16 text-center">
      <p className="text-wmuted mb-5 text-[15px]">
        Could not load products. Please try again.
      </p>
      <button
        onClick={onRetry}
        className="bg-wgreen text-white rounded-full px-8 py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors border-0 cursor-pointer"
      >
        Retry
      </button>
    </div>
  );
}

/* ── Section heading ─────────────────────────────────────────────────────────── */

function SectionHeading({ eyebrow, title }) {
  return (
    <div className="text-center mb-7 lg:mb-11">
      <div className="text-[11px] tracking-[0.24em] uppercase text-wgold mb-3.5">
        {eyebrow}
      </div>
      <h2 className="font-wserif font-medium text-[clamp(30px,3.6vw,46px)] m-0 text-wink">
        {title}
      </h2>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────────── */

export default function HomePage() {
  const reduce = useReducedMotion();

  /* Real data — PRESERVE these hooks */
  const {
    data: bestsellersData,
    isLoading: bestsellersLoading,
  } = useBestsellers(8);
  const bestsellers = bestsellersData ?? [];

  const {
    data: productsData,
    isLoading: productsLoading,
    isError: productsError,
    refetch,
  } = useProducts({ page: 1, page_size: 8 });
  const products = productsData?.items ?? [];

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
      className="bg-wcanvas"
    >
      {/* 1. Split hero */}
      <HeroSection />

      {/* 2. Featured Rituals — bestsellers (up to 4) */}
      <section className="px-5 sm:px-10 lg:px-16 py-10 lg:py-[68px] bg-gradient-to-b from-transparent to-[rgba(216,208,196,0.18)]">
        <SectionHeading eyebrow="Curated for you" title="Your Daily Wellness Rituals" />
        <div className="max-w-[1140px] mx-auto">
          {bestsellersLoading ? (
            <ProductSkeleton count={4} />
          ) : (
            <ProductGrid products={bestsellers.slice(0, 4)} cols={4} />
          )}
        </div>
        <div className="text-center mt-[34px]">
          <Link
            to="/products"
            className="inline-block bg-transparent border border-wgreen text-wgreen no-underline rounded-full px-[34px] py-[13px] text-[13px] tracking-wide hover:bg-wgreen hover:text-white transition-colors"
          >
            View All Products
          </Link>
        </div>
      </section>

      {/* 3. Our Story — copy + checklist left / lifestyle image right */}
      <section className="grid md:grid-cols-2 gap-6 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 py-10 lg:py-[72px]">
        <div>
          <div className="text-[11px] tracking-[0.24em] uppercase text-wgold mb-4">
            Our Story
          </div>
          <h2 className="font-wserif font-medium text-[clamp(30px,3.8vw,48px)] leading-[1.08] m-0 mb-5 text-wink">
            Wellness that
            <br />
            fits into real life
          </h2>
          <p className="text-[15.5px] leading-[1.75] text-wmuted m-0 mb-4 font-light max-w-[460px]">
            At Wellvia, we believe nutrition should feel light, enjoyable, and
            trustworthy — never clinical or overwhelming. Every formulation is
            thoughtfully crafted with clean ingredients, transparent labels and
            science-led outcomes.
          </p>
          <div className="flex flex-col gap-3 mt-6">
            {STORY_POINTS.map((s) => (
              <div key={s} className="flex items-center gap-3 text-[14.5px] text-wink">
                <span className="w-[30px] h-[30px] rounded-full border border-wline flex items-center justify-center shrink-0">
                  <Check size={14} stroke="#183A2E" />
                </span>
                {s}
              </div>
            ))}
          </div>
        </div>
        {/* WImage handles null gracefully with a warm gradient placeholder */}
        <WImage
          src={null}
          alt="Wellvia wellness lifestyle"
          shape="rounded"
          className="w-full h-[clamp(340px,40vw,480px)]"
        />
      </section>

      {/* 4. Trust stats — green bar */}
      <section className="px-5 sm:px-10 lg:px-16 py-8 lg:py-[52px] bg-wgreen">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5 lg:gap-10 max-w-[1100px] mx-auto text-center">
          {TRUST_STATS.map((t) => (
            <div key={t.label}>
              <div className="font-wserif text-[clamp(34px,4vw,48px)] text-white leading-none mb-2">
                {t.value}
              </div>
              <div className="text-[12px] tracking-wide text-[#f3efe6]/70 uppercase">
                {t.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 5. Recommended for You — full catalog page 1 */}
      <section className="px-5 sm:px-10 lg:px-16 py-10 lg:py-[68px]">
        <SectionHeading eyebrow="Picked for you" title="Recommended for You" />
        <div className="max-w-[1140px] mx-auto">
          {productsLoading ? (
            <ProductSkeleton count={8} />
          ) : productsError ? (
            <InlineError onRetry={refetch} />
          ) : (
            <ProductGrid products={products} cols={4} />
          )}
        </div>
        {!productsLoading && !productsError && products.length > 0 && (
          <div className="text-center mt-[34px]">
            <Link
              to="/products"
              className="inline-block bg-transparent border border-wgreen text-wgreen no-underline rounded-full px-[34px] py-[13px] text-[13px] tracking-wide hover:bg-wgreen hover:text-white transition-colors"
            >
              Browse All Products
            </Link>
          </div>
        )}
      </section>

      {/* 6. Customer reviews — static testimonials */}
      <section className="px-5 sm:px-10 lg:px-16 py-10 lg:py-[68px] bg-wpaper">
        <SectionHeading eyebrow="Loved by thousands" title="Real Rituals, Real Results" />
        <div className="grid md:grid-cols-3 gap-[18px] max-w-[1100px] mx-auto">
          {REVIEWS.map((r, i) => (
            <div
              key={i}
              className="bg-wcard border border-wline rounded-xl2 p-6 flex flex-col gap-3.5"
            >
              <Stars />
              <p className="font-wserif text-[18px] leading-[1.5] text-wink m-0 flex-1 italic">
                &ldquo;{r.quote}&rdquo;
              </p>
              <div className="flex items-center gap-2.5">
                {/* Avatar initial fallback */}
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-wgold/25 via-wpaper to-wcanvas flex items-center justify-center shrink-0">
                  <span className="select-none text-[14px] font-semibold text-wink/50">
                    {r.name.charAt(0)}
                  </span>
                </div>
                <div className="text-[13px]">
                  <div className="text-wink">{r.name}</div>
                  <div className="text-wmuted text-[11.5px]">{r.role}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </motion.div>
  );
}
