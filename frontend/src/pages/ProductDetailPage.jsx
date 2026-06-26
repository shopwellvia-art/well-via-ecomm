import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Sparkles, Leaf, ShieldCheck, Award, Package, Truck, RotateCcw, Zap } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';
import {
  useProduct,
  useRelatedProducts,
  useCoPurchasedProducts,
  useLikelyProducts,
} from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { useProductReviews } from '@/features/reviews/hooks.js';
import { formatPrice, mediaUrl, cn } from '@/lib/utils.js';
import { useTrackProductView } from '@/features/history/store.js';

// Feature components — logic 100% preserved, only page chrome re-skinned
import { FrequentlyBoughtTogether } from '@/features/products/components/FrequentlyBoughtTogether.jsx';
import { BrowsingHistoryRail } from '@/features/history/BrowsingHistoryRail.jsx';
import { CustomerReviewsSection } from '@/features/reviews/CustomerReviewsSection.jsx';
import { Reveal } from '@/features/products/components/luxury/luxe.jsx';
import { LuxuryGallery } from '@/features/products/components/luxury/LuxuryGallery.jsx';
import { LuxuryBuyPanel } from '@/features/products/components/luxury/LuxuryBuyPanel.jsx';
import { CustomerSay } from '@/features/products/components/luxury/CustomerSay.jsx';
import { StickyBuyBar } from '@/features/products/components/luxury/StickyBuyBar.jsx';
import { LifestyleBanner } from '@/features/products/components/luxury/LifestyleBanner.jsx';
import { OfferStrip } from '@/features/products/components/OfferStrip.jsx';
import { AboutThisItem } from '@/features/products/components/AboutThisItem.jsx';
import { SpecTable } from '@/features/products/components/SpecTable.jsx';

// Wellness storefront components (Phase 1)
import ProductGrid from '@/components/storefront/ProductGrid.jsx';
import TrustBadges from '@/components/storefront/TrustBadges.jsx';
import { CheckCircle, LeafIcon, Stars } from '@/components/storefront/Icons.jsx';

// ── helpers ───────────────────────────────────────────────────────────────────

const BENEFIT_ICONS = [Sparkles, Leaf, ShieldCheck, Award, Package, Truck, RotateCcw, Zap];

