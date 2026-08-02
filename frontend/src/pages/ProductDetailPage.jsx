import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Page } from '@/components/layout/Page.jsx';
import PageMeta from '@/components/storefront/PageMeta.jsx';
import JsonLd from '@/components/storefront/JsonLd.jsx';
import { absoluteUrl } from '@/lib/pageMeta.js';
import { buildBreadcrumbSchema, buildProductSchema } from '@/lib/productSchema.js';
import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';
import {
  useProduct,
  useRelatedProducts,
  useCoPurchasedProducts,
  useLikelyProducts,
} from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { useTrackProductView } from '@/features/history/store.js';

// Feature components — logic 100% preserved, only page chrome re-skinned
import { FrequentlyBoughtTogether } from '@/features/products/components/FrequentlyBoughtTogether.jsx';
import { BrowsingHistoryRail } from '@/features/history/BrowsingHistoryRail.jsx';
import { CustomerReviewsSection } from '@/features/reviews/CustomerReviewsSection.jsx';
import { Reveal } from '@/features/products/components/luxury/luxe.jsx';
import { LuxuryGallery } from '@/features/products/components/luxury/LuxuryGallery.jsx';
import { LuxuryBuyPanel } from '@/features/products/components/luxury/LuxuryBuyPanel.jsx';
import { StickyBuyBar } from '@/features/products/components/luxury/StickyBuyBar.jsx';

// PDP content sections — each renders null when its product field is empty
import { BenefitsGrid } from '@/features/products/components/pdp/BenefitsGrid.jsx';
import { PackChips } from '@/features/products/components/pdp/PackChips.jsx';
import { IngredientsSection } from '@/features/products/components/pdp/IngredientsSection.jsx';
import { ExpectTimeline } from '@/features/products/components/pdp/ExpectTimeline.jsx';

// Wellness storefront components
import ProductGrid from '@/components/storefront/ProductGrid.jsx';
import { LeafIcon } from '@/components/storefront/Icons.jsx';

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

  const relatedProducts = likely?.length ? likely : coPurchased;

  const canonical = absoluteUrl(`/products/${product.id}`);
  const ogImage = product.image_url ? absoluteUrl(product.image_url) : undefined;
  // Lead the description with what the product is for; the flavour and pack
  // size are what shoppers scan for in a search snippet.
  const metaDescription =
    product.short_description ||
    product.description ||
    `${product.name} from Wellvia.`;

  return (
    <Page>
      <PageMeta
        title={product.name}
        description={metaDescription}
        canonicalPath={`/products/${product.id}`}
        image={product.image_url}
        type="product"
      />
      <JsonLd
        id="product"
        data={buildProductSchema(product, { canonical, image: ogImage })}
      />
      <JsonLd
        id="breadcrumb"
        data={buildBreadcrumbSchema([
          { name: 'Home', path: '/' },
          { name: 'Shop', path: '/products' },
          ...(categoryName
            ? [{ name: categoryName, path: `/products?category_id=${product.category_id}` }]
            : []),
          { name: product.name, path: `/products/${product.id}` },
        ])}
      />

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

      {/* ── BUY AREA: gallery left, buy rail right ─────────────────────────── */}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:gap-12">

        {/* LEFT — gallery (vertical thumbnails + main stage + hover-zoom) */}
        <div className="lg:sticky lg:top-[110px] lg:self-start">
          <div className="rounded-xl2 border border-wline bg-wcard p-4 lg:p-5">
            <LuxuryGallery product={product} />
          </div>
        </div>

        {/* RIGHT — buy rail (title → price → CTA → delivery → accordions) */}
        <div>
          <LuxuryBuyPanel product={product} />
        </div>
      </div>

      {/* ── CONTENT SECTIONS — each hides itself when its field is null ────── */}
      <BenefitsGrid benefits={product.benefits} />
      <PackChips highlights={product.highlights} />
      <IngredientsSection product={product} />
      <ExpectTimeline steps={product.usage_steps} product={product} />

      {/* ── FREQUENTLY BOUGHT TOGETHER ────────────────────────────────────── */}
      <Reveal className="mt-12">
        <FrequentlyBoughtTogether
          product={product}
          related={related}
          isLoading={relatedLoading}
        />
      </Reveal>

      {/* ── YOU MAY ALSO LIKE — wellness ProductGrid ──────────────────────── */}
      {(relatedProducts?.length > 0 || likelyLoading || coPurchasedLoading) && (
        <Reveal className="mt-14">
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

      {/* ── CUSTOMER REVIEWS (id="reviews" — rating anchor target) ────────── */}
      <CustomerReviewsSection product={product} />

      {/* ── STICKY BUY BAR (mobile always-on / desktop slides in) ─────────── */}
      <StickyBuyBar product={product} />

      {/* Spacer for mobile sticky bar */}
      <div className="h-16 lg:hidden" aria-hidden="true" />
    </Page>
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

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:gap-12">
        {/* Gallery skeleton */}
        <div className="rounded-xl2 border border-wline bg-wcard p-4 lg:p-5">
          <Skeleton className="aspect-square rounded-xl2" />
          <div className="mt-3 grid grid-cols-5 gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="aspect-square rounded-lg" />
            ))}
          </div>
        </div>

        {/* Buy rail skeleton */}
        <div className="flex flex-col gap-4">
          <div>
            <Skeleton className="h-10 w-full" />
            <Skeleton className="mt-2 h-6 w-3/4" />
          </div>
          <Skeleton className="h-4 w-36" />
          <div className="flex items-end gap-3">
            <Skeleton className="h-9 w-28" />
            <Skeleton className="h-4 w-16" />
          </div>
          <Skeleton className="h-10 w-32 rounded-lg" />
          <Skeleton className="h-16 w-full rounded-xl" />
          <Skeleton className="h-[52px] w-full rounded-full" />
          <Skeleton className="h-[52px] w-full rounded-full" />
          <Skeleton className="h-24 w-full rounded-xl" />
          <div className="flex flex-col gap-2.5">
            <Skeleton className="h-12 w-full rounded-xl" />
            <Skeleton className="h-12 w-full rounded-xl" />
            <Skeleton className="h-12 w-full rounded-xl" />
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
