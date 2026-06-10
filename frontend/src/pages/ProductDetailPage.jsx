import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AlertTriangle } from 'lucide-react';
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
import { stockLabel } from '@/lib/utils.js';
import { useTrackProductView } from '@/features/history/store.js';

import { FrequentlyBoughtTogether } from '@/features/products/components/FrequentlyBoughtTogether.jsx';
import { ProductRail } from '@/features/products/components/ProductRail.jsx';
import { BrowsingHistoryRail } from '@/features/history/BrowsingHistoryRail.jsx';
import { CustomerReviewsSection } from '@/features/reviews/CustomerReviewsSection.jsx';

import { Aura, Reveal } from '@/features/products/components/luxury/luxe.jsx';
import { LuxuryGallery } from '@/features/products/components/luxury/LuxuryGallery.jsx';
import { LuxuryBuyPanel } from '@/features/products/components/luxury/LuxuryBuyPanel.jsx';
import { KeyFeatures } from '@/features/products/components/luxury/KeyFeatures.jsx';
import { TrustRow } from '@/features/products/components/luxury/TrustRow.jsx';
import { LifestyleBanner } from '@/features/products/components/luxury/LifestyleBanner.jsx';
import { CustomerSay } from '@/features/products/components/luxury/CustomerSay.jsx';
import { StickyBuyBar } from '@/features/products/components/luxury/StickyBuyBar.jsx';

/**
 * Luxury product detail page — Apple / Aesop / Nothing inspired.
 *
 * Layout (>=lg):  | large frosted gallery | product info + glass buy panel |
 *   Below: trust strip → highlights → lifestyle banner → frequently bought
 *   together → recommendation rails → reviews. A scroll-activated buy bar keeps
 *   the CTA reachable throughout.
 *
 * The premium feel is layered with theme tokens + soft lavender ambient light,
 * so it reads as a flagship in BOTH light and dark mode. Every section is
 * data-honest: anything without backing data (video, 360, highlights) simply
 * doesn't render rather than showing placeholders.
 */
export default function ProductDetailPage() {
  const { id } = useParams();
  const { data: product, isLoading, isError } = useProduct(id);
  const { data: related, isLoading: relatedLoading } = useRelatedProducts(id, 12);
  const { data: coPurchased, isLoading: coPurchasedLoading } = useCoPurchasedProducts(
    id,
    12,
  );
  const { data: likely, isLoading: likelyLoading } = useLikelyProducts(id, 12);
  const { data: categories } = useCategories();

  // Record the view for the "Your browsing history" rail on subsequent visits.
  useTrackProductView(id);

  const categoryName = useMemo(() => {
    if (!product?.category_id || !categories) return null;
    return categories.find((c) => c.id === product.category_id)?.name || null;
  }, [product?.category_id, categories]);

  if (isLoading) return <Loading />;
  if (isError || !product) return <NotFound />;

  const stock = stockLabel(product.stock);

  return (
    <Page>
      <div className="relative isolate">
        <Aura />

        <Breadcrumbs
          items={[
            { label: 'Shop', to: '/products' },
            ...(categoryName
              ? [
                  {
                    label: categoryName,
                    to: `/products?category_id=${product.category_id}`,
                  },
                ]
              : []),
          ]}
          current={product.name}
        />

        {/* Hero: gallery | info + buy panel. Plain sections (no Reveal
            transform) so the sticky gallery isn't broken by an animated
            ancestor; the Page wrapper already provides the entrance. */}
        <div className="mt-6 grid gap-10 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-12">
          <section className="lg:sticky lg:top-24 lg:z-30 lg:self-start">
            <LuxuryGallery product={product} />
          </section>

          <section className="relative min-w-0 lg:z-10">
            {categoryName && (
              <Link
                to={`/products?category_id=${product.category_id}`}
                className="text-xs font-semibold uppercase tracking-[0.18em] text-accent hover:underline"
              >
                {categoryName}
              </Link>
            )}
            <h1 className="mt-3 text-4xl font-semibold leading-tight tracking-tight text-ink-primary text-balance sm:text-5xl">
              {product.name}
            </h1>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Badge tone={stock.tone} className="text-[11px]">
                {stock.text}
              </Badge>
              <span className="text-xs text-ink-tertiary">SKU: {product.sku}</span>
            </div>

            {product.description && (
              <p className="mt-5 max-w-prose text-body leading-relaxed text-ink-secondary">
                {product.description}
              </p>
            )}

            <div id="pdp-buybox" className="mt-8">
              <LuxuryBuyPanel product={product} />
            </div>
          </section>
        </div>

        <TrustRow />

        <KeyFeatures product={product} />

        <LifestyleBanner product={product} />

        <Reveal className="mt-20">
          <FrequentlyBoughtTogether
            product={product}
            related={related}
            isLoading={relatedLoading}
          />
        </Reveal>

        <CustomerSay product={product} />

        <ProductRail
          title="You may also like"
          products={likely?.length ? likely : coPurchased}
          isLoading={likelyLoading && coPurchasedLoading}
        />

        <BrowsingHistoryRail excludeId={product.id} title="Recently viewed" />

        <CustomerReviewsSection product={product} />
      </div>

      <StickyBuyBar product={product} />
    </Page>
  );
}

function Loading() {
  return (
    <Page>
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-12">
        {/* Gallery skeleton */}
        <div className="flex flex-col gap-3">
          <Skeleton className="aspect-[4/5] rounded-md" />
          <div className="grid grid-cols-5 gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} variant="circle" className="aspect-square w-full rounded-sm" />
            ))}
          </div>
        </div>

        {/* Buy panel skeleton */}
        <div className="flex flex-col gap-5">
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="h-10 w-4/5" />
          <Skeleton variant="text" lines={3} className="w-full" />
          <div className="mt-2 flex flex-col gap-3 rounded-lg border border-line-subtle p-5">
            <Skeleton className="h-8 w-28" />
            <Skeleton className="h-3 w-40" />
            <Skeleton className="h-3 w-32" />
            <div className="mt-2 h-px w-full bg-line-subtle" />
            <Skeleton className="h-11 w-full rounded-full" />
            <Skeleton className="h-11 w-full rounded-full" />
            <Skeleton className="h-11 w-full rounded-sm" />
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