/** Extract 2-4 prose highlights from the product description. */
function extractHighlights(description) {
  const raw = (description || '').trim();
  if (!raw) return [];
  return [
    ...new Set(
      raw
        .split(/(?<=[.!?])\s+|\n+/g)
        .map((s) => s.trim().replace(/\s+/g, ' '))
        .filter((s) => s.length >= 12 && s.length <= 140),
    ),
  ].slice(0, 4);
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function ProductDetailPage() {
  const { id } = useParams();
  const { data: product, isLoading, isError } = useProduct(id);
  const { data: related, isLoading: relatedLoading } = useRelatedProducts(id, 12);
  const { data: coPurchased, isLoading: coPurchasedLoading } = useCoPurchasedProducts(id, 12);
  const { data: likely, isLoading: likelyLoading } = useLikelyProducts(id, 12);
  const { data: categories } = useCategories();

  useTrackProductView(id);

  const categoryName = useMemo(() => {
    if (!product?.category_id || !categories) return null;
    return categories.find((c) => c.id === product.category_id)?.name || null;
  }, [product?.category_id, categories]);

  if (isLoading) return <Loading />;
  if (isError || !product) return <NotFound />;

  const price = Number(product.price) || 0;
  const compareAt = Number(product.compare_at_price) || 0;
  const hasDiscount = compareAt > price;
  const discountPct = hasDiscount ? Math.round((1 - price / compareAt) * 100) : 0;
  const ratingAvg = Number(product.rating_avg) || 0;
  const ratingCount = Number(product.rating_count) || 0;
  const outOfStock = product.stock <= 0;

  const highlights = extractHighlights(product.description);
  const relatedProducts = likely?.length ? likely : coPurchased;

  return (
    <Page>

      {/* ── BREADCRUMB ─────────────────────────────────────────────────────── */}
      <nav aria-label="Breadcrumb" className="mb-5">
        <ol className="flex flex-wrap items-center gap-x-1 text-[11.5px] text-wmuted">
          <li className="flex items-center gap-1">
            <Link to="/" className="hover:text-wgreen transition-colors">Home</Link>
            <span aria-hidden="true">/</span>
          </li>
          <li className="flex items-center gap-1">
            <Link to="/products" className="hover:text-wgreen transition-colors">Shop</Link>
            <span aria-hidden="true">/</span>
          </li>
          {categoryName && (
            <li className="flex items-center gap-1">
              <Link
                to={`/products?category_id=${product.category_id}`}
                className="hover:text-wgreen transition-colors"
              >
                {categoryName}
              </Link>
              <span aria-hidden="true">/</span>
            </li>
          )}
          <li className="text-wink font-medium max-w-[220px] truncate">{product.name}</li>
        </ol>
      </nav>

      {/* ── HERO CARD: 2-col gallery + info ────────────────────────────────── */}
      <div className="bg-wcard border border-wline rounded-xl2 overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,480px)_1fr]">

          {/* LEFT — gallery (real images[], thumbnails + main + hover-zoom) */}
          <div className="p-5 border-b border-wline lg:border-b-0 lg:border-r lg:sticky lg:top-[120px] lg:self-start">
            <LuxuryGallery product={product} />

            {/* Desktop CTA buttons below gallery — scroll to buybox */}
            <div className="mt-4 hidden grid-cols-2 gap-3 lg:grid">
              <button
                type="button"
                disabled={outOfStock}
                onClick={() =>
                  document
                    .getElementById('pdp-buybox')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                }
                className={cn(
                  'flex h-12 items-center justify-center gap-2 rounded-full bg-wgreen text-sm font-semibold text-white',
                  'shadow-[0_12px_28px_-12px_rgba(24,58,46,0.65)] transition-all hover:bg-wgreen-dark hover:-translate-y-0.5',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-wgreen',
                  'disabled:pointer-events-none disabled:opacity-50',
                )}
              >
                Add to cart
              </button>
              <button
                type="button"
                disabled={outOfStock}
                onClick={() =>
                  document
                    .getElementById('pdp-buybox')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                }
                className={cn(
                  'flex h-12 items-center justify-center gap-2 rounded-full border border-wgreen text-sm font-semibold text-wgreen',
                  'transition-all hover:bg-wgreen/5 hover:-translate-y-0.5',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-wgreen',
                  'disabled:pointer-events-none disabled:opacity-50',
                )}
              >
                Buy now
              </button>
            </div>
          </div>

          {/* RIGHT — info rail */}
          <div className="p-6 lg:p-10">

            {/* Gold eyebrow — category link */}
            {categoryName && (
              <Link
                to={`/products?category_id=${product.category_id}`}
                className="inline-block bg-wgold/15 text-wgold text-[10.5px] tracking-[0.16em] uppercase px-[13px] py-1.5 rounded-full mb-4 hover:bg-wgold/25 transition-colors"
              >
                {categoryName}
              </Link>
            )}

            {/* Product title */}
            <h1 className="font-wserif font-medium text-[clamp(28px,4vw,50px)] leading-[1.06] text-wink">
              {product.name}
            </h1>

            {/* Brand as italic serif sub-title */}
            {product.brand && (
              <p className="font-wserif italic text-[clamp(15px,1.8vw,21px)] text-wgold mt-1">
                {product.brand}
              </p>
            )}

            {/* Tag pills — stock + sale badge */}
            <div className="flex flex-wrap gap-2 mt-4">
              {outOfStock ? (
                <span className="inline-flex items-center gap-1.5 border border-red-300 text-red-500 rounded-full px-3.5 py-[7px] text-[12px]">
                  Out of stock
                </span>
              ) : product.stock <= 5 ? (
                <span className="inline-flex items-center gap-1.5 border border-wgold/40 text-wgold rounded-full px-3.5 py-[7px] text-[12px]">
                  <CheckCircle size={13} stroke="#B49A63" />
                  Only {product.stock} left
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 border border-wline text-wink rounded-full px-3.5 py-[7px] text-[12px]">
                  <CheckCircle size={13} stroke="#183A2E" />
                  In stock
                </span>
              )}
              {hasDiscount && (
                <span className="inline-flex items-center gap-1.5 border border-wgold/40 bg-wgold/10 text-wgold rounded-full px-3.5 py-[7px] text-[12px] font-medium">
                  {discountPct}% OFF
                </span>
              )}
            </div>

            {/* Rating row */}
            {ratingCount > 0 && (
              <div className="flex flex-wrap items-center gap-4 mt-4 text-[13px]">
                <a
                  href="#reviews"
                  className="flex items-center gap-1.5 no-underline hover:opacity-80 transition-opacity"
                  aria-label={`Rated ${ratingAvg.toFixed(1)} out of 5`}
                >
                  <Stars />
                  <b className="font-medium text-wink">{ratingAvg.toFixed(1)}</b>
                  <span className="text-wmuted">({ratingCount.toLocaleString()})</span>
                </a>
                <TrustBadges items={['Clinically Reviewed', 'FSSAI Compliant']} />
              </div>
            )}

            {/* Price block */}
            <div className="flex flex-wrap items-end gap-3 mt-5">
              <span className="text-[clamp(26px,3vw,34px)] font-bold text-wink leading-none">
                {formatPrice(price)}
              </span>
              {hasDiscount && (
                <>
                  <span
                    className="pb-1 text-base text-wmuted line-through"
                    aria-label={`Was ${formatPrice(compareAt)}`}
                  >
                    {formatPrice(compareAt)}
                  </span>
                  <span className="pb-1 text-base font-semibold text-wgold">
                    {discountPct}% off
                  </span>
                </>
              )}
            </div>
            <p className="text-[11px] text-wmuted mt-0.5">Inclusive of all taxes</p>

            {/* Product description (truncated preview) */}
            {product.description && (
              <p className="text-[15px] leading-[1.72] text-wmuted max-w-[480px] mt-4 font-light">
                {product.description.length > 300
                  ? product.description.slice(0, 300) + '…'
                  : product.description}
              </p>
            )}

            {/* Available offers */}
            <OfferStrip />

            {/* Buy panel — id="pdp-buybox" triggers StickyBuyBar IntersectionObserver */}
            <div id="pdp-buybox" className="mt-6">
              <LuxuryBuyPanel product={product} />
            </div>

            {/* About this item */}
            <AboutThisItem product={product} categoryName={categoryName} />

            {/* Spec table — "Product details" */}
            <SpecTable product={product} categoryName={categoryName} />
          </div>
        </div>
      </div>

      {/* ── BENEFITS GRID ─────────────────────────────────────────────────── */}
      {highlights.length >= 2 && (
        <Reveal className="mt-16">
          <section
            aria-labelledby="benefits-heading"
            className="py-9 lg:py-[60px] bg-gradient-to-b from-wcanvas/70 to-transparent rounded-xl2"
          >
            <p className="text-[11px] tracking-[0.24em] uppercase text-wgold text-center mb-2">
              What makes it special
            </p>
            <h2
              id="benefits-heading"
              className="font-wserif font-medium text-[clamp(26px,3vw,40px)] text-center text-wink mb-8"
            >
              Natural Goodness, Every Day
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 lg:gap-[26px] max-w-[1080px] mx-auto">
              {highlights.map((text, i) => {
                const Icon = BENEFIT_ICONS[i % BENEFIT_ICONS.length];
                return (
                  <div
                    key={text.slice(0, 40)}
                    className="bg-wcard border border-wline rounded-xl2 px-[22px] py-[26px] text-center"
                  >
                    <div className="w-[52px] h-[52px] rounded-full bg-wgold/10 flex items-center justify-center mx-auto mb-4 text-wgreen">
                      <Icon size={22} />
                    </div>
                    <p className="text-[12.5px] leading-[1.6] text-wmuted font-light">{text}</p>
                  </div>
                );
              })}
            </div>
          </section>
        </Reveal>
      )}

      {/* ── LIFESTYLE BANNER (editorial image + "Explore collection" CTA) ─── */}
      <Reveal className="mt-10">
        <LifestyleBanner product={product} />
      </Reveal>

      {/* ── SPECS / INGREDIENTS — wellness editorial 2-col ─────────────────── */}
      {Array.isArray(product.specs) && product.specs.length > 0 && (
        <Reveal className="mt-10">
          <section className="grid md:grid-cols-2 gap-7 lg:gap-14 items-center py-10 lg:py-[68px]">
            {/* Product image on the left */}
            <div
              className="relative rounded-xl2 overflow-hidden bg-gradient-to-br from-wgold/20 via-wpaper to-wcanvas flex items-center justify-center"
              style={{ minHeight: 'clamp(280px,32vw,400px)' }}
            >
              {(product.image_url || product.images?.[0]?.url) ? (
                <img
                  src={mediaUrl(product.image_url || product.images?.[0]?.url)}
                  alt={product.name}
                  className="w-full h-full object-cover"
                  loading="lazy"
                  decoding="async"
                />
              ) : (
                <LeafIcon size={64} stroke="#B49A63" strokeWidth={1} />
              )}
            </div>

            {/* Spec list on the right */}
            <div>
              <p className="text-[11px] tracking-[0.24em] uppercase text-wgold mb-3.5">
                Product Details
              </p>
              <h2 className="font-wserif font-medium text-[clamp(26px,3vw,40px)] leading-[1.08] text-wink mb-[22px]">
                Quality Ingredients.<br />Trusted Formula.
              </h2>
              <div className="flex flex-col">
                {product.specs.map((spec, i) => (
                  <div
                    key={`${spec.key}-${i}`}
                    className="flex items-start gap-3.5 py-[15px] border-b border-wline"
                  >
                    <span className="w-8 h-8 rounded-full border border-wline flex items-center justify-center shrink-0 text-wgreen">
                      <LeafIcon size={15} />
                    </span>
                    <div>
                      <div className="text-[15px] text-wink mb-0.5">{spec.key}</div>
                      <div className="text-[12.5px] text-wmuted font-light">{spec.value}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </Reveal>
      )}

      {/* ── TRUST BADGES ──────────────────────────────────────────────────── */}
      <Reveal className="mt-10">
        <div className="bg-wcard border border-wline rounded-xl2 p-6 lg:p-8">
          <TrustBadges className="justify-center" />
        </div>
      </Reveal>

      {/* ── FREQUENTLY BOUGHT TOGETHER ────────────────────────────────────── */}
      <Reveal className="mt-10">
        <FrequentlyBoughtTogether
          product={product}
          related={related}
          isLoading={relatedLoading}
        />
      </Reveal>

      {/* ── GREEN REVIEW BAND (real top review, wellness style) ───────────── */}
      {ratingCount > 0 && (
        <Reveal className="mt-10">
          <ReviewBand product={product} />
        </Reveal>
      )}

      {/* ── YOU MAY ALSO LIKE — wellness ProductGrid ──────────────────────── */}
      {(relatedProducts?.length > 0 || likelyLoading || coPurchasedLoading) && (
        <Reveal className="mt-16">
          <section aria-labelledby="related-heading">
            <div className="flex items-center justify-between mb-6">
              <div>
                <p className="text-[11px] tracking-[0.24em] uppercase text-wgold mb-1">
                  Explore More
                </p>
                <h2
                  id="related-heading"
                  className="font-wserif font-medium text-[clamp(22px,3vw,36px)] text-wink"
                >
                  You May Also Like
                </h2>
              </div>
              <Link
                to="/products"
                className="text-sm text-wgreen hover:text-wgreen-dark transition-colors font-medium"
              >
                View all →
              </Link>
            </div>

            {(likelyLoading || coPurchasedLoading) ? (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3.5 lg:gap-[22px]">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i}>
                    <Skeleton className="aspect-square rounded-xl2" />
                    <div className="mt-3 flex flex-col gap-2 p-1">
                      <Skeleton className="h-3 w-full" />
                      <Skeleton className="h-4 w-20" />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <ProductGrid products={relatedProducts?.slice(0, 8)} cols={4} />
            )}
          </section>
        </Reveal>
      )}

      {/* ── RECENTLY VIEWED ───────────────────────────────────────────────── */}
      <BrowsingHistoryRail excludeId={product.id} title="Recently viewed" />

      {/* ── CUSTOMER REVIEWS SUMMARY (rating + histogram + preview cards) ─── */}
      <CustomerSay product={product} />

      {/* ── FULL PAGINATED REVIEWS ────────────────────────────────────────── */}
      <CustomerReviewsSection product={product} />

      {/* ── STICKY BUY BAR (mobile always-on / desktop slides in) ─────────── */}
      <StickyBuyBar product={product} />

      {/* Spacer for mobile sticky bar */}
      <div className="h-16 lg:hidden" aria-hidden="true" />
    </Page>
  );
}

// ── ReviewBand ────────────────────────────────────────────────────────────────

/**
 * Editorial green review band.
 * Pulls the top text review from the real useProductReviews hook (React Query
 * deduplicates this with the CustomerSay call — no double network request).
 * Falls back to the aggregate score when no text review exists yet.
 */
function ReviewBand({ product }) {
  const { data } = useProductReviews(product.id, { page: 1, page_size: 6, sort: 'top' });
  const ratingAvg = Number(product.rating_avg) || 0;
  const ratingCount = Number(product.rating_count) || 0;

  const topReview = (data?.items || []).find((r) => r.body);

  return (
    <section aria-label="Customer highlight" className="py-9 lg:py-[60px]">
      <div className="max-w-[760px] mx-auto bg-wgreen text-[#f3efe6] rounded-xl3 p-7 lg:p-[52px] text-center">
        <Stars className="text-[20px]" />
        {topReview ? (
          <>
            <p className="font-wserif text-[clamp(20px,2.6vw,28px)] leading-[1.4] my-[18px] italic">
              &ldquo;{topReview.body}&rdquo;
            </p>
            <div className="flex items-center justify-center gap-3">
              {/* Avatar initial */}
              <div
                className="w-[46px] h-[46px] rounded-full bg-white/20 flex items-center justify-center text-white font-semibold text-lg shrink-0"
                aria-hidden="true"
              >
                {(topReview.author_display || '?').charAt(0).toUpperCase()}
              </div>
              <div className="text-left text-[13.5px]">
                <div>{topReview.author_display}</div>
                <div className="text-[#f3efe6]/65 text-[12px]">
                  {topReview.is_verified_purchase ? 'Verified Buyer' : 'Customer'}
                </div>
              </div>
            </div>
          </>
        ) : (
          /* Fallback: aggregate score when no text review is available */
          <p className="font-wserif text-[clamp(20px,2.6vw,28px)] leading-[1.4] my-[18px]">
            {ratingAvg.toFixed(1)}&nbsp;out of 5&nbsp;&mdash;&nbsp;
            {ratingCount.toLocaleString()} happy customer{ratingCount === 1 ? '' : 's'}
          </p>
        )}
      </div>
    </section>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────────────

function Loading() {
  return (
    <Page>
      {/* Breadcrumb skeleton */}
      <div className="mb-5 flex items-center gap-2">
        <Skeleton className="h-3 w-12" />
        <Skeleton className="h-3 w-3" />
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-3 w-3" />
        <Skeleton className="h-3 w-32" />
      </div>

      <div className="bg-wcard border border-wline rounded-xl2 overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,480px)_1fr]">
          {/* Gallery skeleton */}
          <div className="p-5 border-b border-wline lg:border-b-0 lg:border-r">
            <Skeleton className="aspect-square rounded-xl2" />
            <div className="mt-3 grid grid-cols-5 gap-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="aspect-square rounded-lg" />
              ))}
            </div>
          </div>

          {/* Info skeleton */}
          <div className="p-6 lg:p-10 flex flex-col gap-4">
            <Skeleton className="h-6 w-28 rounded-full" />
            <div>
              <Skeleton className="h-10 w-full" />
              <Skeleton className="mt-2 h-6 w-3/4" />
            </div>
            <Skeleton className="h-4 w-36" />
            <div className="flex items-end gap-3">
              <Skeleton className="h-9 w-28" />
              <Skeleton className="h-4 w-16" />
            </div>
            <Skeleton className="h-16 w-full rounded-lg" />
            <div className="mt-2 flex flex-col gap-3 rounded-xl2 border border-wline p-4">
              <Skeleton className="h-3 w-48" />
              <Skeleton className="h-3 w-36" />
              <div className="h-px w-full bg-wline" />
              <Skeleton className="h-12 w-full rounded-full" />
              <Skeleton className="h-12 w-full rounded-full" />
            </div>
          </div>
        </div>
      </div>
    </Page>
  );
}

// ── Not found ─────────────────────────────────────────────────────────────────

function NotFound() {
  return (
    <Page>
      <div className="flex flex-col items-center gap-6 rounded-xl2 border border-wline bg-wcard py-20 text-center">
        <div className="w-16 h-16 rounded-full bg-wgold/10 flex items-center justify-center">
          <LeafIcon size={28} stroke="#B49A63" />
        </div>
        <div>
          <h2 className="font-wserif text-[28px] text-wink">Product not found</h2>
          <p className="text-wmuted mt-2 text-sm max-w-xs mx-auto">
            This product may have been removed or never existed.
          </p>
        </div>
        <Link
          to="/products"
          className="bg-wgreen text-white rounded-full px-8 py-3 text-sm font-semibold hover:bg-wgreen-dark transition-colors shadow-[0_10px_22px_-10px_rgba(24,58,46,0.55)]"
        >
          Back to shop
        </Link>
      </div>
    </Page>
  );
}
