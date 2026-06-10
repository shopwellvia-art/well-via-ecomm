import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Search, PackageX, AlertTriangle, ChevronLeft, ChevronRight, X, SlidersHorizontal } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { ProductGrid } from '@/features/products/components/ProductGrid.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { fadeUp, heroContainer } from '@/lib/motion.js';

const PAGE_SIZE = 12;

export default function ProductListPage() {
  const [searchParams] = useSearchParams();
  // The catalog can be filtered by category via either URL shape:
  //   ?category_id=<id>   (used by product-detail "Visit the X store" links)
  //   ?category=<slug>    (used by the homepage category circles)
  const categoryIdParam = searchParams.get('category_id');
  const categorySlug = searchParams.get('category');

  const { data: categories = [] } = useCategories();

  const activeCategory = useMemo(() => {
    if (categoryIdParam) {
      const id = Number(categoryIdParam);
      return categories.find((c) => c.id === id) || (Number.isFinite(id) ? { id, name: null } : null);
    }
    if (categorySlug) {
      return categories.find((c) => c.slug === categorySlug) || null;
    }
    return null;
  }, [categoryIdParam, categorySlug, categories]);

  const categoryId = activeCategory?.id;

  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);

  // Debounce the search input so we do not refetch on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  // Reset to the first page whenever the active category changes.
  useEffect(() => {
    setPage(1);
  }, [categoryId, categorySlug]);

  const { data, isLoading, isError, refetch } = useProducts({
    q: query || undefined,
    category_id: categoryId,
    page,
    page_size: PAGE_SIZE,
  });
  const addToCart = useAddToCart();

  const products = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const isFiltered = !!(categoryIdParam || categorySlug);
  const heading = activeCategory?.name || (isFiltered ? 'Category' : 'Shop');

  return (
    <Page>
      <Breadcrumbs
        items={isFiltered ? [{ label: 'Shop', to: '/products' }] : []}
        current={heading}
        className="mb-6"
      />

      {/* Page header — title + search + filter strip */}
      <motion.header
        variants={heroContainer}
        initial="hidden"
        animate="show"
        className="flex flex-col gap-5"
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <motion.div variants={fadeUp}>
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-h1 tracking-tight text-ink-primary">{heading}</h1>
              {isFiltered && (
                <Link
                  to="/products"
                  className="inline-flex items-center gap-1.5 rounded-full border border-line-subtle bg-fill px-3 py-1 text-xs font-medium text-ink-secondary transition-colors hover:bg-fill-strong hover:text-ink-primary focus-visible:focus-ring"
                >
                  <X className="size-3.5" aria-hidden="true" />
                  Clear filter
                </Link>
              )}
            </div>
            <p className="mt-1.5 text-sm text-ink-secondary">
              {isLoading ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-block size-1.5 animate-pulse rounded-full bg-accent" />
                  Loading the collection…
                </span>
              ) : (
                <>
                  <span className="nums font-medium text-ink-primary">{total.toLocaleString()}</span>
                  {' '}product{total === 1 ? '' : 's'}
                  {activeCategory?.name ? ` in ${activeCategory.name}` : ''}
                </>
              )}
            </p>
          </motion.div>

          <motion.div variants={fadeUp} className="w-full sm:w-72">
            <Input
              type="search"
              label="Search"
              icon={Search}
              placeholder="Search products…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </motion.div>
        </div>

        {/* Category chip rail — only when we have categories loaded */}
        {categories.length > 0 && !isFiltered && (
          <motion.div
            variants={fadeUp}
            className="flex items-center gap-2 overflow-x-auto pb-1 [scrollbar-width:none]"
            aria-label="Browse by category"
          >
            <span className="flex shrink-0 items-center gap-1.5 text-xs text-ink-tertiary">
              <SlidersHorizontal className="size-3.5" aria-hidden="true" />
              Browse
            </span>
            <div className="mx-2 h-4 w-px bg-line-subtle" aria-hidden="true" />
            {categories.slice(0, 10).map((cat) => (
              <Link
                key={cat.id}
                to={`/products?category_id=${cat.id}`}
                className="shrink-0 rounded-full border border-line-subtle bg-bg-elevated px-3.5 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:border-accent/50 hover:bg-accent-soft hover:text-accent focus-visible:focus-ring"
              >
                {cat.name}
              </Link>
            ))}
          </motion.div>
        )}

        {/* Active category badge */}
        {isFiltered && activeCategory?.name && (
          <motion.div variants={fadeUp} className="flex items-center gap-2">
            <span className="text-xs text-ink-tertiary">Showing</span>
            <Badge tone="accent" outline>
              {activeCategory.name}
            </Badge>
          </motion.div>
        )}
      </motion.header>

      {/* Divider */}
      <div className="mt-6 border-t border-line-subtle" />

      <div className="mt-6">
        {isError ? (
          <EmptyState
            iconTone="danger"
            icon={AlertTriangle}
            title="We couldn't load products"
            description="Something went wrong on our end. Please try again."
            action={
              <Button size="sm" onClick={() => refetch()}>
                Retry
              </Button>
            }
          />
        ) : !isLoading && products.length === 0 ? (
          <EmptyState
            icon={PackageX}
            title="No products found"
            description={
              query
                ? `Nothing matched "${query}". Try a different search.`
                : activeCategory?.name
                  ? `No products in ${activeCategory.name} yet. Check back shortly.`
                  : 'The catalog is being stocked. Check back shortly.'
            }
            action={
              isFiltered ? (
                <Link to="/products">
                  <Button size="sm" variant="secondary">
                    View all products
                  </Button>
                </Link>
              ) : undefined
            }
          />
        ) : (
          <ProductGrid
            products={products}
            loading={isLoading}
            skeletonCount={PAGE_SIZE}
            onQuickAdd={(p) => addToCart.mutate({ productId: p.id })}
          />
        )}
      </div>

      {!isLoading && !isError && totalPages > 1 && (
        <nav
          className="mt-12 flex items-center justify-center gap-4"
          aria-label="Pagination"
        >
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
            Previous
          </Button>

          {/* Page indicator pills */}
          <div className="flex items-center gap-1.5" aria-hidden="true">
            {Array.from({ length: Math.min(totalPages, 7) }).map((_, i) => {
              const pg = i + 1;
              return (
                <button
                  key={pg}
                  type="button"
                  onClick={() => setPage(pg)}
                  className={[
                    'nums size-8 rounded-sm text-xs font-medium transition-colors focus-visible:focus-ring',
                    pg === page
                      ? 'bg-accent text-white shadow-glow-sm'
                      : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                  ].join(' ')}
                  aria-label={`Go to page ${pg}`}
                  aria-current={pg === page ? 'page' : undefined}
                >
                  {pg}
                </button>
              );
            })}
            {totalPages > 7 && (
              <span className="text-xs text-ink-tertiary">… {totalPages}</span>
            )}
          </div>

          <Button
            variant="secondary"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Next
            <ChevronRight className="size-4" aria-hidden="true" />
          </Button>
        </nav>
      )}
    </Page>
  );
}
