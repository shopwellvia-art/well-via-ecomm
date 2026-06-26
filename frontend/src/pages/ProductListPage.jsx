import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  X,
  Search,
  SlidersHorizontal,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import ProductGrid from '@/components/storefront/ProductGrid.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { cn } from '@/lib/utils.js';

const PAGE_SIZE = 12;

const SORT_OPTIONS = [
  { value: 'relevance',  label: 'Relevance' },
  { value: 'price_asc',  label: 'Price — Low to High' },
  { value: 'price_desc', label: 'Price — High to Low' },
  { value: 'newest',     label: 'Newest First' },
];

export default function ProductListPage() {
  const [searchParams] = useSearchParams();
  const categoryIdParam = searchParams.get('category_id');
  const categorySlug    = searchParams.get('category');
  const queryParam      = searchParams.get('q') ?? '';

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

  const [search,     setSearch]     = useState(queryParam);
  const [query,      setQuery]      = useState(queryParam);
  const [page,       setPage]       = useState(1);
  const [sortBy,     setSortBy]     = useState('relevance');
  const [filterOpen, setFilterOpen] = useState(false);

  // Refs for drawer focus management
  const filterTriggerRef = useRef(null);
  const drawerHeadingRef = useRef(null);

  useEffect(() => { setSearch(queryParam); }, [queryParam]);

  useEffect(() => {
    const t = setTimeout(() => { setQuery(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => { setPage(1); }, [categoryId, categorySlug]);

  // Focus drawer heading when it opens; restore focus to trigger when it closes
  useEffect(() => {
    if (filterOpen) drawerHeadingRef.current?.focus();
    else            filterTriggerRef.current?.focus();
  }, [filterOpen]);

  const { data, isLoading, isError, refetch } = useProducts({
    q:           query || undefined,
    category_id: categoryId,
    page,
    page_size:   PAGE_SIZE,
    sort_by:     sortBy,
  });

  const products   = data?.items ?? [];
  const total      = data?.total  ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const isFiltered = !!(categoryIdParam || categorySlug);
  const heading    = activeCategory?.name || (isFiltered ? 'Category' : 'All Products');

  // Page numbers: up to 5 pages around current + ellipsis sentinels
  const pageNumbers = useMemo(() => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
    const start = Math.max(1, Math.min(page - 2, totalPages - 4));
    const end   = Math.min(totalPages, start + 4);
    const nums  = Array.from({ length: end - start + 1 }, (_, i) => start + i);
    if (nums[0] > 1)                            nums.unshift(-1);  // -1 = ellipsis
    if (nums[nums.length - 1] < totalPages) nums.push(-2);         // -2 = ellipsis end
    return nums;
  }, [page, totalPages]);

  return (
    <Page bleed>
      <div className="max-w-[1320px] mx-auto px-5 sm:px-10 lg:px-16 py-7 lg:py-[52px]">

        {/* ── BREADCRUMB ── */}
        <nav aria-label="Breadcrumb" className="mb-3.5">
          <span className="text-[11.5px] text-wmuted tracking-wide">
            <Link
              to="/"
              className="text-wmuted hover:text-wgreen transition-colors no-underline"
            >
              Home
            </Link>
            {' '}&nbsp;/&nbsp;{' '}
            {isFiltered && (
              <>
                <Link
                  to="/products"
                  className="text-wmuted hover:text-wgreen transition-colors no-underline"
                >
                  Shop
                </Link>
                {' '}&nbsp;/&nbsp;{' '}
              </>
            )}
            <span className="text-wink">{heading}</span>
          </span>
        </nav>

        {/* ── TITLE + COUNT + SORT ── */}
        <div className="flex flex-wrap items-end justify-between gap-4 mb-6 lg:mb-9">
          <div>
            <h1 className="font-wserif font-medium text-[clamp(30px,4.4vw,52px)] text-wink m-0 mb-1.5 leading-none">
              {isFiltered && activeCategory?.name ? activeCategory.name : 'Shop All Rituals'}
            </h1>
            <p className="text-[14px] text-wmuted m-0 font-light">
              {isLoading ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-block size-1.5 animate-pulse rounded-full bg-wgold" />
                  Loading…
                </span>
              ) : (
                `${total.toLocaleString('en-IN')} product${total === 1 ? '' : 's'}`
              )}
            </p>
          </div>

          {/* Sort select + mobile filter trigger */}
          <div className="flex items-center gap-2.5 text-[13px] text-wmuted">
            {/* Mobile filter button (hidden on lg+) */}
            <button
              ref={filterTriggerRef}
              type="button"
              onClick={() => setFilterOpen(true)}
              aria-expanded={filterOpen}
              aria-controls="filter-drawer"
              className="inline-flex items-center gap-1.5 rounded-full border border-wline px-4 py-2.5 text-[12.5px] text-wink cursor-pointer transition-colors hover:border-wgreen hover:text-wgreen lg:hidden"
            >
              <SlidersHorizontal className="size-3.5" aria-hidden="true" />
              Filters
            </button>

            <span className="hidden sm:inline">Sort</span>
            <select
              value={sortBy}
              onChange={(e) => { setSortBy(e.target.value); setPage(1); }}
              className="bg-wcard border border-wline rounded-full px-4 py-2.5 text-[13px] text-wink cursor-pointer focus:outline-none focus:border-wgreen transition-colors"
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* ── SEARCH BAR ── */}
        <div className="mb-5">
          <div className="relative max-w-md">
            <Search
              className="absolute left-3.5 top-1/2 -translate-y-1/2 size-4 text-wmuted pointer-events-none"
              aria-hidden="true"
            />
            <input
              type="search"
              placeholder="Search products…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-wcard border border-wline rounded-full pl-10 pr-4 py-2.5 text-[13px] text-wink placeholder:text-wmuted focus:outline-none focus:border-wgreen transition-colors"
            />
          </div>
        </div>

        {/* ── CATEGORY PILLS ── */}
        {categories.length > 0 && (
          <div className="flex flex-wrap gap-2.5 mb-6 lg:mb-9">
            <Link
              to="/products"
              aria-current={!isFiltered ? 'page' : undefined}
              className={cn(
                'rounded-full px-5 py-[9px] text-[12.5px] tracking-wide transition-all border no-underline',
                !isFiltered
                  ? 'bg-wgreen text-white border-wgreen'
                  : 'bg-transparent text-wink border-wline hover:border-wgreen hover:text-wgreen',
              )}
            >
              All
            </Link>
            {categories.map((cat) => {
              const active = activeCategory?.id === cat.id;
              return (
                <Link
                  key={cat.id}
                  to={`/products?category_id=${cat.id}`}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'rounded-full px-5 py-[9px] text-[12.5px] tracking-wide transition-all border no-underline',
                    active
                      ? 'bg-wgreen text-white border-wgreen'
                      : 'bg-transparent text-wink border-wline hover:border-wgreen hover:text-wgreen',
                  )}
                >
                  {cat.name}
                </Link>
              );
            })}
          </div>
        )}

        {/* ── ACTIVE FILTER CLEAR CHIP ── */}
        {isFiltered && activeCategory?.name && (
          <div className="mb-4 flex items-center gap-2">
            <span className="rounded-full border border-wgold/40 bg-wgold/10 px-3 py-1 text-[12px] font-medium text-wgold">
              {activeCategory.name}
            </span>
            <Link
              to="/products"
              className="inline-flex items-center gap-1 text-[12px] text-wmuted hover:text-wink transition-colors no-underline"
            >
              <X className="size-3" aria-hidden="true" />
              Clear
            </Link>
          </div>
        )}

        {/* ── PRODUCT GRID / STATES ── */}
        {isError ? (
          <div className="py-20 text-center">
            <p className="font-wserif text-[28px] text-wink mb-2">Something went wrong</p>
            <p className="text-wmuted text-[14px] mb-6">
              We couldn't load products. Please try again.
            </p>
            <button
              onClick={() => refetch()}
              className="bg-wgreen text-white rounded-full px-7 py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors"
            >
              Retry
            </button>
          </div>
        ) : isLoading ? (
          /* Skeleton grid */
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3.5 lg:gap-[22px]">
            {Array.from({ length: PAGE_SIZE }).map((_, i) => (
              <div
                key={i}
                className="bg-wcard border border-wline rounded-xl2 overflow-hidden animate-pulse"
              >
                <div className="bg-wcanvas h-[200px]" />
                <div className="p-[18px] pb-5 space-y-3">
                  <div className="h-3 bg-wcanvas rounded-full w-3/4" />
                  <div className="h-5 bg-wcanvas rounded-full" />
                  <div className="h-3 bg-wcanvas rounded-full w-1/2" />
                  <div className="h-10 bg-wcanvas rounded-full mt-2" />
                </div>
              </div>
            ))}
          </div>
        ) : products.length === 0 ? (
          <div className="py-20 text-center">
            <p className="font-wserif text-[28px] text-wink mb-2">No products found</p>
            <p className="text-wmuted text-[14px] mb-6">
              {query
                ? `Nothing matched "${query}". Try a different search.`
                : activeCategory?.name
                  ? `No products in ${activeCategory.name} yet.`
                  : 'The catalog is being stocked. Check back shortly.'}
            </p>
            {isFiltered && (
              <Link
                to="/products"
                className="inline-block bg-wgreen text-white no-underline rounded-full px-7 py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors"
              >
                View all products
              </Link>
            )}
          </div>
        ) : (
          <ProductGrid products={products} />
        )}

        {/* ── PAGINATION ── */}
        {!isLoading && !isError && totalPages > 1 && (
          <nav className="mt-10 flex items-center justify-center gap-1.5" aria-label="Pagination">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              aria-label="Previous page"
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-wline bg-wcard text-wmuted transition-colors hover:border-wgreen hover:text-wgreen disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="size-4" aria-hidden="true" />
            </button>

            {pageNumbers.map((pg, i) => {
              if (pg < 0) {
                return (
                  <span
                    key={`ellipsis-${i}`}
                    className="inline-flex h-9 w-9 items-center justify-center text-[13px] text-wmuted"
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
                    'inline-flex h-9 w-9 items-center justify-center rounded-full border text-[13px] font-medium transition-colors',
                    pg === page
                      ? 'bg-wgreen border-wgreen text-white shadow-sm'
                      : 'bg-wcard border-wline text-wmuted hover:border-wgreen hover:text-wgreen',
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
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-wline bg-wcard text-wmuted transition-colors hover:border-wgreen hover:text-wgreen disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronRight className="size-4" aria-hidden="true" />
            </button>
          </nav>
        )}
      </div>

      {/* ── MOBILE FILTER DRAWER ── */}
      {filterOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          {/* Backdrop */}
          <button
            type="button"
            aria-label="Close filters"
            className="absolute inset-0 cursor-default bg-wink/40 animate-dim"
            onClick={() => setFilterOpen(false)}
          />

          {/* Drawer panel */}
          <div
            id="filter-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="filter-drawer-title"
            onKeyDown={(e) => { if (e.key === 'Escape') setFilterOpen(false); }}
            className="absolute inset-y-0 left-0 flex w-72 max-w-[calc(100vw-2.5rem)] flex-col bg-wcard shadow-xl animate-slidein"
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-wline px-4 py-4">
              <span
                id="filter-drawer-title"
                ref={drawerHeadingRef}
                tabIndex={-1}
                className="text-base font-wserif font-semibold text-wink outline-none"
              >
                Filters
              </span>
              <button
                type="button"
                onClick={() => setFilterOpen(false)}
                aria-label="Close filters"
                className="grid size-11 place-items-center rounded-full text-wmuted hover:text-wink transition-colors"
              >
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>

            {/* Scrollable body */}
            <div className="flex-1 overflow-y-auto py-3">
              {/* Search */}
              <div className="border-b border-wline px-4 pb-4 pt-2">
                <div className="relative">
                  <Search
                    className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-wmuted pointer-events-none"
                    aria-hidden="true"
                  />
                  <input
                    type="search"
                    placeholder="Search products…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-full bg-wpaper border border-wline rounded-full pl-9 pr-4 py-2 text-[13px] text-wink placeholder:text-wmuted focus:outline-none focus:border-wgreen transition-colors"
                  />
                </div>
              </div>

              {/* Categories */}
              {categories.length > 0 && (
                <div className="py-2">
                  <p className="px-4 pb-2 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-wmuted">
                    Category
                  </p>
                  <ul>
                    <li>
                      <Link
                        to="/products"
                        onClick={() => setFilterOpen(false)}
                        aria-current={!isFiltered ? 'page' : undefined}
                        className={cn(
                          'flex items-center px-4 py-2.5 text-[13px] no-underline transition-colors',
                          !isFiltered
                            ? 'border-l-2 border-wgreen bg-wgreen/10 font-semibold text-wgreen'
                            : 'text-wmuted hover:text-wink',
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
                              'flex items-center px-4 py-2.5 text-[13px] no-underline transition-colors',
                              active
                                ? 'border-l-2 border-wgreen bg-wgreen/10 font-semibold text-wgreen'
                                : 'text-wmuted hover:text-wink',
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

            {/* Footer actions */}
            <div className="border-t border-wline px-4 py-4">
              {isFiltered && (
                <Link
                  to="/products"
                  onClick={() => setFilterOpen(false)}
                  className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-full border border-wline py-2.5 text-[13px] font-medium text-wmuted hover:text-wink transition-colors no-underline"
                >
                  <X className="size-4" aria-hidden="true" />
                  Clear All Filters
                </Link>
              )}
              <button
                type="button"
                className="w-full bg-wgreen text-white rounded-full py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors"
                onClick={() => setFilterOpen(false)}
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
