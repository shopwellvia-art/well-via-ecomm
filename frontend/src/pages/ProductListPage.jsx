import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Search,
  PackageX,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  X,
  SlidersHorizontal,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { ProductGrid } from '@/features/products/components/ProductGrid.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { cn } from '@/lib/utils.js';

const PAGE_SIZE = 12;

const SORT_OPTIONS = [
  { value: 'relevance', label: 'Relevance' },
  { value: 'price_asc', label: 'Price — Low to High' },
  { value: 'price_desc', label: 'Price — High to Low' },
  { value: 'newest', label: 'Newest First' },
];

export default function ProductListPage() {
  const [searchParams] = useSearchParams();
  const categoryIdParam = searchParams.get('category_id');
  const categorySlug = searchParams.get('category');
  const queryParam = searchParams.get('q') ?? '';

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

  const [search, setSearch] = useState(queryParam);
  const [query, setQuery] = useState(queryParam);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState('relevance');
  const [filterOpen, setFilterOpen] = useState(false);

  // Refs for drawer focus management
  const filterTriggerRef = useRef(null);
  const drawerHeadingRef = useRef(null);

  useEffect(() => {
    setSearch(queryParam);
  }, [queryParam]);

  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [categoryId, categorySlug]);

  // Focus drawer heading when it opens; restore focus to trigger when it closes
  useEffect(() => {
    if (filterOpen) {
      drawerHeadingRef.current?.focus();
    } else {
      filterTriggerRef.current?.focus();
    }
  }, [filterOpen]);

  const { data, isLoading, isError, refetch } = useProducts({
    q: query || undefined,
    category_id: categoryId,
    page,
    page_size: PAGE_SIZE,
    sort_by: sortBy,
  });
  const addToCart = useAddToCart();

  const products = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const isFiltered = !!(categoryIdParam || categorySlug);
  const heading = activeCategory?.name || (isFiltered ? 'Category' : 'All Products');

  // Page numbers to display (Flipkart style: show up to 5 pages around current)
  const pageNumbers = useMemo(() => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
    const start = Math.max(1, Math.min(page - 2, totalPages - 4));
    const end = Math.min(totalPages, start + 4);
    const nums = Array.from({ length: end - start + 1 }, (_, i) => start + i);
    if (nums[0] > 1) nums.unshift(-1); // -1 = ellipsis
    if (nums[nums.length - 1] < totalPages) nums.push(-2); // -2 = ellipsis end
    return nums;
  }, [page, totalPages]);

  return (
    <Page>
      <Breadcrumbs
        items={isFiltered ? [{ label: 'Home', to: '/' }, { label: 'Shop', to: '/products' }] : [{ label: 'Home', to: '/' }]}
        current={heading}
        className="mb-4"
      />

      {/* Main two-pane layout */}
      <div className="flex gap-0 lg:gap-5">
        {/* ── FILTER SIDEBAR (desktop) ── */}
        <aside className="hidden lg:block w-60 xl:w-64 shrink-0">
          <div className="sticky top-20 rounded-sm border border-line-subtle bg-bg-elevated shadow-sm overflow-hidden">
            {/* Sidebar header */}
            <div className="flex items-center justify-between border-b border-line-subtle px-4 py-3">
              <span className="text-sm font-semibold text-ink-primary">Filters</span>
              {isFiltered && (
                <Link
                  to="/products"
                  className="text-xs font-medium text-accent hover:underline focus-visible:outline-none focus-visible:underline"
                >
                  Clear All
                </Link>
              )}
            </div>

            {/* Search in sidebar */}
            <div className="border-b border-line-subtle px-4 py-3">
              <Input
                type="search"
                label="Search"
                icon={Search}
                placeholder="Search products…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            {/* Categories */}
            {categories.length > 0 && (
              <div className="px-0 py-3">
                <p className="px-4 pb-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                  Category
                </p>
                <ul>
                  <li>
                    <Link
                      to="/products"
                      aria-current={!isFiltered ? 'page' : undefined}
                      className={cn(
                        'flex items-center gap-2.5 px-4 py-2 text-sm transition-colors',
                        !isFiltered
                          ? 'border-l-2 border-accent bg-accent/12 font-semibold text-accent'
                          : 'text-ink-secondary hover:bg-bg-sunken hover:text-ink-primary',
                      )}
                    >
                      All Categories
                    </Link>
                  </li>
                  {categories.map((cat) => {
                    const active = activeCategory?.id === cat.id;
                    return (
                      <li key={cat.id}>
                        <Link
                          to={`/products?category_id=${cat.id}`}
                          aria-current={active ? 'page' : undefined}
                          className={cn(
                            'flex items-center gap-2.5 px-4 py-2 text-sm transition-colors',
                            active
                              ? 'border-l-2 border-accent bg-accent/12 font-semibold text-accent'
                              : 'text-ink-secondary hover:bg-bg-sunken hover:text-ink-primary',
                          )}
                        >
                          {cat.name}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </div>
        </aside>

        {/* ── RIGHT COLUMN ── */}
        <div className="min-w-0 flex-1">
          {/* SORT BAR */}
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-sm border border-line-subtle bg-bg-elevated px-4 py-3 shadow-sm">
            {/* Left: count + mobile filter button */}
            <div className="flex items-center gap-3">
              {/* Mobile filter trigger */}
              <button
                ref={filterTriggerRef}
                type="button"
                onClick={() => setFilterOpen(true)}
                aria-expanded={filterOpen}
                aria-controls="filter-drawer"
                className="inline-flex items-center gap-1.5 rounded-sm border border-line-subtle px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:border-line-strong hover:text-ink-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent lg:hidden"
              >
                <SlidersHorizontal className="size-3.5" aria-hidden="true" />
                Filters
              </button>
              <span className="text-sm text-ink-secondary">
                {isLoading ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="inline-block size-1.5 animate-pulse rounded-full bg-accent" />
                    Loading…
                  </span>
                ) : total === 0 ? (
                  <span className="font-semibold text-ink-primary nums">0 products</span>
                ) : (
                  <>
                    Showing{' '}
                    <span className="font-semibold text-ink-primary nums">
                      {((page - 1) * PAGE_SIZE + 1).toLocaleString()}–{Math.min(page * PAGE_SIZE, total).toLocaleString()}
                    </span>{' '}
                    of{' '}
                    <span className="font-semibold text-ink-primary nums">{total.toLocaleString()}</span>{' '}
                    product{total === 1 ? '' : 's'}
                    {activeCategory?.name ? ` in ${activeCategory.name}` : ''}
                  </>
                )}
              </span>
            </div>

            {/* Sort options */}
            <div className="flex items-center gap-2 overflow-x-auto [scrollbar-width:none]">
              <span className="shrink-0 text-xs font-medium text-ink-tertiary">Sort By</span>
              <div className="flex min-h-[2.75rem] items-center gap-1">
                {SORT_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => { setSortBy(opt.value); setPage(1); }}
                    className={cn(
                      'shrink-0 rounded-full px-3 py-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                      sortBy === opt.value
                        ? 'bg-accent text-white'
                        : 'text-ink-secondary hover:text-ink-primary',
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Active category badge */}
          {isFiltered && activeCategory?.name && (
            <div className="mb-3 flex items-center gap-2">
              <span className="rounded-sm border border-accent/30 bg-accent/12 px-2.5 py-1 text-xs font-medium text-accent">
                {activeCategory.name}
              </span>
              <Link
                to="/products"
                className="inline-flex items-center gap-1 text-xs text-ink-tertiary hover:text-ink-primary"
              >
                <X className="size-3" aria-hidden="true" />
                Clear
              </Link>
            </div>
          )}

          {/* Product grid / states */}
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
                    ? `No products in ${activeCategory.name} yet.`
                    : 'The catalog is being stocked. Check back shortly.'
              }
              action={
                isFiltered ? (
                  <Link to="/products">
                    <Button size="sm" variant="secondary">View all products</Button>
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

          {/* ── PAGINATION (Flipkart style) ── */}
          {!isLoading && !isError && totalPages > 1 && (
            <nav
              className="mt-8 flex items-center justify-center gap-1"
              aria-label="Pagination"
            >
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                aria-label="Previous page"
                className="inline-flex h-9 w-9 items-center justify-center rounded-sm border border-line-subtle bg-bg-elevated text-ink-secondary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <ChevronLeft className="size-4" aria-hidden="true" />
              </button>

              {pageNumbers.map((pg, i) => {
                if (pg < 0) {
                  return (
                    <span
                      key={`ellipsis-${i}`}
                      className="inline-flex h-9 w-9 items-center justify-center text-sm text-ink-tertiary"
                    >
                      …
                    </span>
                  );
                }
                return (
                  <button
                    key={pg}
                    type="button"
                    onClick={() => setPage(pg)}
                    aria-label={`Go to page ${pg}`}
                    aria-current={pg === page ? 'page' : undefined}
                    className={cn(
                      'nums inline-flex h-9 w-9 items-center justify-center rounded-sm border text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                      pg === page
                        ? 'border-accent bg-accent text-white shadow-sm'
                        : 'border-line-subtle bg-bg-elevated text-ink-secondary hover:border-accent hover:text-accent',
                    )}
                  >
                    {pg}
                  </button>
                );
              })}

              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                aria-label="Next page"
                className="inline-flex h-9 w-9 items-center justify-center rounded-sm border border-line-subtle bg-bg-elevated text-ink-secondary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <ChevronRight className="size-4" aria-hidden="true" />
              </button>
            </nav>
          )}
        </div>
      </div>

      {/* ── MOBILE FILTER DRAWER ── */}
      {filterOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          {/* Backdrop — keyboard-operable button */}
          <button
            type="button"
            aria-label="Close filters"
            className="absolute inset-0 cursor-default bg-black/50"
            onClick={() => setFilterOpen(false)}
          />
          {/* Drawer */}
          <div
            id="filter-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="filter-drawer-title"
            onKeyDown={(e) => { if (e.key === 'Escape') setFilterOpen(false); }}
            className="absolute inset-y-0 left-0 flex w-72 max-w-[calc(100vw-2.5rem)] flex-col bg-bg-elevated shadow-xl"
          >
            <div className="flex items-center justify-between border-b border-line-subtle px-4 py-4">
              <span
                id="filter-drawer-title"
                ref={drawerHeadingRef}
                tabIndex={-1}
                className="text-base font-semibold text-ink-primary outline-none"
              >
                Filters
              </span>
              <button
                type="button"
                onClick={() => setFilterOpen(false)}
                aria-label="Close filters"
                className="grid size-11 place-items-center rounded-sm text-ink-secondary hover:text-ink-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto py-2">
              {/* Search */}
              <div className="border-b border-line-subtle px-4 pb-4 pt-2">
                <Input
                  type="search"
                  label="Search"
                  icon={Search}
                  placeholder="Search products…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>

              {/* Categories */}
              {categories.length > 0 && (
                <div className="py-2">
                  <p className="px-4 pb-2 pt-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Category
                  </p>
                  <ul>
                    <li>
                      <Link
                        to="/products"
                        onClick={() => setFilterOpen(false)}
                        aria-current={!isFiltered ? 'page' : undefined}
                        className={cn(
                          'flex items-center gap-2.5 px-4 py-2.5 text-sm',
                          !isFiltered
                            ? 'border-l-2 border-accent bg-accent/12 font-semibold text-accent'
                            : 'text-ink-secondary',
                        )}
                      >
                        All Categories
                      </Link>
                    </li>
                    {categories.map((cat) => {
                      const active = activeCategory?.id === cat.id;
                      return (
                        <li key={cat.id}>
                          <Link
                            to={`/products?category_id=${cat.id}`}
                            onClick={() => setFilterOpen(false)}
                            aria-current={active ? 'page' : undefined}
                            className={cn(
                              'flex items-center gap-2.5 px-4 py-2.5 text-sm',
                              active
                                ? 'border-l-2 border-accent bg-accent/12 font-semibold text-accent'
                                : 'text-ink-secondary',
                            )}
                          >
                            {cat.name}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>

            <div className="border-t border-line-subtle px-4 py-4">
              {isFiltered && (
                <Link
                  to="/products"
                  onClick={() => setFilterOpen(false)}
                  className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-sm border border-line-subtle py-2 text-sm font-medium text-ink-secondary"
                >
                  <X className="size-4" aria-hidden="true" />
                  Clear All Filters
                </Link>
              )}
              <Button
                variant="primary"
                size="sm"
                className="w-full"
                onClick={() => setFilterOpen(false)}
              >
                Apply
              </Button>
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
