import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { formatINR } from '../data/products';
import StepIndicator from '../components/StepIndicator';
import CheckoutForm from '../components/CheckoutForm';
import ImageSlot from '../components/ImageSlot';
import { Check } from '../components/Icons';

const CHECKOUT_TRUST = ['Free Delivery', 'Secured Checkout', 'Customer Support'];

export default function CheckoutPage() {
  const { lines, subtotal } = useCart();
  const [step, setStep] = useState(1);
  const [placed, setPlaced] = useState(false);
  const navigate = useNavigate();

  if (placed) {
    return (
      <main className="px-5 py-16 flex justify-center">
        <div className="max-w-[560px] w-full text-center bg-card border border-line rounded-xl3 p-9 lg:p-14">
          <div className="w-[70px] h-[70px] rounded-full bg-green flex items-center justify-center mx-auto mb-5">
            <Check size={34} stroke="#fff" strokeWidth={1.6} />
          </div>
          <h1 className="font-serif font-medium text-[clamp(30px,4vw,44px)] m-0 mb-3">Thank You</h1>
          <p className="text-[15px] text-muted leading-[1.6] m-0 mb-7 font-light">
            Your ritual is on its way. A confirmation has been sent to your email — we can't wait
            for you to feel the difference.
          </p>
          <button onClick={() => navigate('/')} className="bg-green text-white border-0 rounded-full px-9 py-3.5 text-[14px] cursor-pointer hover:bg-greenh">Continue Shopping</button>
        </div>
      </main>
    );
  }

  return (
    <main className="px-5 sm:px-10 lg:px-16 py-7 lg:py-12 pb-16 lg:pb-20">
      <div className="max-w-[1080px] mx-auto">
        <h1 className="font-serif font-medium text-[clamp(32px,4vw,48px)] text-center m-0 mb-7">Secure Checkout</h1>
        <StepIndicator step={step} onStep={setStep} />

        <div className="grid lg:grid-cols-[1.4fr_0.9fr] gap-6 lg:gap-10 items-start">
          <CheckoutForm step={step} setStep={setStep} total={subtotal} onPlaceOrder={() => { setPlaced(true); window.scrollTo(0, 0); }} />

          {/* Order summary */}
          <div className="bg-card border border-line rounded-xl3 p-6 lg:sticky lg:top-[90px]">
            <h3 className="font-serif font-semibold text-[20px] m-0 mb-4">Order Summary</h3>
            <div className="flex flex-col gap-3.5 mb-4">
              {lines.map((l) => (
                <div key={l.id} className="flex gap-3 items-center">
                  <ImageSlot id={`prod-${l.id}`} shape="rounded" radius={10} className="w-[54px] h-[62px] shrink-0 border border-line" />
                  <div className="flex-1">
                    <div className="text-[14px] leading-tight">{l.name}</div>
                    <div className="text-[11.5px] text-muted">{l.flavor} · Qty {l.qty}</div>
                  </div>
                  <div className="font-serif text-[16px]">{formatINR(l.lineTotal)}</div>
                </div>
              ))}
            </div>
            <div className="border-t border-line pt-4 flex flex-col gap-2.5 text-[13.5px] text-muted">
              <div className="flex justify-between"><span>Subtotal</span><span className="text-ink">{formatINR(subtotal)}</span></div>
              <div className="flex justify-between"><span>Shipping</span><span className="text-green">Free</span></div>
              <div className="flex justify-between items-center border-t border-line pt-3 mt-1">
                <span className="text-[15px] text-ink">Total</span>
                <span className="font-serif text-[24px] text-ink">{formatINR(subtotal)}</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-3 justify-center mt-5 pt-[18px] border-t border-line">
              {CHECKOUT_TRUST.map((t) => (
                <div key={t} className="flex items-center gap-1.5 text-[11px] text-muted"><span className="text-green">✓</span>{t}</div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
