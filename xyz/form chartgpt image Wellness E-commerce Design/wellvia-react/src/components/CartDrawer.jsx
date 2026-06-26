import { useNavigate } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { formatINR } from '../data/products';
import ImageSlot from './ImageSlot';
import { CloseIcon, LockIcon } from './Icons';
import { LeafMark } from './Logo';

/**
 * CartDrawer — slide-in cart panel. Reads from CartContext; always mounted,
 * shown when cart.isOpen is true.
 */
export default function CartDrawer() {
  const { isOpen, close, lines, subtotal, inc, dec, remove } = useCart();
  const navigate = useNavigate();
  if (!isOpen) return null;

  const goCheckout = () => { close(); navigate('/checkout'); };
  const goShop = () => { close(); navigate('/shop'); };

  return (
    <>
      <div onClick={close} className="fixed inset-0 bg-[rgba(30,24,14,0.34)] z-[90] animate-dim" />
      <aside className="fixed top-0 right-0 h-full w-full sm:w-[420px] max-w-full bg-bg z-[91] flex flex-col shadow-[-20px_0_60px_-20px_rgba(40,30,10,0.4)] animate-slidein">
        <div className="px-6 py-[22px] border-b border-line flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <LeafMark size={22} dot={false} />
            <span className="font-display tracking-[0.18em] text-[15px] text-green">YOUR CART</span>
          </div>
          <button onClick={close} className="bg-transparent border-0 p-0 cursor-pointer text-ink" aria-label="Close cart">
            <CloseIcon size={22} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {lines.length === 0 ? (
            <div className="text-center text-muted py-[50px] text-[14px]">
              Your cart is empty.
              <br />
              <button onClick={goShop} className="mt-4 bg-green text-white border-0 rounded-full px-[26px] py-3 text-[13px] cursor-pointer">Shop Rituals</button>
            </div>
          ) : (
            <div className="flex flex-col gap-[18px]">
              {lines.map((l) => (
                <div key={l.id} className="flex gap-3.5 pb-[18px] border-b border-line">
                  <ImageSlot id={`prod-${l.id}`} shape="rounded" radius={12} className="w-[74px] h-[88px] shrink-0 border border-line" />
                  <div className="flex-1 flex flex-col">
                    <div className="flex justify-between gap-2">
                      <span className="font-serif text-[17px] leading-tight">{l.name}</span>
                      <button onClick={() => remove(l.id)} className="bg-transparent border-0 p-0 cursor-pointer text-muted shrink-0" aria-label="Remove">
                        <CloseIcon size={16} stroke="#6F6A60" />
                      </button>
                    </div>
                    <span className="text-[12px] text-muted mt-0.5 mb-3">{l.flavor}</span>
                    <div className="flex items-center justify-between mt-auto">
                      <div className="flex items-center border border-line rounded-full overflow-hidden">
                        <button onClick={() => dec(l.id)} className="bg-transparent border-0 px-[13px] py-[7px] text-[15px] cursor-pointer text-ink">−</button>
                        <span className="text-[13px] min-w-[20px] text-center">{l.qty}</span>
                        <button onClick={() => inc(l.id)} className="bg-transparent border-0 px-[13px] py-[7px] text-[15px] cursor-pointer text-ink">+</button>
                      </div>
                      <span className="font-serif text-[18px]">{formatINR(l.lineTotal)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="px-6 py-5 border-t border-line bg-card">
          <div className="flex items-center justify-between mb-4">
            <span className="text-[15px] text-ink">Subtotal</span>
            <span className="font-serif text-[24px]">{formatINR(subtotal)}</span>
          </div>
          <button onClick={goCheckout} className="w-full bg-green text-white border-0 rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer hover:bg-greenh">
            Proceed to Checkout
          </button>
          <div className="flex items-center justify-center gap-1.5 my-3.5 mb-2.5 text-[12px] text-muted">
            <LockIcon size={13} stroke="#B49A63" />
            Checkout is encrypted &amp; secure
          </div>
          <div className="text-center">
            <button onClick={close} className="bg-transparent border-0 text-[13px] text-green underline underline-offset-[3px] cursor-pointer">Continue Shopping</button>
          </div>
        </div>
      </aside>
    </>
  );
}
