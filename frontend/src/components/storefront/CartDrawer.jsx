import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { BadgePercent, MapPin, ShieldCheck } from 'lucide-react';
import {
  useCart,
  useApplyCoupon,
  useRemoveCoupon,
} from '@/features/cart/hooks';
import { useCartDrawer } from '@/features/cart/drawerStore';
import { useAuthStore } from '@/features/auth/store';
import { useServiceability } from '@/features/shipping/hooks.js';
import { readSavedPincode, saveSavedPincode } from '@/features/shipping/storage.js';
import CartItemRow from '@/features/cart/components/CartItemRow.jsx';
import { formatPrice } from '@/lib/utils';
import { CloseIcon } from './Icons';

const PAYMENT_MARKS = ['VISA', 'MasterCard', 'RuPay', 'UPI', 'COD'];

/**
 * CartDrawer — slide-in cart panel (mockup: "Your Cart items (N)").
 *
 * Always mounted in Layout; renders null when closed so the CSS entry
 * animations (animate-dim / animate-slidein) fire fresh on each open.
 *
 * Works for guests too — useCart() composes a client-side cart when signed
 * out. Coupons stay signed-in only (server carts).
 *
 * Sections: savings banner → item cards → coupon box → delivery pincode/ETA
 * → payment marks → sticky total + Proceed to Checkout.
 */
