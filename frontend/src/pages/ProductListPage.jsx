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
import ProductCard from '@/components/storefront/ProductCard.jsx';
import FilterSidebar, {
  PRICE_MIN,
  PRICE_MAX,
} from '@/features/products/components/FilterSidebar.jsx';
import { useProducts, useBestsellers } from '@/features/products/hooks.js';
import { useCategories } from '@/features/categories/hooks.js';
import { GOALS, resolveGoalCategoryIds } from '@/lib/catalogOptions.js';
import { cn } from '@/lib/utils.js';

const PAGE_SIZE = 12;
const NEW_WINDOW_DAYS = 30;


const SORT_OPTIONS = [
  { value: 'newest', label: 'Newest First' },
  { value: 'price_asc', label: 'Lowest Price' },
  { value: 'price_desc', label: 'Highest Price' },
  { value: 'rating', label: 'Top Rated' },
];

/** Per-mode hero copy + defaults (mockups: All Products / Bestsellers / New Arrivals). */
const MODES = {
  all: {
    title: 'Wellness, Your Way.',
    sub: 'From better sleep to daily immunity, discover gummies crafted for every goal.',
    badge: undefined,
    heroBg: 'linear-gradient(115deg,#eef3e4 0%,#f7f4ea 55%,#e9efdc 100%)',
    heroImage: '/product-hero.png',
  },
  // best
  bestsellers: {
    title: 'Customer Favorites, For a Reason.',
    sub: 'Discover the gummies our customers keep coming back for.',
    badge: 'bestseller',
    heroBg: 'linear-gradient(115deg,#efe4f0 0%,#f7f0f4 55%,#e7dcEC 100%)',
    heroImage: '/bestseller-hero.png',
    chips: ['Science Backed Ingredients', 'Safe & Effective', 'Delicious & Easy to Enjoy', 'Loved by Thousands'],
  },

  'new-arrivals': {
    title: 'Fresh Drops, Feel Good Finds.',
    sub: 'Be the first to discover our latest wellness gummies.',
    badge: 'new',
    heroBg: 'linear-gradient(115deg,#e7efe0 0%,#f6f3e9 55%,#eae4d4 100%)',
    heroImage: '/product-hero.png',
  },
};

/* ── URL param helpers — the search params ARE the filter state ── */

function parseFilters(sp) {
  const csv = (key) => (sp.get(key) ? sp.get(key).split(',').filter(Boolean) : []);
  // Legacy links (?category_id=N) fold into the multi-select filter.
  const legacyId = sp.get('category_id') ? [Number(sp.get('category_id'))] : [];
  return {
    q: sp.get('q') ?? '',
    categoryIds: [...new Set([...csv('category_ids').map(Number), ...legacyId])].filter(
      Number.isFinite,
    ),
    goals: csv('goals'),
    flavours: csv('flavours'),
    minPrice: sp.get('min_price') ? Number(sp.get('min_price')) : null,
    maxPrice: sp.get('max_price') ? Number(sp.get('max_price')) : null,
    offers: csv('offers'),
    minRating: sp.get('min_rating'),
    inStock: sp.get('in_stock') === '1',
    sortBy: sp.get('sort_by') || 'newest',
    page: Math.max(1, Number(sp.get('page') || 1)),
  };
}

function filtersToParams(f) {
  const sp = {};
  if (f.q) sp.q = f.q;
  if (f.categoryIds.length) sp.category_ids = f.categoryIds.join(',');
  if (f.goals.length) sp.goals = f.goals.join(',');
  if (f.flavours.length) sp.flavours = f.flavours.join(',');
  if (f.minPrice != null) sp.min_price = String(f.minPrice);
  if (f.maxPrice != null) sp.max_price = String(f.maxPrice);
  if (f.offers.length) sp.offers = f.offers.join(',');
  if (f.minRating) sp.min_rating = f.minRating;
  if (f.inStock) sp.in_stock = '1';
  if (f.sortBy && f.sortBy !== 'newest') sp.sort_by = f.sortBy;
  if (f.page > 1) sp.page = String(f.page);
  return sp;
}

/** Map UI filters → GET /products query params. Goal slugs resolve to
 * category ids (goals ARE categories — see catalogOptions.js) and merge
 * into the `category_ids` param. */
