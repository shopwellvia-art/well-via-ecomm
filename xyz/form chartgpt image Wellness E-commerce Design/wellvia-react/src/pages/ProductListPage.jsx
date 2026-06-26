import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { PRODUCTS, CATEGORIES } from '../data/products';
import ProductGrid from '../components/ProductGrid';
import Footer from '../components/Footer';

const SORTS = ['Featured', 'Price: Low to High', 'Price: High to Low', 'Top Rated'];

export default function ProductListPage() {
  const [category, setCategory] = useState('All');
  const [sort, setSort] = useState('Featured');

  const products = useMemo(() => {
    let list = PRODUCTS.filter((p) => category === 'All' || p.cat === category);
    if (sort === 'Price: Low to High') list = [...list].sort((a, b) => a.price - b.price);
    else if (sort === 'Price: High to Low') list = [...list].sort((a, b) => b.price - a.price);
    else if (sort === 'Top Rated') list = [...list].sort((a, b) => b.rating - a.rating);
    return list;
  }, [category, sort]);

  return (
    <main className="px-5 sm:px-10 lg:px-16 py-7 lg:py-[52px]">
      <div className="text-[11.5px] text-muted tracking-wide mb-3.5">
        <Link to="/" className="text-muted hover:text-green no-underline">Home</Link> &nbsp;/&nbsp; <span className="text-ink">All Rituals</span>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-4 mb-6 lg:mb-9">
        <div>
          <h1 className="font-serif font-medium text-[clamp(34px,4.4vw,54px)] m-0 mb-1.5">Shop All Rituals</h1>
          <p className="text-[14px] text-muted m-0 font-light">{products.length} products</p>
        </div>
        <div className="flex items-center gap-2.5 text-[13px] text-muted">
          <span>Sort</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="bg-card border border-line rounded-full px-4 py-2.5 text-[13px] text-ink cursor-pointer">
            {SORTS.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap gap-2.5 mb-6 lg:mb-9">
        {CATEGORIES.map((c) => (
          <button key={c} onClick={() => setCategory(c)}
            className={`rounded-full px-5 py-[9px] text-[12.5px] tracking-wide cursor-pointer transition-all border ${category === c ? 'bg-green text-white border-green' : 'bg-transparent text-ink border-line'}`}>
            {c}
          </button>
        ))}
      </div>

      <ProductGrid products={products} showCategory />

      <Footer />
    </main>
  );
}
