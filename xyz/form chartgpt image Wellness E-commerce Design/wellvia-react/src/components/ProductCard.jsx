import { Link } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { formatINR } from '../data/products';
import ImageSlot from './ImageSlot';

/**
 * ProductCard — used in grids across Home, Shop, Wishlist.
 * Pass a `product` from data/products.js.
 */
export default function ProductCard({ product, buttonLabel = 'Build This Ritual', showCategory = false }) {
  const { addAndOpen } = useCart();

  return (
    <article className="bg-card border border-line rounded-xl2 overflow-hidden flex flex-col transition-transform duration-200 hover:-translate-y-[5px] hover:shadow-[0_24px_50px_-28px_rgba(40,30,10,0.42)]">
      <Link to={`/product/${product.slug}`} className="relative block" style={{ background: 'linear-gradient(160deg,#efe9df,#e4dccd)' }}>
        <ImageSlot id={`prod-${product.id}`} placeholder="Drop product photo" className="w-full h-[200px]" />
        <span className="absolute top-3 left-3 bg-bg/90 text-green text-[9.5px] tracking-[0.14em] uppercase px-[11px] py-[5px] rounded-full border border-line">
          {product.tag}
        </span>
      </Link>
      <div className="p-[18px] pb-5 flex flex-col flex-1">
        <div className="flex items-center gap-1.5 text-[11.5px] text-gold mb-[7px]">
          <span className="tracking-[0.5px]">★★★★★</span>
          <span className="text-muted">{product.rating.toFixed(1)}</span>
        </div>
        <Link to={`/product/${product.slug}`} className="font-serif font-semibold text-[21px] m-0 mb-[5px] leading-[1.15] no-underline text-ink">
          {product.name}
        </Link>
        <p className="text-[12.5px] text-muted leading-[1.5] m-0 mb-3.5 font-light flex-1">{product.sub}</p>
        <div className="flex items-center justify-between mb-[13px]">
          <span className="font-serif text-[20px] text-ink">{formatINR(product.price)}</span>
          {showCategory && (
            <span className="text-[10.5px] tracking-[0.1em] uppercase text-muted">{product.cat}</span>
          )}
        </div>
        <button
          onClick={() => addAndOpen(product.id)}
          className="w-full bg-green text-white border-0 rounded-full py-3 text-[12.5px] tracking-wide cursor-pointer hover:bg-greenh"
        >
          {buttonLabel}
        </button>
      </div>
    </article>
  );
}