function toApiParams(f, goalCategoryIds = []) {
  const categoryIds = [...new Set([...f.categoryIds, ...goalCategoryIds])];
  return {
    q: f.q || undefined,
    category_ids: categoryIds.length ? categoryIds.join(',') : undefined,
    flavours: f.flavours.length ? f.flavours.join(',') : undefined,
    min_price: f.minPrice ?? undefined,
    max_price: f.maxPrice ?? undefined,
    min_rating: f.minRating ?? undefined,
    in_stock: f.inStock || undefined,
    is_combo: f.offers.includes('combo') || undefined,
    // "Best Value" = discounted, ranked by discount depth client-side.
    discounted:
      f.offers.includes('discounted') || f.offers.includes('best_value') || undefined,
    sort_by: f.sortBy,
    page: f.page,
    page_size: PAGE_SIZE,
  };
}

/** Same predicates applied client-side (bestsellers mode fetches a plain list).
 * Goals compare via the same slug→category-id resolution as toApiParams. */
function applyFiltersLocally(items, f, goalCategoryIds = []) {
  const categoryIds = [...new Set([...f.categoryIds, ...goalCategoryIds])];
  let out = items.filter((p) => {
    if (f.q && !p.name.toLowerCase().includes(f.q.toLowerCase())) return false;
    if (categoryIds.length && !categoryIds.includes(p.category_id)) return false;
    if (f.flavours.length && !f.flavours.includes(p.flavour)) return false;
    if (f.minPrice != null && Number(p.price) < f.minPrice) return false;
    if (f.maxPrice != null && Number(p.price) > f.maxPrice) return false;
    if (f.minRating && !(Number(p.rating_avg) >= Number(f.minRating) && p.rating_count > 0))
      return false;
    if (f.inStock && p.stock <= 0) return false;
    if (f.offers.includes('combo') && !p.is_combo) return false;
    const discounted =
      p.compare_at_price != null && Number(p.compare_at_price) > Number(p.price);
    if ((f.offers.includes('discounted') || f.offers.includes('best_value')) && !discounted)
      return false;
    return true;
  });
  const discountPct = (p) =>
    p.compare_at_price
      ? (Number(p.compare_at_price) - Number(p.price)) / Number(p.compare_at_price)
      : 0;
  if (f.offers.includes('best_value')) out = [...out].sort((a, b) => discountPct(b) - discountPct(a));
  else if (f.sortBy === 'price_asc') out = [...out].sort((a, b) => a.price - b.price);
  else if (f.sortBy === 'price_desc') out = [...out].sort((a, b) => b.price - a.price);
  else if (f.sortBy === 'rating')
    out = [...out].sort((a, b) => b.rating_avg - a.rating_avg || b.rating_count - a.rating_count);
  return out;
}

const isNewProduct = (p) =>
  p.created_at &&
  Date.now() - new Date(p.created_at).getTime() < NEW_WINDOW_DAYS * 86_400_000;

/**
 * Shared listing template for /products, /bestsellers and /new-arrivals —
 * hero band, filter sidebar, active-filter chips, sort, grid, pagination.
 * All filter/sort/page state lives in the URL search params.
 */
