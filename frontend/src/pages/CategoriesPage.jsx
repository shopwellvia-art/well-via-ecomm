import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Page } from '@/components/layout/Page.jsx';
import ProductGrid from '@/components/storefront/ProductGrid.jsx';
import WImage from '@/components/storefront/WImage.jsx';
import { useCategories } from '@/features/categories/hooks.js';
import { useProducts } from '@/features/products/hooks.js';
import { cn } from '@/lib/utils.js';

const TABS = [
  { key: 'categories', label: 'All Categories' },
  { key: 'combos', label: 'Combo Packs' },
];

/**
 * Categories landing page (mockup: "Your Perfect Wellness Bundle") —
 * hero band + tab switch between the category grid and combo-pack products.
 */
export default function CategoriesPage() {
  const [tab, setTab] = useState('categories');
  const { data: categories = [], isLoading: catsLoading } = useCategories();
  const combosQuery = useProducts(tab === 'combos' ? { is_combo: true, page_size: 24 } : undefined);
  const combos = combosQuery.data?.items ?? [];

  return (
   <Page bleed>
  {/* ── HERO ── */}
<section className="relative overflow-hidden border-b border-wline pt-20 sm:pt-36 h-[180px] sm:min-h-[420px]">
    <img
      src="/hero-categories.png"
      alt=""
      className="absolute inset-0 w-full h-full object-cover object-center"
    />

    <div className="absolute inset-0 bg-black/20" />

<div className="relative z-10 max-w-[1320px] mx-auto px-5 sm:px-10 lg:px-16 pt-0 sm:pt-10 pb-10 sm:pb-20 -translate-y-10 sm:translate-y-0">

  <h1 className="font-cormorant font-semibold text-black text-[22px] sm:text-[clamp(30px,4.2vw,50px)] leading-[1.1] m-0 mb-1 sm:mb-2 max-w-[180px] sm:max-w-xl">
    Your Perfect Wellness Bundle.
  </h1>

  <p className="font-outfit text-black text-[11px] sm:text-[14.5px] font-light leading-[1.4] m-0 max-w-[160px] sm:max-w-md">
    Curated bundles that offer more benefits at better value.
  </p>

  <Link
    to="/products?offers=combo"
    className="mt-3 sm:mt-5 inline-block rounded-full px-4 sm:px-6 py-2 sm:py-3 text-[10px] sm:text-[13px] tracking-wide text-white no-underline transition-colors"
    style={{ backgroundColor: "#08112C" }}
  >
    Shop your combos →
  </Link>

</div>
  </section>
      <div className="max-w-[1320px] mx-auto px-5 sm:px-10 lg:px-16 py-8 lg:py-12">
        {/* ── TABS ── */}
        <div
          role="tablist"
          aria-label="Browse by"
          className="grid grid-cols-2 border-b border-wline mb-8 lg:mb-10"
        >
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                'bg-transparent border-0 cursor-pointer pb-3.5 font-wserif text-[clamp(18px,2.2vw,24px)] transition-colors relative',
                tab === t.key ? 'text-wink' : 'text-wmuted hover:text-wink',
              )}
            >
              {t.label}
              {tab === t.key && (
                <span className="absolute -bottom-px left-1/4 right-1/4 h-[2.5px] bg-wgreen rounded-full" />
              )}
            </button>
          ))}
        </div>

        {/* ── ALL CATEGORIES ── */}
        {tab === 'categories' &&
          (catsLoading ? (
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-5">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-[280px] bg-wcard border border-wline rounded-xl2 animate-pulse" />
              ))}
            </div>
          ) : categories.length === 0 ? (
            <p className="py-16 text-center text-wmuted text-[14px]">
              Categories are being set up. Check back shortly.
            </p>
          ) : (
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-5">
              {categories.map((c) => (
                <Link
                  key={c.id}
                  to={`/products?category_ids=${c.id}`}
                  className="group bg-wcard border border-wline rounded-xl2 overflow-hidden no-underline transition-transform duration-200 hover:-translate-y-[4px] hover:shadow-[0_24px_50px_-28px_rgba(40,30,10,0.42)]"
                >
                  <div
                    className="relative"
                    style={{ background: 'linear-gradient(160deg,#efe9df,#e4dccd)' }}
                  >
                    <WImage src={c.image_url} alt="" className="w-full h-[220px]" />
                  </div>
                  <div className="p-4 text-center">
                    <p className="font-wserif text-[19px] text-wink m-0 group-hover:text-wgreen transition-colors">
                      {c.name}
                    </p>
                  </div>
                </Link>
              ))}
            </div>
          ))}

        {/* ── COMBO PACKS ── */}
        {tab === 'combos' &&
          (combosQuery.isLoading ? (
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-5">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-[380px] bg-wcard border border-wline rounded-xl2 animate-pulse" />
              ))}
            </div>
          ) : combos.length === 0 ? (
            <p className="py-16 text-center text-wmuted text-[14px]">
              Combo packs are coming soon.
            </p>
          ) : (
            <ProductGrid products={combos} cols={3} />
          ))}
      </div>
    </Page>
  );
}