export default function CartDrawer() {
  const navigate = useNavigate();
  const { isOpen, closeDrawer } = useCartDrawer();
  const token = useAuthStore((s) => s.accessToken);
  const { data: cartData, isLoading } = useCart();
  const applyCoupon = useApplyCoupon();
  const removeCoupon = useRemoveCoupon();

  const [couponInput, setCouponInput] = useState('');
  const [couponError, setCouponError] = useState(null);

  const [pincode, setPincode] = useState(readSavedPincode());
  const [editingPin, setEditingPin] = useState(!readSavedPincode());
  const [pinInput, setPinInput] = useState('');
  const serviceability = useServiceability(pincode);

  if (!isOpen) return null;

  const items = cartData?.items ?? [];
  const total = cartData?.total ?? 0;
  const count = items.reduce((s, i) => s + (i.quantity || 0), 0);

  // Σ MRP savings across lines (+ coupon discount when applied).
  const mrpSavings = items.reduce((s, i) => {
    const mrp = i.compare_at_price != null ? Number(i.compare_at_price) : null;
    return mrp && mrp > Number(i.unit_price)
      ? s + (mrp - Number(i.unit_price)) * i.quantity
      : s;
  }, 0);
  const savings = mrpSavings + Number(cartData?.discount_amount ?? 0);

  const etaDays = serviceability.data?.eta_days_max ?? serviceability.data?.eta_days_min;
  const etaDate =
    etaDays != null ? new Date(Date.now() + etaDays * 86_400_000) : null;
  const etaLabel = etaDate
    ? `Delivery by ${etaDate.toLocaleDateString('en-IN', {
        day: 'numeric',
        month: 'long',
      })}, ${etaDate.toLocaleDateString('en-IN', { weekday: 'long' })}`
    : null;

  function submitCoupon(e) {
    e.preventDefault();
    const code = couponInput.trim();
    if (!code) return;
    setCouponError(null);
    applyCoupon.mutate(code, {
      onSuccess: () => setCouponInput(''),
      onError: (err) =>
        setCouponError(
          err?.response?.data?.error?.message || 'This code can’t be applied.',
        ),
    });
  }

  function submitPincode(e) {
    e.preventDefault();
    const clean = pinInput.trim();
    if (!/^\d{6}$/.test(clean)) return;
    setPincode(clean);
    saveSavedPincode(clean);
    setEditingPin(false);
  }

  function goCheckout() {
    closeDrawer();
    navigate('/checkout');
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={closeDrawer}
        className="fixed inset-0 bg-wink/30 z-[90] animate-dim"
        aria-hidden="true"
      />

      {/* Panel */}
      <aside
        className="fixed top-0 right-0 h-full w-full sm:w-[430px] max-w-full bg-wpaper z-[91] flex flex-col shadow-[-20px_0_60px_-20px_rgba(40,30,10,0.4)] animate-slidein"
        role="dialog"
        aria-modal="true"
        aria-label="Shopping cart"
      >
        {/* Header */}
        <div className="px-5 py-[18px] border-b border-wline flex items-center justify-between shrink-0 bg-wcard">
          <span className="font-wserif text-[20px] font-semibold text-wink">
            Your Cart items ({count})
          </span>
          <button
            onClick={closeDrawer}
            className="bg-transparent border-0 p-0 cursor-pointer text-wmuted hover:text-wink transition-colors"
            aria-label="Close cart"
          >
            <CloseIcon size={22} />
          </button>
        </div>

        {/* Savings banner */}
        {savings > 0 && (
          <div className="bg-[#5d7a63] text-white text-[13px] px-5 py-2.5 flex items-center gap-2 shrink-0">
            <BadgePercent className="size-4 shrink-0" aria-hidden="true" />
            You save {formatPrice(savings)} on this order!
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {/* Empty */}
          {!isLoading && items.length === 0 && (
            <div className="text-center text-wmuted py-[60px] text-[14px]">
              <p className="mb-4">Your cart is empty.</p>
              <button
                onClick={() => {
                  closeDrawer();
                  navigate('/products');
                }}
                className="bg-wgreen text-white border-0 rounded-full px-[26px] py-3 text-[13px] cursor-pointer hover:bg-wgreen-dark transition-colors"
              >
                Shop Now
              </button>
            </div>
          )}

          {/* Loading skeleton */}
          {isLoading && (
            <div className="flex flex-col gap-4 pt-2">
              {[1, 2].map((n) => (
                <div key={n} className="flex gap-3 animate-pulse">
                  <div className="w-[72px] h-[84px] shrink-0 rounded-xl2 bg-wline/50" />
                  <div className="flex-1 space-y-2 pt-1">
                    <div className="h-4 bg-wline/50 rounded-full w-3/4" />
                    <div className="h-3 bg-wline/50 rounded-full w-1/2" />
                    <div className="h-8 bg-wline/50 rounded-full w-2/3 mt-3" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Item cards */}
          {!isLoading && items.length > 0 && (
            <div className="flex flex-col gap-3">
              {items.map((item) => (
                <CartItemRow key={item.product_id} item={item} />
              ))}
            </div>
          )}

          {items.length > 0 && (
            <>
              {/* Coupons & Offers */}
              <div className="rounded-xl2 border border-wline bg-wcard p-4">
                <div className="flex items-center gap-2 mb-1">
                  <BadgePercent className="size-4 text-wgreen" aria-hidden="true" />
                  <span className="text-[14px] font-medium text-wink">
                    Coupons &amp; Offers
                  </span>
                </div>
                <p className="text-[12px] text-wmuted m-0 mb-3">
                  Save more with coupons and offers
                </p>

                {!token ? (
                  <Link
                    to="/login?next=/cart"
                    onClick={closeDrawer}
                    className="text-[13px] text-wgreen underline"
                  >
                    Log in to apply coupons
                  </Link>
                ) : cartData?.coupon_code ? (
                  <div className="flex items-center justify-between rounded-lg border border-wgreen/40 bg-wgreen/5 px-3 py-2">
                    <span className="text-[13px] text-wgreen font-medium">
                      {cartData.coupon_code} applied
                      {Number(cartData.discount_amount) > 0 &&
                        ` — ${formatPrice(cartData.discount_amount)} off`}
                    </span>
                    <button
                      onClick={() => removeCoupon.mutate()}
                      className="bg-transparent border-0 cursor-pointer text-[12px] text-wmuted underline hover:text-wink"
                    >
                      Remove
                    </button>
                  </div>
                ) : (
                  <form onSubmit={submitCoupon} className="flex gap-2">
                    <input
                      value={couponInput}
                      onChange={(e) => setCouponInput(e.target.value)}
                      placeholder="Enter the coupon code"
                      className="flex-1 min-w-0 rounded-lg border border-wline bg-wpaper px-3 py-2 text-[13px] text-wink placeholder:text-wmuted"
                      aria-label="Coupon code"
                    />
                    <button
                      type="submit"
                      disabled={applyCoupon.isPending}
                      className="rounded-lg bg-wmuted/80 hover:bg-wgreen text-white text-[12.5px] px-3.5 py-2 border-0 cursor-pointer transition-colors disabled:opacity-60"
                    >
                      {applyCoupon.isPending ? 'Applying…' : 'Apply Code'}
                    </button>
                  </form>
                )}
                {couponError && (
                  <p className="text-[12px] text-red-600 m-0 mt-2">{couponError}</p>
                )}
              </div>

              {/* Delivery pincode + ETA */}
              <div className="rounded-xl2 border border-wline bg-wcard p-4">
                {editingPin || !pincode ? (
                  <form onSubmit={submitPincode} className="flex items-center gap-2">
                    <MapPin className="size-4 text-wgreen shrink-0" aria-hidden="true" />
                    <input
                      value={pinInput}
                      onChange={(e) => setPinInput(e.target.value.replace(/\D/g, '').slice(0, 6))}
                      placeholder="Enter delivery pincode"
                      inputMode="numeric"
                      className="flex-1 min-w-0 rounded-lg border border-wline bg-wpaper px-3 py-2 text-[13px] text-wink placeholder:text-wmuted"
                      aria-label="Delivery pincode"
                    />
                    <button
                      type="submit"
                      className="rounded-lg bg-wgreen text-white text-[12.5px] px-3.5 py-2 border-0 cursor-pointer hover:bg-wgreen-dark transition-colors"
                    >
                      Check
                    </button>
                  </form>
                ) : (
                  <>
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-[13.5px] text-wink">
                        <MapPin className="size-4 text-wgreen shrink-0" aria-hidden="true" />
                        Delivery for pincode <strong>{pincode}</strong>
                      </span>
                      <button
                        onClick={() => {
                          setPinInput(pincode);
                          setEditingPin(true);
                        }}
                        className="bg-transparent border-0 cursor-pointer text-[12.5px] text-wgreen underline"
                      >
                        Change
                      </button>
                    </div>
                    {serviceability.data?.serviceable === false ? (
                      <p className="text-[12.5px] text-red-600 m-0 mt-1.5">
                        Sorry, this pincode isn’t serviceable yet.
                      </p>
                    ) : (
                      <>
                        <p className="text-[12px] text-wmuted m-0 mt-1">
                          Yay! Your pincode is eligible for delivery
                        </p>
                        {etaLabel && (
                          <p className="text-[13.5px] text-wgreen font-medium m-0 mt-1.5">
                            {etaLabel}
                          </p>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>

              {/* Payment marks */}
              <div className="text-center pt-1">
                <div className="flex items-center justify-center gap-2 flex-wrap mb-2">
                  {PAYMENT_MARKS.map((m) => (
                    <span
                      key={m}
                      className="rounded-md border border-wline bg-wcard px-2.5 py-1 text-[10.5px] tracking-wide text-wmuted"
                    >
                      {m}
                    </span>
                  ))}
                </div>
                <span className="inline-flex items-center gap-1.5 text-[12px] text-wmuted">
                  <ShieldCheck className="size-3.5 text-wgreen" aria-hidden="true" />
                  100% secured payments
                </span>
              </div>
            </>
          )}
        </div>

        {/* Sticky footer — total + checkout */}
        {items.length > 0 && (
          <div className="px-5 py-4 border-t border-wline bg-wcard shrink-0 flex items-center gap-4">
            <div className="min-w-0">
              <span className="font-wserif text-[24px] text-wink leading-none block">
                {formatPrice(total)}
              </span>
              <Link
                to="/cart"
                onClick={closeDrawer}
                className="text-[12px] text-wgreen underline"
              >
                View price details
              </Link>
            </div>
            <button
              onClick={goCheckout}
              className="flex-1 bg-wgreen text-white border-0 rounded-xl py-[14px] text-[14px] cursor-pointer hover:bg-wgreen-dark transition-colors"
            >
              Proceed to Checkout
            </button>
          </div>
        )}
      </aside>
    </>
  );
}