export default function ProductListPage({ mode = 'all' }) {
  const [showSidebar, setShowSidebar] = useState(true);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);
  const modeCfg = MODES[mode] ?? MODES.all;

  const { data: categories = [] } = useCategories();

  // Legacy ?category=<slug> links resolve to an id once categories load.
  useEffect(() => {
    const slug = searchParams.get('category');
    if (!slug || !categories.length) return;
    const match = categories.find((c) => c.slug === slug);
    const next = new URLSearchParams(searchParams);
    next.delete('category');
    if (match) next.set('category_ids', String(match.id));
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categories, searchParams]);

  // Debounced search box (writes into the URL like every other filter).
  const [search, setSearch] = useState(filters.q);
  useEffect(() => setSearch(filters.q), [filters.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if (search.trim() !== filters.q) update({ q: search.trim() });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  function update(patch, { resetPage = true } = {}) {
    const next = { ...filters, ...patch };
    if (resetPage) next.page = 1;
    setSearchParams(filtersToParams(next), { replace: false });
  }

  // Mobile filter drawer
  const [filterOpen, setFilterOpen] = useState(false);
  const filterTriggerRef = useRef(null);
  const drawerHeadingRef = useRef(null);
  useEffect(() => {
    if (filterOpen) drawerHeadingRef.current?.focus();
    else filterTriggerRef.current?.focus();
  }, [filterOpen]);

  /* ── Data ── */
  // Goal filters resolve to category ids once categories load (goals ARE
  // category slugs — no separate backend field).
  const goalCategoryIds = useMemo(
    () => resolveGoalCategoryIds(filters.goals, categories),
    [filters.goals, categories],
  );

  const isBestsellers = mode === 'bestsellers';
  const isNewArrivals = mode === 'new-arrivals';

  // /products paginates server-side; /new-arrivals pulls one newest-first
  // page and applies the freshness window + local pagination client-side.
  const serverQuery = useProducts(
    isNewArrivals
      ? {
          ...toApiParams(filters, goalCategoryIds),
          sort_by: 'newest',
          page: 1,
          page_size: 100,
        }
      : toApiParams(filters, goalCategoryIds),
  );
  const bestsellersQuery = useBestsellers(24);

  const { isLoading, isError, refetch } = isBestsellers ? bestsellersQuery : serverQuery;

  let products, total, totalPages;
  if (isBestsellers) {
    const filtered = applyFiltersLocally(bestsellersQuery.data ?? [], filters, goalCategoryIds);
    total = filtered.length;
    totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    products = filtered.slice((filters.page - 1) * PAGE_SIZE, filters.page * PAGE_SIZE);
  } else if (isNewArrivals) {
    const fresh = (serverQuery.data?.items ?? []).filter(isNewProduct);
    const filtered = applyFiltersLocally(fresh, filters, goalCategoryIds);
    total = filtered.length;
    totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    products = filtered.slice((filters.page - 1) * PAGE_SIZE, filters.page * PAGE_SIZE);
  } else {
    products = serverQuery.data?.items ?? [];
    total = serverQuery.data?.total ?? 0;
    totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  }

  const shown = products.length;
  const catName = (id) => categories.find((c) => c.id === id)?.name ?? `Category ${id}`;

  /* Active filter chips (mockup: "Grape ×" "In Stock ×" next to the count) */
  const chips = [
    ...filters.goals.map((goal) => ({
    label: goal,
    clear: () => update({ goals: filters.goals.filter((x) => x !== goal) }),
  })),
    ...filters.categoryIds.map((id) => ({
      label: catName(id),
      clear: () => update({ categoryIds: filters.categoryIds.filter((x) => x !== id) }),
    })),
    ...filters.goals.map((slug) => ({
      label: GOALS.find((g) => g.slug === slug)?.label ?? slug,
      clear: () => update({ goals: filters.goals.filter((x) => x !== slug) }),
    })),
    ...filters.flavours.map((fl) => ({
      label: fl,
      clear: () => update({ flavours: filters.flavours.filter((x) => x !== fl) }),
    })),
    ...(filters.minPrice != null || filters.maxPrice != null
      ? [
          {
            label: `₹${filters.minPrice ?? PRICE_MIN}–₹${filters.maxPrice ?? PRICE_MAX}`,
            clear: () => update({ minPrice: null, maxPrice: null }),
          },
        ]
      : []),
    ...filters.offers.map((o) => ({
      label: { combo: 'Combo Packs', discounted: 'Discounted', best_value: 'Best Value' }[o] ?? o,
      clear: () => update({ offers: filters.offers.filter((x) => x !== o) }),
    })),
    ...(filters.minRating
      ? [{ label: `${filters.minRating}★ & above`, clear: () => update({ minRating: null }) }]
      : []),
    ...(filters.inStock ? [{ label: 'In Stock', clear: () => update({ inStock: false }) }] : []),
    ...(filters.q ? [{ label: `"${filters.q}"`, clear: () => update({ q: '' }) }] : []),
  ];
  const hasFilters = chips.length > 0;

  /* Badge per card: mode ribbon, or derived "New" inside the window on /products */
  const gridBadge = modeCfg.badge;

  const pageNumbers = useMemo(() => {
    const page = filters.page;
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
    const start = Math.max(1, Math.min(page - 2, totalPages - 4));
    const end = Math.min(totalPages, start + 4);
    const nums = Array.from({ length: end - start + 1 }, (_, i) => start + i);
    if (nums[0] > 1) nums.unshift(-1);
    if (nums[nums.length - 1] < totalPages) nums.push(-2);
    return nums;
  }, [filters.page, totalPages]);

  const sidebar = (
    <FilterSidebar filters={filters} onChange={(patch) => update(patch)} categories={categories} />
  );

  return (
    // HERO BANNER OF ALL 3 PAGES
    <Page bleed>
      {/* ── HERO BAND ── */}
<section
  className="border-b border-wline"
  style={{ background: modeCfg.heroBg }}
  aria-label={modeCfg.title}
>

  {/* BESTSELLERS */}
{mode === "bestsellers" && (
  <div className="relative overflow-hidden w-full">
    <img
      src={modeCfg.heroImage}
      alt="Best Sellers"
      className="w-full h-[160px] sm:h-[200px] md:h-auto object-cover object-left md:object-contain block"
    />

    <Link
      to="/bestsellers"
      className="absolute left-3.5 bottom-3 md:left-20 md:bottom-12 bg-[#08112C] text-white px-2.5 py-1.5 md:px-6 md:py-3 rounded-full font-serif text-[9px] sm:text-xs md:text-base leading-none shadow-md"
    >
      Shop for Bestsellers
    </Link>
  </div>
)}

  {/* ALL PRODUCTS */}
  {mode === "all" && (
    <div className="relative overflow-hidden w-full">
      <img
        src={modeCfg.heroImage}
        alt="Products"
        className="w-full h-[160px] sm:h-[200px] md:h-auto object-cover object-center md:object-contain block"
      />

      <div className="absolute left-3.5 top-1/2 -translate-y-1/2 max-w-[58%] md:left-12 md:max-w-none">
        <h1 className="font-cormorant font-medium text-[clamp(15px,4.5vw,22px)] md:text-[clamp(30px,4.2vw,50px)] text-[#133F30] m-0 mb-0.5 md:mb-2 leading-[1.15] md:leading-[1.35] max-w-[220px] md:max-w-[420px]">
          {modeCfg.title}
        </h1>

        <p className="font-cormorant text-[10px] sm:text-[12px] md:text-[14.5px] text-black m-0 font-light max-w-[140px] md:max-w-[380px] mb-2 md:mb-5 leading-[1.25] md:leading-normal">
          {modeCfg.sub}
        </p>

        <Link
          to="/products"
          className="inline-block bg-[#08112C] text-white px-2.5 py-1.5 md:px-6 md:py-3 rounded-full font-serif text-[9px] sm:text-xs md:text-base leading-none"
        >
          Shop All Products
        </Link>
      </div>
    </div>
  )}

  {/* NEW ARRIVALS */}
  {mode === "new-arrivals" && (
    <div className="relative overflow-hidden w-full">
      <img
        src={modeCfg.heroImage}
        alt="New Arrivals"
        className="w-full h-[160px] sm:h-[200px] md:h-auto object-cover object-center md:object-contain block"
      />

      <div className="absolute left-3.5 top-1/2 -translate-y-1/2 max-w-[58%] md:left-12 md:max-w-none">
        <h1 className="font-cormorant font-medium text-[clamp(15px,4.5vw,22px)] md:text-[clamp(30px,4.2vw,50px)] text-[#133F30] m-0 mb-1 md:mb-2 leading-[1.15] md:leading-[1.35] max-w-[140px] md:max-w-[380px]">
          {modeCfg.title}
        </h1>

        <p className="font-cormorant text-[10px] sm:text-[12px] md:text-[14.5px] text-black m-0 font-light max-w-[130px] md:max-w-[380px] mb-2 md:mb-5 leading-[1.25] md:leading-normal">
          {modeCfg.sub}
        </p>

        <Link
          to="/new-arrivals"
          className="inline-block bg-[#08112C] text-white px-2.5 py-1.5 md:px-6 md:py-3 rounded-full font-serif text-[9px] sm:text-xs md:text-base leading-none"
        >
          Shop New Arrivals
        </Link>
      </div>
    </div>
  )}

</section>

      <div className="max-w-[1320px] mx-auto px-5 sm:px-10 lg:px-16 pt-3 pb-7 lg:py-10">
        {/* ── TOOLBAR: count + chips + sort ── */}
        <div className="flex items-center justify-between -mb-4 mt-6">
          {/* Mobile filter button */}
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
          <div className="hidden lg:flex items-center gap-2">
        <button
  type="button"
  onClick={() => setShowSidebar(!showSidebar)}
  className="inline-flex items-center gap-2 rounded-full border border-wline px-4 py-2.5 text-[13px] text-wink hover:border-wgreen"
>
  <SlidersHorizontal className="size-4" />
  Filter
</button>
</div>

          <p className="ml-auto text-[13.5px] text-wmuted m-0">
            {isLoading ? 'Loading…' : `Showing ${shown} of ${total.toLocaleString('en-IN')}`}
          </p>
</div>
          <div className="mt-5 mb-4 flex flex-wrap items-center gap-2">
            {chips.map((chip) => (
              <button
                key={chip.label}
                type="button"
                onClick={chip.clear}
                className="inline-flex items-center gap-1.5 rounded-full border border-wline bg-wcard px-3.5 py-1.5 text-[12px] text-wink cursor-pointer hover:border-wgreen transition-colors"
              >
                {chip.label}
                <X className="size-3 text-wmuted" aria-hidden="true" />
              </button>
            ))}
            {hasFilters && (
              <button
                type="button"
                onClick={() =>
                  update({
                    q: '',
                    categoryIds: [],
                    goals: [],
                    flavours: [],
                    minPrice: null,
                    maxPrice: null,
                    offers: [],
                    minRating: null,
                    inStock: false,
                  })
                }
                className="text-[12px] text-wmuted underline bg-transparent border-0 cursor-pointer hover:text-wink"
              >
                Clear all
              </button>
            )}
          

          <div className="ml-auto flex items-center gap-2.5 text-[13px] text-wmuted">
            <span className="hidden sm:inline">Sort by</span>
            <select
              value={filters.sortBy}
              onChange={(e) => update({ sortBy: e.target.value })}
              className="bg-wcard border border-wline rounded-full px-4 py-2.5 text-[13px] text-wink cursor-pointer focus:outline-none focus:border-wgreen transition-colors"
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* ── SIDEBAR + GRID ── */}
       <div
  className={cn(
    showSidebar
      ? "lg:grid lg:grid-cols-[260px_1fr] lg:gap-8 items-start"
      : ""
  )}
>
  {/* Desktop sidebar */}
  {showSidebar && (
    <aside className="hidden lg:block sticky top-24" aria-label="Product filters">
      {/* Search within listing */}
      <div className="relative mb-2">
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

      {sidebar}
    </aside>
  )}

          {/* Grid + states */}
          <div>
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
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3.5 lg:gap-[22px]">
                {Array.from({ length: 6 }).map((_, i) => (
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
                  {hasFilters
                    ? 'Nothing matched your filters. Try removing some.'
                    : 'The catalog is being stocked. Check back shortly.'}
                </p>
                {hasFilters && (
                  <Link
                    to={mode === 'all' ? '/products' : `/${mode}`}
                    className="inline-block bg-[#08112C] text-white no-underline rounded-full px-7 py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors"
                  >
                    Clear filters
                  </Link>
                )}
              </div>
            ) : gridBadge ? (
              <ProductGrid products={products} cols={3} badge={gridBadge} />
            ) : (
              /* /products: per-card "New" ribbon inside the freshness window */
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3 lg:gap-5">
                {products.map((p) => (
                  <ProductCardWithDerivedBadge key={p.id} product={p} />
                ))}
              </div>
            )}

            {/* ── PAGINATION ── */}
            {!isLoading && !isError && totalPages > 1 && (
              <nav
                className="mt-10 flex items-center justify-center gap-1.5"
                aria-label="Pagination"
              >
                <button
                  type="button"
                  disabled={filters.page <= 1}
                  onClick={() => update({ page: filters.page - 1 }, { resetPage: false })}
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
                      onClick={() => update({ page: pg }, { resetPage: false })}
                      aria-label={`Go to page ${pg}`}
                      aria-current={pg === filters.page ? 'page' : undefined}
                      className={cn(
                        'inline-flex h-9 w-9 items-center justify-center rounded-full border text-[13px] font-medium transition-colors',
                        pg === filters.page
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
                  disabled={filters.page >= totalPages}
                  onClick={() => update({ page: filters.page + 1 }, { resetPage: false })}
                  aria-label="Next page"
                  className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-wline bg-wcard text-wmuted transition-colors hover:border-wgreen hover:text-wgreen disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <ChevronRight className="size-4" aria-hidden="true" />
                </button>
              </nav>
            )}
          </div>
        </div>
      </div>

      {/* ── MOBILE FILTER DRAWER ── */}
      {filterOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close filters"
            className="absolute inset-0 cursor-default bg-wink/40 animate-dim"
            onClick={() => setFilterOpen(false)}
          />
          <div
            id="filter-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="filter-drawer-title"
            onKeyDown={(e) => {
              if (e.key === 'Escape') setFilterOpen(false);
            }}
            className="absolute inset-y-0 left-0 flex w-80 max-w-[calc(100vw-2.5rem)] flex-col bg-wcard shadow-xl animate-slidein"
          >
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

            <div className="flex-1 overflow-y-auto p-4">
              <div className="relative mb-4">
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
              {sidebar}
            </div>

            <div className="border-t border-wline px-4 py-4">
              <button
                type="button"
                className="w-full bg-[#08112C] text-white rounded-full py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors"
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

/* Card wrapper for /products: derive a "New" ribbon from created_at. */
function ProductCardWithDerivedBadge({ product }) {
  return <ProductCard product={product} badge={isNewProduct(product) ? 'new' : undefined} />;
}
