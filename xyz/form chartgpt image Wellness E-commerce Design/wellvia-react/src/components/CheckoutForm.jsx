import { useState } from 'react';
import { formatINR } from '../data/products';

const inputCls =
  'w-full bg-bg border border-line rounded-xl px-[17px] py-[15px] text-[14px]';

const PAY_METHODS = ['Card', 'UPI', 'Net Banking'];
const BRANDS = [
  { label: 'Visa', color: '#1a1f71' },
  { label: 'Mastercard', color: '#eb6f25' },
  { label: 'Razorpay', color: '#0b6bff' },
  { label: 'UPI', color: '#0f7a3d' },
  { label: 'G Pay', color: '#4285f4' },
  { label: 'Amex', color: '#2e77bb' },
];

/**
 * CheckoutForm — controlled multi-step form (Shipping / Payment / Review).
 * `step` and `setStep` are owned by the parent CheckoutPage so the step
 * indicator stays in sync. `onPlaceOrder` fires on the final confirm.
 */
export default function CheckoutForm({ step, setStep, onPlaceOrder, total }) {
  const [form, setForm] = useState({
    name: '', email: '', address: '', postal: '', city: '', country: 'India',
    payMethod: 'Card', cardName: '', card: '', exp: '', cvv: '', upi: '', bank: 'HDFC Bank',
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const payMasked = form.card ? `Card ending ${form.card.slice(-4)}` : 'Card •••• ••••';
  const shipAddr = `${form.address || 'Street Address'}, ${form.city || 'City'} ${form.postal || ''}, ${form.country}`;

  return (
    <div className="bg-card border border-line rounded-xl3 p-6 lg:p-9">
      {/* STEP 1 — SHIPPING */}
      {step === 1 && (
        <>
          <h2 className="font-serif font-semibold text-[24px] m-0 mb-1">Shipping Details</h2>
          <p className="text-[13px] text-muted m-0 mb-[22px] font-light">Where should we send your ritual?</p>
          <div className="flex flex-col gap-3.5">
            <input value={form.name} onChange={set('name')} placeholder="Full Name" className={inputCls} />
            <input value={form.email} onChange={set('email')} placeholder="Email Address" className={inputCls} />
            <input value={form.address} onChange={set('address')} placeholder="Street Address" className={inputCls} />
            <div className="grid grid-cols-2 gap-3.5">
              <input value={form.postal} onChange={set('postal')} placeholder="Postal Code" className={inputCls} />
              <input value={form.city} onChange={set('city')} placeholder="City" className={inputCls} />
            </div>
            <select value={form.country} onChange={set('country')} className={`${inputCls} cursor-pointer`}>
              <option>India</option><option>United States</option><option>United Kingdom</option><option>United Arab Emirates</option>
            </select>
          </div>
          <button onClick={() => setStep(2)} className="w-full bg-green text-white border-0 rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer mt-[22px] hover:bg-greenh">
            Continue to Payment
          </button>
        </>
      )}

      {/* STEP 2 — PAYMENT */}
      {step === 2 && (
        <>
          <h2 className="font-serif font-semibold text-[24px] m-0 mb-1">Payment</h2>
          <p className="text-[13px] text-muted m-0 mb-[18px] font-light">Choose how you'd like to pay. All transactions are encrypted &amp; secure.</p>
          <div className="flex gap-2.5 mb-5 flex-wrap">
            {PAY_METHODS.map((m) => (
              <button key={m} onClick={() => setForm((f) => ({ ...f, payMethod: m }))}
                className={`rounded-full px-[18px] py-[9px] text-[12.5px] tracking-wide cursor-pointer whitespace-nowrap border ${form.payMethod === m ? 'bg-green text-white border-green' : 'bg-transparent text-ink border-line'}`}>
                {m}
              </button>
            ))}
          </div>

          {form.payMethod === 'Card' && (
            <div className="flex flex-col gap-3.5">
              <input value={form.cardName} onChange={set('cardName')} placeholder="Name on Card" className={inputCls} />
              <input value={form.card} onChange={set('card')} placeholder="Card Number" className={inputCls} />
              <div className="grid grid-cols-2 gap-3.5">
                <input value={form.exp} onChange={set('exp')} placeholder="MM / YY" className={inputCls} />
                <input value={form.cvv} onChange={set('cvv')} placeholder="CVV" className={inputCls} />
              </div>
            </div>
          )}
          {form.payMethod === 'UPI' && (
            <>
              <input value={form.upi} onChange={set('upi')} placeholder="yourname@bank" className={inputCls} />
              <p className="text-[12px] text-muted mt-2.5 px-0.5 font-light">You'll receive a collect request on your UPI app to approve the payment.</p>
            </>
          )}
          {form.payMethod === 'Net Banking' && (
            <select value={form.bank} onChange={set('bank')} className={`${inputCls} cursor-pointer`}>
              <option>HDFC Bank</option><option>ICICI Bank</option><option>State Bank of India</option><option>Axis Bank</option><option>Kotak Mahindra</option>
            </select>
          )}

          <div className="text-[10.5px] tracking-[0.16em] uppercase text-muted mt-[22px] mb-[11px]">We Accept</div>
          <div className="flex items-center gap-2.5 flex-wrap">
            {BRANDS.map((b) => (
              <span key={b.label} className="inline-flex items-center gap-1.5 border border-line rounded-lg px-3 py-[7px] text-[11.5px] bg-white text-ink">
                <span className="w-[9px] h-[9px] rounded-sm" style={{ background: b.color }} />
                {b.label}
              </span>
            ))}
          </div>

          <div className="flex gap-3 mt-6">
            <button onClick={() => setStep(1)} className="shrink-0 bg-transparent border border-line text-ink rounded-full px-6 py-4 text-[14px] cursor-pointer">Back</button>
            <button onClick={() => setStep(3)} className="flex-1 bg-green text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-greenh">Review Order</button>
          </div>
        </>
      )}

      {/* STEP 3 — REVIEW */}
      {step === 3 && (
        <>
          <h2 className="font-serif font-semibold text-[24px] m-0 mb-[22px]">Review &amp; Confirm</h2>
          <div className="flex flex-col gap-4">
            <div className="bg-bg border border-line rounded-2xl p-[18px]">
              <div className="text-[11px] tracking-[0.14em] uppercase text-gold mb-2">Shipping To</div>
              <div className="text-[14px] leading-[1.6] text-ink">{form.name || 'Your Name'}<br />{shipAddr}</div>
            </div>
            <div className="bg-bg border border-line rounded-2xl p-[18px]">
              <div className="text-[11px] tracking-[0.14em] uppercase text-gold mb-2">Payment</div>
              <div className="text-[14px] text-ink">{form.payMethod === 'Card' ? payMasked : form.payMethod}</div>
            </div>
          </div>
          <div className="flex gap-3 mt-[22px]">
            <button onClick={() => setStep(2)} className="shrink-0 bg-transparent border border-line text-ink rounded-full px-6 py-4 text-[14px] cursor-pointer">Back</button>
            <button onClick={onPlaceOrder} className="flex-1 bg-green text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-greenh">
              Place Order · {formatINR(total)}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
