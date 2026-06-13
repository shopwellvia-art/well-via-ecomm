import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AlertTriangle, Star } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import {
  useProduct,
  useRelatedProducts,
  useCoPurchasedProducts,
  useLikelyProducts,
} from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { stockLabel, formatPrice } from '@/lib/utils.js';
import { useTrackProductView } from '@/features/history/store.js';

import { FrequentlyBoughtTogether } from '@/features/products/components/FrequentlyBoughtTogether.jsx';
import { ProductRail } from '@/features/products/components/ProductRail.jsx';
import { BrowsingHistoryRail } from '@/features/history/BrowsingHistoryRail.jsx';
import { CustomerReviewsSection } from '@/features/reviews/CustomerReviewsSection.jsx';

// luxury/* — all still rendered; only page layout restructured
import { Reveal } from '@/features/products/components/luxury/luxe.jsx';
import { LuxuryGallery } from '@/features/products/components/luxury/LuxuryGallery.jsx';
import { LuxuryBuyPanel } from '@/features/products/components/luxury/LuxuryBuyPanel.jsx';
import { KeyFeatures } from '@/features/products/components/luxury/KeyFeatures.jsx';
import { TrustRow } from '@/features/products/components/luxury/TrustRow.jsx';
import { LifestyleBanner } from '@/features/products/components/luxury/LifestyleBanner.jsx';
import { CustomerSay } from '@/features/products/components/luxury/CustomerSay.jsx';
import { StickyBuyBar } from '@/features/products/components/luxury/StickyBuyBar.jsx';

import { OfferStrip } from '@/features/products/components/OfferStrip.jsx';
import { AboutThisItem } from '@/features/products/components/AboutThisItem.jsx';
import { SpecTable } from '@/features/products/components/SpecTable.jsx';

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

  const stock = stockLabel(product.stock);
  const price = Number(product.price) || 0;
  const compareAt = Number(product.compare_at_price) || 0;
  const hasDiscount = compareAt > price;
  const discountPct = hasDiscount ? Math.round((1 - price / compareAt) * 100) : 0;
  const ratingAvg = Number(product.rating_avg) || 0;
  const ratingCount = Number(product.rating_count) || 0;

  return (
    <Page>
      {/* Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: 'Home', to: '/' },
          { label: 'Shop', to: '/products' },
          ...(categoryName
            ? [{ label: categoryName, to: `/products?category_id=${product.category_id}` }]
            : []),
        ]}
        current={product.name}
        className="mb-4"
      />

      {/* ── HERO TWO-COLUMN: Flipkart / Amazon layout ──────────────────────────
          LEFT  (sticky): gallery + thumbnail rail  (desktop)
          RIGHT         : title / rating / price / buy panel / specs
          Mobile: stacked — gallery → info → buy panel
      ───────────────────────────────────────────────────────────────────────── */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)] xl:grid-cols-[460px_minmax(0,1fr)]">

        {/* ── LEFT: Image gallery (sticky on desktop) ── */}
        <section className="lg:sticky lg:top-20 lg:self-start">
          <LuxuryGallery product={product} />
        </section>

        {/* ── RIGHT: Product info + buy panel ── */}
        <section className="min-w-0">
          {/* Category breadcrumb */}
          {categoryName && (
            <Link
              to={`/products?category_id=${product.category_id}`}
              className="mb-1 block text-xs font-semibold uppercase tracking-wide text-accent hover:underline focus-visible:outline-none focus-visible:underline"
            >
              {categoryName}
            </Link>
          )}

          {/* Product title */}
          <h1 className="text-xl font-medium leading-snug text-ink-primary sm:text-[1.375rem]">
            {product.name}
          </h1>

          {/* Rating row — only when data exists */}
          {ratingCount > 0 && (
            <a
              href="#reviews"
              className="mt-2 inline-flex items-center gap-1.5 hover:underline focus-visible:outline-none"
            >
              <Badge tone="rating" className="inline-flex items-center gap-1 px-2 py-0.5 text-xs">
                {ratingAvg.toFixed(1)}
                <Star className="size-3 fill-current" aria-hidden="true" />
              </Badge>
              <span className="text-xs text-ink-tertiary">
                {ratingCount.toLocaleString()} rating{ratingCount === 1 ? '' : 's'}
              </span>
            </a>
          )}

          {/* Stock + SKU */}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone={stock.tone} className="text-[11px]">
              {stock.text}
            </Badge>
            <span className="text-xs text-ink-tertiary">SKU: {product.sku}</span>
          </div>

          {/* Divider */}
          <div className="my-4 border-t border-line-subtle" />

          {/* Price block */}
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="nums text-3xl font-semibold text-accent">
              {formatPrice(price)}
            </span>
            {hasDiscount && (
              <>
                <s className="nums text-base text-ink-tertiary" aria-label={`Was ${formatPrice(compareAt)}`}>
                  {formatPrice(compareAt)}
                </s>
                <span className="text-sm font-bold text-rating">{discountPct}% off</span>
              </>
            )}
          </div>
          <p className="mt-0.5 text-xs text-ink-tertiary">Inclusive of all taxes.</p>

          {/* Offer strip */}
          <OfferStrip />

          {/* Divider */}
          <div className="my-4 border-t border-line-subtle" />

          {/* Brief description */}
          {product.description && (
            <p className="text-sm leading-relaxed text-ink-secondary">
              {product.description.length > 300
                ? `${product.description.slice(0, 300)}…`
                : product.description}
            </p>
          )}

          {/* Buy panel — StickyBuyBar observes #pdp-buybox to know when to appear */}
          <div id="pdp-buybox" className="mt-5">
            <LuxuryBuyPanel product={product} />
          </div>

          {/* About this item */}
          <AboutThisItem product={product} categoryName={categoryName} />

          {/* Spec table */}
          <SpecTable product={product} categoryName={categoryName} />
        </section>
      </div>

      {/* ── BELOW-FOLD SECTIONS ── */}

      {/* Trust row */}
      <TrustRow />

      {/* Key features / highlights */}
      <KeyFeatures product={product} />

      {/* Lifestyle banner */}
      <LifestyleBanner product={product} />

      {/* Frequently bought together */}
      <Reveal className="mt-10">
        <FrequentlyBoughtTogether
          product={product}
          related={related}
          isLoading={relatedLoading}
        />
      </Reveal>

      {/* Customer reviews summary */}
      <CustomerSay product={product} />

      {/* You may also like rail */}
      <ProductRail
        title="You may also like"
        products={likely?.length ? likely : coPurchased}
        isLoading={likelyLoading && coPurchasedLoading}
      />

      {/* Recently viewed */}
      <BrowsingHistoryRail excludeId={product.id} title="Recently viewed" />

      {/* Full paginated reviews */}
      <CustomerReviewsSection product={product} />

      {/* Sticky bottom CTA bar — slides in once #pdp-buybox scrolls out of view */}
      <StickyBuyBar product={product} />
    </Page>
  );
}

