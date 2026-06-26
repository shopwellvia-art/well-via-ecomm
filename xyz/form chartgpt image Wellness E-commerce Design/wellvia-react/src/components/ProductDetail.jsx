import { Link } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { formatINR, PRODUCT_BENEFITS, PRODUCT_INGREDIENTS } from '../data/products';
import ImageSlot from './ImageSlot';
import { CheckCircle, LockIcon, LeafIcon } from './Icons';

/**
 * ProductDetail — full detail layout for a single product.
 */
export default function ProductDetail({ product }) {
  const { add, open } = useCart();
  const addToCart = () => { add(product.id); open(); };

  return (
    <main>
      <div className="px-5 sm:px-10 lg:px-16 pt-5 lg:pt-7 text-[11.5px] text-muted">
        <Link to="/" className="text-muted hover:text-green no-underline">Home</Link> &nbsp;/&nbsp;
        <Link to="/shop" className="text-muted hover:text-green no-underline"> All Rituals</Link> &nbsp;/&nbsp;
        <span className="text-ink"> {product.name}</span>
      </div>

      <section className="grid md:grid-cols-2 gap-7 lg:gap-[60px] items-center px-5 sm:px-10 lg:px-16 py-7 lg:py-12">
        <div>
          <span className="inline-block bg-gold/15 text-gold text-[10.5px] tracking-[0.16em] uppercase px-[13px] py-1.5 rounded-full mb-[18px]">
            Bestseller · Hormonal Care
          </span>
          <h1 className="font-serif font-medium text-[clamp(38px,5vw,62px)] leading-[1.02] m-0 mb-2">{product.name}</h1>
          <p className="font-serif italic text-[clamp(19px,2vw,24px)] text-gold m-0 mb-5">Balance, Made for Her</p>

          <div className="flex flex-wrap gap-2.5 mb-[22px]">
            {['Hormonal', 'Cycle Care', 'Vegan'].map((t) => (
              <span key={t} className="inline-flex items-center gap-1.5 border border-line rounded-full px-3.5 py-[7px] text-[12px] text-ink">
                <CheckCircle size={13} stroke="#183A2E" />
                {t}
              </span>
            ))}
          </div>

          <p className="text-[15.5px] leading-[1.72] text-muted max-w-[460px] m-0 mb-5 font-light">
            Clinically formulated with premium ingredients to balance hormones, regulate cycles,
            and support women's wellness. Enjoy the delicious blueberry taste — with no refined
            sugar added.
          </p>

          <div className="flex flex-wrap items-center gap-[18px] mb-[26px] text-[13px]">
            <span className="flex items-center gap-1.5">
              <span className="text-gold tracking-[1px]">★★★★★</span>
              <b className="font-medium">{product.rating.toFixed(1)}</b>
              <span className="text-muted">({product.reviews.toLocaleString('en-IN')})</span>
            </span>
            <span className="flex items-center gap-1.5 text-muted"><CheckCircle size={14} stroke="#183A2E" />Clinically Reviewed</span>
            <span className="flex items-center gap-1.5 text-muted"><CheckCircle size={14} stroke="#183A2E" />FSSAI Compliant</span>
          </div>

          <div className="flex flex-wrap items-center gap-3.5 mb-4">
            <button onClick={addToCart} className="flex items-center gap-[18px] bg-green text-white border-0 rounded-full px-[30px] py-[17px] text-[15px] tracking-wide cursor-pointer shadow-[0_16px_34px_-16px_rgba(24,58,46,0.7)] hover:bg-greenh">
              Add to Cart <span className="font-serif text-[19px]">{formatINR(product.price)}</span>
            </button>
          </div>

          <div className="flex items-center gap-2 text-[13px] text-muted">
            <LockIcon size={15} stroke="#183A2E" />
            Save 15% with subscription · Cancel anytime
          </div>
        </div>

        <div className="relative">
          <div className="absolute -inset-y-[4%] -right-[6%] w-[70%] h-[88%] z-0 rounded-full"
            style={{ background: 'radial-gradient(circle at 50% 45%, rgba(24,58,46,0.10), transparent 70%)' }} />
          <ImageSlot id="detail-main" shape="rounded" radius={24} placeholder="Drop the gummies pouch + botanical setup"
            className="w-full h-[clamp(380px,44vw,560px)] relative z-[1]" />
        </div>
      </section>

      {/* Benefits */}
      <section className="px-5 sm:px-10 lg:px-16 py-9 lg:py-[60px] bg-gradient-to-b from-[rgba(216,208,196,0.18)] to-transparent">
        <h2 className="font-serif font-medium text-[clamp(28px,3.4vw,42px)] text-center m-0 mb-7 lg:mb-11">Gentle Hormonal Balance, Naturally</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 lg:gap-[26px] max-w-[1080px] mx-auto">
          {PRODUCT_BENEFITS.map((b, i) => (
            <div key={i} className="bg-card border border-line rounded-xl2 px-[22px] py-[26px] text-center">
              <div className="w-[52px] h-[52px] rounded-full bg-gold/10 flex items-center justify-center mx-auto mb-4 text-green text-[22px]">{b.icon}</div>
              <h3 className="font-serif font-semibold text-[19px] m-0 mb-2">{b.title}</h3>
              <p className="text-[12.5px] leading-[1.6] text-muted m-0 font-light">{b.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Ingredients + science */}
      <section className="grid md:grid-cols-2 gap-7 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 py-10 lg:py-[68px]">
        <ImageSlot id="detail-ingredients" shape="rounded" radius={22} placeholder="Drop ingredients flat-lay (berries, herbs)"
          className="w-full h-[clamp(320px,38vw,440px)]" />
        <div>
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Clean Ingredients</div>
          <h2 className="font-serif font-medium text-[clamp(28px,3.4vw,44px)] leading-[1.08] m-0 mb-[22px]">Backed by Science.<br />Designed for Her.</h2>
          <div className="flex flex-col">
            {PRODUCT_INGREDIENTS.map((ing, i) => (
              <div key={i} className="flex items-start gap-3.5 py-[15px] border-b border-line">
                <span className="w-8 h-8 rounded-full border border-line flex items-center justify-center shrink-0 text-green"><LeafIcon size={15} /></span>
                <div>
                  <div className="text-[15px] text-ink mb-0.5">{ing.name}</div>
                  <div className="text-[12.5px] text-muted font-light">{ing.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Customer review */}
      <section className="px-5 sm:px-10 lg:px-16 py-9 lg:py-[60px]">
        <div className="max-w-[760px] mx-auto bg-green text-[#f3efe6] rounded-xl3 p-7 lg:p-[52px] text-center">
          <span className="text-gold tracking-[3px] text-[18px]">★★★★★</span>
          <p className="font-serif text-[clamp(22px,2.6vw,30px)] leading-[1.4] my-[18px] mb-[22px] italic">
            “My cycles have become so regular and my skin is clearer than ever. A gentle daily
            ritual that genuinely works.”
          </p>
          <div className="flex items-center justify-center gap-3">
            <ImageSlot id="review-avatar" shape="circle" placeholder="" className="w-[46px] h-[46px]" />
            <div className="text-left text-[13.5px]">
              <div>Aditi K.</div>
              <div className="text-[#f3efe6]/65 text-[12px]">Verified Buyer</div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
