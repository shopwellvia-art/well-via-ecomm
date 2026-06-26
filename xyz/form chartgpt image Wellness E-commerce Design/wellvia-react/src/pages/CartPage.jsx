import { Link, useNavigate } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { formatINR } from '../data/products';
import ImageSlot from '../components/ImageSlot';
import Footer from '../components/Footer';
import { LockIcon, CloseIcon } from '../components/Icons';

/**
 * CartPage — full-page cart (alternative to the slide-in drawer).
 */
export default function CartPage() {
  const { lines, subtotal, inc, dec, remove } = useCart();
  const navigate = useNavigate();

  if (lines.length === 0) {
    return (
      <main className="px-5 sm:px-10 lg:px-16 py-16 text-center min-h-[60vh]">
        <h1 className="font-serif font-medium text-[clamp(32px,4vw,48px)] m-0 mb-3">Your Cart</h1>
        <p className="text-muted mb-6 font-light">Your cart is empty.</p>
        <Link to="/shop" className="inline-block bg-green text-white no-underline rounded-full px-8 py-3.5 text-[14px] cursor-pointer hover:bg-greenh">Shop Rituals</Link>
      </main>
    );
  }

  return (
    <main className="px-5 sm:px-10 lg:px-16 py-7 lg:py-[52px]">
      <h1 className="font-serif font-medium text-[clamp(32px,4vw,48px)] m-0 mb-7">Your Cart</h1>
      <div className="grid lg:grid-cols-[1.5fr_0.9fr] gap-6 lg:gap-10 items-start">
        <div className="flex flex-col gap-4">
          {lines.map((l) => (
            <div key={l.id} className="bg-card border border-line rounded-xl2 p-4 flex gap-4 items-center">
              <ImageSlot id={`prod-${l.id}`} shape="rounded" radius={12} className="w-[88px] h-[104px] shrink-0 border border-line" />
              <div className="flex-1">
                <div className="flex justify-between gap-3">
                  <span className="font-serif text-[20px] leading-tight">{l.name}</span>
                  <button onClick={() => remove(l.id)} className="bg-transparent border-0 p-0 cursor-pointer text-muted shrink-0" aria-label="Remove"><CloseIcon size={16} stroke="#6F6A60" /></button>
                </div>
                <div className="text-[12.5px] text-muted mt-0.5 mb-3">{l.flavor}</div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center border border-line rounded-full overflow-hidden">
                    <button onClick={() => dec(l.id)} className="bg-transparent border-0 px-3.5 py-2 text-[15px] cursor-pointer text-ink">−</button>
                    <span className="text-[13px] min-w-[22px] text-center">{l.qty}</span>
                    <button onClick={() => inc(l.id)} className="bg-transparent border-0 px-3.5 py-2 text-[15px] cursor-pointer text-ink">+</button>
                  </div>
                  <span className="font-serif text-[20px]">{formatINR(l.lineTotal)}</span>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="bg-card border border-line rounded-xl3 p-6 lg:sticky lg:top-[90px]">
          <h2 className="font-serif font-semibold text-[20px] m-0 mb-4">Order Summary</h2>
          <div className="flex flex-col gap-2.5 text-[13.5px] text-muted border-t border-line pt-4">
            <div className="flex justify-between"><span>Subtotal</span><span className="text-ink">{formatINR(subtotal)}</span></div>
            <div className="flex justify-between"><span>Shipping</span><span className="text-green">Free</span></div>
            <div className="flex justify-between items-center border-t border-line pt-3 mt-1">
              <span className="text-[15px] text-ink">Total</span>
              <span className="font-serif text-[24px] text-ink">{formatINR(subtotal)}</span>
            </div>
          </div>
          <button onClick={() => navigate('/checkout')} className="w-full bg-green text-white border-0 rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer mt-5 hover:bg-greenh">
            Proceed to Checkout
          </button>
          <div className="flex items-center justify-center gap-1.5 mt-3.5 text-[12px] text-muted">
            <LockIcon size={13} stroke="#B49A63" /> Checkout is encrypted &amp; secure
          </div>
          <div className="text-center mt-3">
            <Link to="/shop" className="text-[13px] text-green underline underline-offset-[3px] no-underline">Continue Shopping</Link>
          </div>
        </div>
      </div>
      <Footer />
    </main>
  );
}