function Loading() {
  return (
    <Page>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        {/* Gallery skeleton */}
        <div className="flex flex-col gap-2.5">
          <Skeleton className="aspect-square rounded-sm" />
          <div className="grid grid-cols-6 gap-1.5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="aspect-square rounded-sm" />
            ))}
          </div>
        </div>

        {/* Info skeleton */}
        <div className="flex flex-col gap-4">
          <Skeleton className="h-3 w-20" />
          <div>
            <Skeleton className="h-6 w-full" />
            <Skeleton className="mt-1.5 h-6 w-4/5" />
          </div>
          <Skeleton className="h-3 w-24" />
          <div className="flex items-baseline gap-3">
            <Skeleton className="h-9 w-28" />
            <Skeleton className="h-4 w-14" />
          </div>
          <Skeleton variant="text" lines={3} className="w-full" />
          <div className="mt-2 flex flex-col gap-2.5 rounded-sm border border-line-subtle p-4">
            <Skeleton className="h-3 w-40" />
            <Skeleton className="h-3 w-32" />
            <div className="h-px w-full bg-line-subtle" />
            <Skeleton className="h-12 w-full rounded-sm" />
            <Skeleton className="h-12 w-full rounded-sm" />
          </div>
        </div>
      </div>
    </Page>
  );
}

function NotFound() {
  return (
    <Page>
      <EmptyState
        icon={AlertTriangle}
        title="Product not found"
        description="This product may have been removed or never existed."
        action={
          <Link to="/products">
            <Button size="sm">Back to shop</Button>
          </Link>
        }
      />
    </Page>
  );
}
