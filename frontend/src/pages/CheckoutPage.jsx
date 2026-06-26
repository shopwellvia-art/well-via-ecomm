import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertTriangle,
  Banknote,
  Split,
  Smartphone,
  Building2,
  CreditCard,
  WalletCards,
  Wallet,
  TicketPercent,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import StepIndicator from '@/components/storefront/StepIndicator.jsx';
import WImage from '@/components/storefront/WImage.jsx';
import {
  LockIcon,
  TruckIcon,
  ShieldIcon,
  SupportIcon,
} from '@/components/storefront/Icons.jsx';
import AddressPicker from '@/features/addresses/components/AddressPicker.jsx';
import { useCart } from '@/features/cart/hooks.js';
import { useProduct } from '@/features/products/hooks.js';
import { useCheckout } from '@/features/payments/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useRateQuote } from '@/features/shipping/hooks.js';
import FreeShippingNudge from '@/features/shipping/components/FreeShippingNudge.jsx';
import { useCodCheck } from '@/features/cod/hooks.js';
import CodOtpModal from '@/features/cod/components/CodOtpModal.jsx';
import { usePaymentInstruments } from '@/features/payments/instruments.js';
import { useActivePaymentMethods } from '@/features/paymentMethods/hooks.js';
import { usePublicSettings } from '@/features/settings/public.js';
import { cn, formatPrice } from '@/lib/utils.js';

/* ─── Instrument icon map (from real instrument codes) ─── */
const INSTRUMENT_ICONS = {
  upi: Smartphone,
  netbanking: Building2,
  card: CreditCard,
  wallet: WalletCards,
};

/* ─── Trust badge definitions (right rail footer) ─── */
const TRUST_ITEMS = [
  { Icon: TruckIcon, label: 'Free Delivery' },
  { Icon: ShieldIcon, label: 'Secured Checkout' },
  { Icon: SupportIcon, label: 'Customer Support' },
];

/* ─── Step name → numeric mapping (for StepIndicator) ─── */
const STEP_NUM = { address: 1, payment: 2, review: 3 };

/* ─── Shared input style (wellness) ─── */
const inputCls =
  'w-full bg-wpaper border border-wline rounded-xl px-[17px] py-[15px] text-[14px] text-wink placeholder:text-wmuted focus:outline-none focus:ring-1 focus:ring-wgreen/40 focus:border-wgreen/60 transition-colors';

// Mirrors backend compute_line_tax — active rates sum, applied to (price × qty).
function computeBuyNowTax(product, qty) {
  if (!product?.taxes?.length) return 0;
  const rateSum = product.taxes
    .filter((t) => t.is_active !== false)
    .reduce((a, t) => a + Number(t.rate), 0);
  return Math.round(Number(product.price) * qty * rateSum) / 100;
}

export default function CheckoutPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [params] = useSearchParams();

  /* ── buyNow vs cart mode ── */
  const buyNowId = params.get('buyNow');
  const buyNowQty = Math.max(1, Number(params.get('qty') || 1));
  const buyNowMode = !!buyNowId;

  const {
    data: buyNowProduct,
    isLoading: buyNowLoading,
    isError: buyNowError,
    refetch: refetchBuyNow,
  } = useProduct(buyNowMode ? buyNowId : null);
  const {
    data: cart,
    isLoading: cartLoading,
    isError: cartError,
    refetch: refetchCart,
  } = useCart();
  const checkout = useCheckout();

  /* ── Form state ── */
  const [addressPayload, setAddressPayload] = useState({});
  const [billingSameAsShipping, setBillingSameAsShipping] = useState(true);
  const [billingPayload, setBillingPayload] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [paymentMethod, setPaymentMethod] = useState('prepaid');
  const [paymentInstrument, setPaymentInstrument] = useState(null);
  const [customerPhone, setCustomerPhone] = useState(() => user?.phone || '');
  const [phoneUserEdited, setPhoneUserEdited] = useState(false);
  const [phoneError, setPhoneError] = useState(null);
  const [otpOpen, setOtpOpen] = useState(false);
  const [otpVerified, setOtpVerified] = useState(false);
  // Gateway selector — only shown when >1 active gateway and method is prepaid/split_cod.
  const [selectedGateway, setSelectedGateway] = useState(null);
  // Wellness 3-step flow: 'address' | 'payment' | 'review'
  const [activeStep, setActiveStep] = useState('address');

  const pincode = addressPayload.pincode || '';

  /* Sync phone from address picker unless user has manually edited it */
  useEffect(() => {
    if (phoneUserEdited) return;
    const addrPhone = addressPayload.phone || '';
    if (addrPhone && addrPhone !== customerPhone) {
      setCustomerPhone(addrPhone);
    }
  }, [addressPayload.phone, phoneUserEdited]); // eslint-disable-line react-hooks/exhaustive-deps

  const isLoading = buyNowMode ? buyNowLoading : cartLoading;
  const isError = buyNowMode ? buyNowError : cartError;
  const refetch = buyNowMode ? refetchBuyNow : refetchCart;

  /* ── Derive items + totals ── */
  const { items, subtotal, taxAmount, discountAmount, total, couponCode, checkoutItems } =
    useMemo(() => {
      if (buyNowMode) {
        if (!buyNowProduct) {
          return {
            items: [],
            subtotal: 0,
            taxAmount: 0,
            discountAmount: 0,
            total: 0,
            couponCode: null,
            checkoutItems: [],
          };
        }
        const lineSubtotal = Number(buyNowProduct.price) * buyNowQty;
        const lineTax = computeBuyNowTax(buyNowProduct, buyNowQty);
        return {
          items: [
            {
              product_id: buyNowProduct.id,
              name: buyNowProduct.name,
              brand: buyNowProduct.brand,
              image_url: buyNowProduct.image_url,
              quantity: buyNowQty,
              unit_price: buyNowProduct.price,
              line_total: lineSubtotal + lineTax,
            },
          ],
          subtotal: lineSubtotal,
          taxAmount: lineTax,
          discountAmount: 0,
          total: lineSubtotal + lineTax,
          couponCode: null,
          checkoutItems: [{ product_id: buyNowProduct.id, quantity: buyNowQty }],
        };
      }
      const items = cart?.items ?? [];
      return {
        items,
        subtotal: Number(cart?.subtotal ?? 0),
        taxAmount: Number(cart?.tax_amount ?? 0),
        discountAmount: Number(cart?.discount_amount ?? 0),
        total: Number(cart?.total ?? 0),
        couponCode: cart?.coupon_code || null,
        checkoutItems: items.map((i) => ({ product_id: i.product_id, quantity: i.quantity })),
      };
    }, [buyNowMode, buyNowProduct, buyNowQty, cart]);

  /* ── Shipping rate ── */
  const { data: quote } = useRateQuote(pincode, checkoutItems);
  const shippingAmount = quote ? Number(quote.amount) : 0;

  /* ── COD availability ── */
  const { data: codCheck } = useCodCheck(pincode, checkoutItems);
  const codAvailable = !!codCheck?.available;
  const codSurcharge = codCheck ? Number(codCheck.surcharge_amount) : 0;
  const splitAvailable = !!codCheck?.split_available;
  const splitPrepaid = codCheck ? Number(codCheck.split_prepaid_amount) : 0;

  /* ── Payment instruments (admin-configured: upi/card/netbanking/wallet) ── */
  const { data: instrumentsData } = usePaymentInstruments();
  const enabledInstruments = (instrumentsData?.items || []).filter((i) => i.enabled);
  const suggestedInstrument = enabledInstruments.find((i) => i.suggested);

  // Auto-select suggested (or first) instrument when list arrives.
  useEffect(() => {
    if (paymentInstrument || enabledInstruments.length === 0) return;
    setPaymentInstrument((suggestedInstrument || enabledInstruments[0]).code);
  }, [paymentInstrument, enabledInstruments, suggestedInstrument]);

  // Guard: if COD availability changes, revert to prepaid when needed.
  useEffect(() => {
    if (!codCheck) return;
    if (paymentMethod === 'cod' && !codAvailable) setPaymentMethod('prepaid');
    if (paymentMethod === 'split_cod' && !splitAvailable) setPaymentMethod('prepaid');
  }, [paymentMethod, codCheck, codAvailable, splitAvailable]);

  // Guard: if instruments list changes, keep selection valid.
  useEffect(() => {
    if (!instrumentsData) return;
    if (paymentInstrument && !enabledInstruments.some((i) => i.code === paymentInstrument)) {
      setPaymentInstrument(
        (suggestedInstrument || enabledInstruments[0])?.code || null,
      );
    }
  }, [instrumentsData, paymentInstrument, enabledInstruments, suggestedInstrument]);

  /* ── Instrument discount ── */
  const activeInstrument = enabledInstruments.find((i) => i.code === paymentInstrument);
  const instrumentDiscountPct = activeInstrument
    ? Number(activeInstrument.discount_percent || 0)
    : 0;
  const instrumentApplies = paymentMethod === 'prepaid' || paymentMethod === 'split_cod';
  const instrumentDiscount = instrumentApplies
    ? Math.round(((subtotal + taxAmount) * instrumentDiscountPct) / 100 * 100) / 100
    : 0;

  /* ── Final totals ── */
  const codSurchargeApplied =
    paymentMethod === 'cod' || paymentMethod === 'split_cod' ? codSurcharge : 0;
  const displayedTotal = Math.max(
    0,
    total + shippingAmount + codSurchargeApplied - instrumentDiscount,
  );
  const splitBalance = Math.max(0, displayedTotal - splitPrepaid);

  /* ── COD OTP setting ── */
  const { data: publicSettings } = usePublicSettings();
  const codOtpRequired =
    paymentMethod === 'cod' &&
    String(publicSettings?.['cod.require_otp'] || 'true').toLowerCase() === 'true';

  /* ── Active payment gateways (admin-configured providers) ── */
  const { data: activeMethodsData } = useActivePaymentMethods();
  const activeGateways = activeMethodsData?.items ?? [];
  // Gateway selector only when >1 gateway available for online payment.
  const showGatewaySelector = instrumentApplies && activeGateways.length > 1;
  // Auto-select first gateway when list arrives and nothing chosen.
  const resolvedGateway =
    selectedGateway ?? (activeGateways.length > 0 ? activeGateways[0].code : null);

  /* ── Validation ── */
  const hasValidAddress =
    addressPayload.address_id != null ||
    (addressPayload.address != null && addressPayload.pincode);
  const hasValidBilling =
    billingSameAsShipping ||
    billingPayload.address_id != null ||
    billingPayload.address != null;
  const canSubmit = items.length > 0 && hasValidAddress && hasValidBilling && !submitting;

  /* ── Step mapping for StepIndicator ── */
  const numericStep = STEP_NUM[activeStep] || 1;

  function handleIndicatorStep(n) {
    if (n === 1) setActiveStep('address');
    else if (n === 2 && hasValidAddress) setActiveStep('payment');
    // Step 3 (review) is only reachable via "Review Order" button, not indicator click.
  }

  /* ── Payment method label for review summary ── */
  const paymentMethodLabel =
    paymentMethod === 'cod'
      ? 'Cash on Delivery'
      : paymentMethod === 'split_cod'
      ? 'Split COD'
      : activeInstrument?.label || 'Online Payment';

  /* ── Auth guard ── */
  if (!user) return <Navigate to="/login" replace />;

  /* ── Fetch error ── */
  if (!isLoading && isError) {
    return (
      <Page>
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full bg-wcanvas border border-wline flex items-center justify-center mb-5">
            <AlertTriangle className="size-8 text-wmuted" aria-hidden="true" />
          </div>
          <h1 className="font-wserif text-[26px] font-medium text-wink mb-2">
            Could not load checkout
          </h1>
          <p className="text-wmuted text-[14px] mb-6 max-w-[360px]">
            There was a problem loading your items. Please try again.
          </p>
          <button
            onClick={() => refetch()}
            className="bg-wgreen text-white rounded-full px-9 py-3.5 text-[14px] hover:bg-wgreen-dark transition-colors cursor-pointer border-0"
          >
            Try again
          </button>
        </div>
      </Page>
    );
  }

  /* ── Buy-now product not found ── */
  if (buyNowMode && !buyNowLoading && !buyNowError && buyNowProduct == null) {
    return (
      <Page>
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full bg-wcanvas border border-wline flex items-center justify-center mb-5">
            <AlertTriangle className="size-8 text-wmuted" aria-hidden="true" />
          </div>
          <h1 className="font-wserif text-[26px] font-medium text-wink mb-2">
            Product not found
          </h1>
          <p className="text-wmuted text-[14px] mb-6">
            This product could not be found. Please browse and try again.
          </p>
          <Link
            to="/products"
            className="bg-wgreen text-white rounded-full px-9 py-3.5 text-[14px] hover:bg-wgreen-dark transition-colors"
          >
            Browse products
          </Link>
        </div>
      </Page>
    );
  }

  /* ── Empty cart ── */
  if (!isLoading && items.length === 0) {
    return (
      <Page>
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full bg-wcanvas border border-wline flex items-center justify-center mb-5 text-wmuted">
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M6 8h12l-1 12H7L6 8Z" />
              <path d="M9 8a3 3 0 0 1 6 0" />
            </svg>
          </div>
          <h1 className="font-wserif text-[26px] font-medium text-wink mb-2">
            Your cart is empty
          </h1>
          <p className="text-wmuted text-[14px] mb-6">
            Add a product before heading to checkout.
          </p>
          <Link
            to="/products"
            className="bg-wgreen text-white rounded-full px-9 py-3.5 text-[14px] hover:bg-wgreen-dark transition-colors"
          >
            Browse products
          </Link>
        </div>
      </Page>
    );
  }

  /* ── COD OTP + submit handlers ── */
  function startPay() {
    if (codOtpRequired && !otpVerified) {
      setError(null);
      if (!customerPhone || customerPhone.trim().length < 8) {
        setPhoneError('Enter a valid phone number to receive the OTP.');
        // Redirect to address step where the phone field lives.
        setActiveStep('address');
        return;
      }
      setOtpOpen(true);
      return;
    }
    submitCheckout();
  }

  async function submitCheckout() {
    setSubmitting(true);
    setError(null);
    try {
      const resp = await checkout.mutateAsync({
        items: checkoutItems,
        payment_method: paymentMethod,
        ...(addressPayload.address_id != null
          ? { address_id: addressPayload.address_id }
          : {}),
        ...(addressPayload.address
          ? { address: addressPayload.address, save_address: addressPayload.save_address ?? false }
          : {}),
        ...(instrumentApplies && paymentInstrument
          ? { payment_instrument: paymentInstrument }
          : {}),
        ...(customerPhone?.trim()
          ? { customer_phone: customerPhone.trim() }
          : {}),
        ...(couponCode ? { coupon_code: couponCode } : {}),
        // Gateway code: include when method is prepaid/split_cod and a
        // specific gateway is available. For 0 or 1 gateways the backend
        // picks the only one; for >1 we send the customer's choice.
        ...(instrumentApplies && resolvedGateway
          ? { gateway_code: resolvedGateway }
          : {}),
        // Billing address: only send when unchecked. Backend defaults billing
        // = shipping when absent. billing_address_id takes precedence over inline.
        ...(!billingSameAsShipping && billingPayload.address_id != null
          ? { billing_address_id: billingPayload.address_id }
          : {}),
        ...(!billingSameAsShipping &&
        billingPayload.address_id == null &&
        billingPayload.address
          ? { billing_address: billingPayload.address }
          : {}),
      });
      window.location.assign(resp.redirect_url);
    } catch (err) {
      setSubmitting(false);
      const msg =
        err?.response?.data?.error?.message ||
        'Could not start payment. Please try again.';
      setError(msg);
    }
  }

  /* ════════════════════════════════════════════════════════
     MAIN RENDER
  ════════════════════════════════════════════════════════ */
  return (
    <Page>
      {/* ── Page heading ── */}
      <div className="text-center mb-7">
        <p className="text-[10.5px] tracking-[0.18em] uppercase text-wgold font-medium mb-2">
          Safe &amp; Secure
        </p>
        <h1 className="font-wserif font-medium text-[clamp(32px,4vw,48px)] text-wink m-0">
          Secure Checkout
        </h1>
      </div>

      {/* ── Step progress indicator ── */}
      <StepIndicator step={numericStep} onStep={handleIndicatorStep} />

      {/* ── Loading skeleton ── */}
      {isLoading ? (
        <div className="grid lg:grid-cols-[1.4fr_0.9fr] gap-6 lg:gap-10 items-start mt-2">
          <div className="bg-wcard border border-wline rounded-xl3 p-6 lg:p-9 flex flex-col gap-3 animate-pulse">
            <div className="h-8 w-1/3 bg-wcanvas rounded-xl" />
            <div className="h-4 w-1/2 bg-wcanvas rounded-xl" />
            <div className="h-12 bg-wcanvas rounded-xl" />
            <div className="h-12 bg-wcanvas rounded-xl" />
            <div className="h-12 bg-wcanvas rounded-xl" />
            <div className="h-12 bg-wcanvas rounded-xl mt-2" />
          </div>
          <div className="bg-wcard border border-wline rounded-xl3 p-6 flex flex-col gap-3 animate-pulse">
            <div className="h-6 w-1/2 bg-wcanvas rounded-xl" />
            <div className="h-20 bg-wcanvas rounded-xl" />
            <div className="h-4 w-full bg-wcanvas rounded-xl" />
            <div className="h-4 w-2/3 bg-wcanvas rounded-xl" />
            <div className="h-4 w-3/4 bg-wcanvas rounded-xl" />
          </div>
        </div>
      ) : (
        <div className="grid lg:grid-cols-[1.4fr_0.9fr] gap-6 lg:gap-10 items-start mt-2">

          {/* ══════════════════════════════════════
              LEFT — multi-step form card
          ══════════════════════════════════════ */}
          <div className="bg-wcard border border-wline rounded-xl3 p-6 lg:p-9">
            <AnimatePresence mode="wait">

              {/* ─── STEP 1 — Shipping Details ─── */}
              {activeStep === 'address' && (
                <motion.div
                  key="step-address"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.22, ease: 'easeOut' }}
                >
                  <h2 className="font-wserif font-semibold text-[24px] text-wink mb-1">
                    Shipping Details
                  </h2>
                  <p className="text-[13px] text-wmuted mb-[22px] font-light">
                    Where should we send your order?
                  </p>

                  {/* Address picker — saved addresses + inline new-address form */}
                  <AddressPicker onChange={(payload) => setAddressPayload(payload)} />

                  {/* Billing address same as shipping */}
                  <div className="mt-4">
                    <label className="flex cursor-pointer items-center gap-2.5 rounded-xl border border-wline bg-wpaper px-4 py-3 text-sm transition-colors hover:border-wgreen/40">
                      <input
                        type="checkbox"
                        checked={billingSameAsShipping}
                        onChange={(e) => {
                          setBillingSameAsShipping(e.target.checked);
                          if (e.target.checked) setBillingPayload({});
                        }}
                        className="size-4 rounded accent-wgreen"
                      />
                      <span className="font-medium text-wink">
                        Billing address same as delivery address
                      </span>
                    </label>

                    <AnimatePresence initial={false}>
                      {!billingSameAsShipping && (
                        <motion.div
                          key="billing-form"
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2, ease: 'easeInOut' }}
                          className="overflow-hidden"
                        >
                          <div className="mt-3 rounded-xl border border-wline bg-wpaper p-4">
                            <p className="mb-3 text-[11px] font-bold uppercase tracking-widest text-wmuted">
                              Billing address
                            </p>
                            <AddressPicker onChange={(payload) => setBillingPayload(payload)} />
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>

                  {/* Phone number (used for delivery updates + COD OTP) */}
                  <div className="mt-4 border-t border-wline pt-4">
                    <label htmlFor="ship-phone" className="block mb-1.5 text-[13px] text-wmuted">
                      Phone number
                      {codOtpRequired && (
                        <span className="ml-2 text-[11px] text-wgold">
                          (OTP required for COD)
                        </span>
                      )}
                    </label>
                    <input
                      id="ship-phone"
                      type="tel"
                      inputMode="tel"
                      autoComplete="tel"
                      value={customerPhone}
                      placeholder="+91 98765 43210"
                      onChange={(e) => {
                        setCustomerPhone(e.target.value);
                        setPhoneUserEdited(true);
                        setOtpVerified(false);
                        if (phoneError) setPhoneError(null);
                      }}
                      className={cn(
                        inputCls,
                        phoneError && 'border-red-400 focus:ring-red-300/40 focus:border-red-400',
                      )}
                    />
                    {phoneError ? (
                      <p className="mt-1 text-xs text-red-500">{phoneError}</p>
                    ) : (
                      <p className="mt-1 text-xs text-wmuted">
                        {codOtpRequired
                          ? 'We use this for delivery updates and the COD verification SMS.'
                          : 'We use this for delivery updates.'}
                      </p>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => hasValidAddress && setActiveStep('payment')}
                    disabled={!hasValidAddress}
                    className="w-full bg-wgreen text-white border-0 rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer mt-[22px] hover:bg-wgreen-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    Continue to Payment →
                  </button>
                </motion.div>
              )}

              {/* ─── STEP 2 — Payment ─── */}
              {activeStep === 'payment' && (
                <motion.div
                  key="step-payment"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.22, ease: 'easeOut' }}
                >
                  <h2 className="font-wserif font-semibold text-[24px] text-wink mb-1">
                    Payment
                  </h2>
                  <p className="text-[13px] text-wmuted mb-[18px] font-light">
                    Choose how you'd like to pay. All transactions are encrypted &amp; secure.
                  </p>

                  <div className="flex flex-col gap-2.5">
                    {enabledInstruments.length > 0 && (
                      <p className="text-[10.5px] tracking-[0.14em] uppercase text-wmuted font-medium px-0.5">
                        {suggestedInstrument ? 'Suggested' : 'Pay Online'}
                      </p>
                    )}

                    {/* Online payment instruments — driven by usePaymentInstruments (real data) */}
                    {enabledInstruments.map((inst) => {
                      const Icon = INSTRUMENT_ICONS[inst.code] || Wallet;
                      const selected =
                        paymentMethod === 'prepaid' && paymentInstrument === inst.code;
                      const discount = Number(inst.discount_percent || 0);
                      return (
                        <button
                          key={inst.code}
                          type="button"
                          onClick={() => {
                            setPaymentMethod('prepaid');
                            setPaymentInstrument(inst.code);
                          }}
                          className={cn(
                            'flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors duration-150 cursor-pointer',
                            selected
                              ? 'border-wgreen bg-wgreen/8'
                              : 'border-wline bg-wpaper hover:border-wgreen/40 hover:shadow-sm',
                          )}
                        >
                          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-wgreen/10 text-wgreen">
                            <Icon className="size-5" aria-hidden="true" />
                          </span>
                          <div className="flex-1 min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="text-sm font-semibold text-wink">{inst.label}</p>
                              {inst.suggested && (
                                <span className="rounded-full bg-wgreen/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-wgreen">
                                  Suggested
                                </span>
                              )}
                              {discount > 0 && (
                                <span className="rounded-full bg-wgold/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-wgold">
                                  {discount}% off
                                </span>
                              )}
                            </div>
                            <p className="mt-0.5 text-xs text-wmuted">{inst.description}</p>
                          </div>
                          {selected && (
                            <CheckCircle2 className="size-5 shrink-0 text-wgreen mt-0.5" aria-hidden="true" />
                          )}
                        </button>
                      );
                    })}

                    {/* Cash on Delivery */}
                    <button
                      type="button"
                      onClick={() => codAvailable && setPaymentMethod('cod')}
                      disabled={!codAvailable}
                      className={cn(
                        'flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors duration-150',
                        !codAvailable && 'cursor-not-allowed opacity-50',
                        codAvailable && paymentMethod === 'cod'
                          ? 'border-wgold bg-wgold/8 cursor-pointer'
                          : codAvailable
                          ? 'border-wline bg-wpaper hover:border-wgold/40 hover:shadow-sm cursor-pointer'
                          : 'border-wline bg-wpaper',
                      )}
                    >
                      <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-wgold/10 text-wgold">
                        <Banknote className="size-5" aria-hidden="true" />
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-wink">Cash on Delivery</p>
                          {codCheck && codAvailable && codSurcharge > 0 && (
                            <span className="rounded-full bg-wgold/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-wgold">
                              +{formatPrice(codSurcharge)} fee
                            </span>
                          )}
                        </div>
                        {codAvailable ? (
                          <p className="mt-0.5 text-xs text-wmuted">
                            Pay {formatPrice(displayedTotal)} on delivery.
                          </p>
                        ) : codCheck?.reasons?.length ? (
                          <ul className="mt-1 list-disc pl-4 text-xs text-wmuted">
                            {codCheck.reasons.slice(0, 3).map((r, i) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        ) : (
                          <p className="mt-0.5 text-xs text-wmuted">Checking availability…</p>
                        )}
                      </div>
                      {codAvailable && paymentMethod === 'cod' ? (
                        <CheckCircle2 className="size-5 shrink-0 text-wgold mt-0.5" aria-hidden="true" />
                      ) : !codAvailable && codCheck ? (
                        <XCircle className="size-5 shrink-0 text-wmuted mt-0.5" aria-hidden="true" />
                      ) : null}
                    </button>

                    {/* Split COD */}
                    {splitAvailable && (
                      <button
                        type="button"
                        onClick={() => setPaymentMethod('split_cod')}
                        className={cn(
                          'flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors duration-150 cursor-pointer',
                          paymentMethod === 'split_cod'
                            ? 'border-wgreen bg-wgreen/8'
                            : 'border-wline bg-wpaper hover:border-wgreen/40 hover:shadow-sm',
                        )}
                      >
                        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-wgreen/10 text-wgreen">
                          <Split className="size-5" aria-hidden="true" />
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-wink">Split COD</p>
                            {codSurcharge > 0 && (
                              <span className="rounded-full bg-wgold/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-wgold">
                                +{formatPrice(codSurcharge)} fee
                              </span>
                            )}
                          </div>
                          <p className="mt-0.5 text-xs text-wmuted">
                            Pay{' '}
                            <span className="font-semibold text-wink">{formatPrice(splitPrepaid)}</span>{' '}
                            now,{' '}
                            <span className="font-semibold text-wink">{formatPrice(splitBalance)}</span>{' '}
                            on delivery.
                          </p>
                        </div>
                        {paymentMethod === 'split_cod' && (
                          <CheckCircle2 className="size-5 shrink-0 text-wgreen mt-0.5" aria-hidden="true" />
                        )}
                      </button>
                    )}
                  </div>

                  {/* Gateway selector — only when >1 admin-configured gateway and online payment */}
                  {showGatewaySelector && (
                    <div className="mt-4">
                      <p className="text-[10.5px] tracking-[0.14em] uppercase text-wmuted mb-2">
                        Pay via
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {activeGateways.map((gw) => {
                          const active = resolvedGateway === gw.code;
                          return (
                            <button
                              key={gw.code}
                              type="button"
                              onClick={() => setSelectedGateway(gw.code)}
                              className={cn(
                                'rounded-full border px-4 py-1.5 min-h-[36px] text-[12.5px] font-medium transition-colors cursor-pointer',
                                active
                                  ? 'border-wgreen bg-wgreen text-white'
                                  : 'border-wline bg-wpaper text-wmuted hover:border-wgreen/40 hover:text-wink',
                              )}
                            >
                              {gw.name}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Step navigation */}
                  <div className="flex gap-3 mt-6">
                    <button
                      type="button"
                      onClick={() => setActiveStep('address')}
                      className="shrink-0 bg-transparent border border-wline text-wink rounded-full px-6 py-4 text-[14px] cursor-pointer hover:border-wgreen/40 transition-colors"
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      onClick={() => setActiveStep('review')}
                      className="flex-1 bg-wgreen text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-wgreen-dark transition-colors"
                    >
                      Review Order →
                    </button>
                  </div>
                </motion.div>
              )}

              {/* ─── STEP 3 — Review & Confirm ─── */}
              {activeStep === 'review' && (
                <motion.div
                  key="step-review"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.22, ease: 'easeOut' }}
                >
                  <h2 className="font-wserif font-semibold text-[24px] text-wink mb-[22px]">
                    Review &amp; Confirm
                  </h2>

                  <div className="flex flex-col gap-4">
                    {/* Address summary */}
                    <div className="bg-wpaper border border-wline rounded-xl2 p-[18px]">
                      <div className="text-[11px] tracking-[0.14em] uppercase text-wgold mb-2 font-medium">
                        Shipping To
                      </div>
                      <div className="text-[14px] leading-[1.6] text-wink">
                        {pincode ? (
                          <>Pincode: <span className="font-semibold">{pincode}</span></>
                        ) : (
                          'Address selected'
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => setActiveStep('address')}
                        className="mt-2 text-[12px] text-wgreen hover:text-wgreen-dark font-medium transition-colors bg-transparent border-0 cursor-pointer p-0"
                      >
                        Change address
                      </button>
                    </div>

                    {/* Payment method summary */}
                    <div className="bg-wpaper border border-wline rounded-xl2 p-[18px]">
                      <div className="text-[11px] tracking-[0.14em] uppercase text-wgold mb-2 font-medium">
                        Payment
                      </div>
                      <div className="text-[14px] text-wink">{paymentMethodLabel}</div>
                      <button
                        type="button"
                        onClick={() => setActiveStep('payment')}
                        className="mt-2 text-[12px] text-wgreen hover:text-wgreen-dark font-medium transition-colors bg-transparent border-0 cursor-pointer p-0"
                      >
                        Change payment
                      </button>
                    </div>
                  </div>

                  {/* Error from submit attempt */}
                  {error && (
                    <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50 p-3.5 text-sm text-red-600">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                      <span>{error}</span>
                    </div>
                  )}

                  {/* Step navigation + place order */}
                  <div className="flex gap-3 mt-[22px]">
                    <button
                      type="button"
                      onClick={() => setActiveStep('payment')}
                      className="shrink-0 bg-transparent border border-wline text-wink rounded-full px-6 py-4 text-[14px] cursor-pointer hover:border-wgreen/40 transition-colors"
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      onClick={startPay}
                      disabled={!canSubmit || submitting}
                      className="flex-1 bg-wgreen text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-wgreen-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {submitting
                        ? 'Processing…'
                        : paymentMethod === 'cod'
                        ? `Place Order · ${formatPrice(displayedTotal)} COD`
                        : paymentMethod === 'split_cod'
                        ? `Pay ${formatPrice(splitPrepaid)} Now`
                        : `Place Order · ${formatPrice(displayedTotal)}`}
                    </button>
                  </div>
                </motion.div>
              )}

            </AnimatePresence>
          </div>

          {/* ══════════════════════════════════════
              RIGHT — sticky order summary
          ══════════════════════════════════════ */}
          <div className="bg-wcard border border-wline rounded-xl3 p-6 lg:sticky lg:top-[90px]">
            <h3 className="font-wserif font-semibold text-[20px] text-wink mb-4">
              Order Summary
            </h3>

            {/* Item list with thumbnails */}
            <div className="flex flex-col gap-3.5 mb-4">
              {items.map((i) => (
                <div key={i.product_id} className="flex gap-3 items-center">
                  <WImage
                    src={i.image_url}
                    alt={i.name}
                    shape="rounded"
                    className="w-[54px] h-[62px] shrink-0 border border-wline"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-[14px] leading-tight text-wink truncate">{i.name}</div>
                    <div className="text-[11.5px] text-wmuted">
                      {i.brand ? `${i.brand} · ` : ''}Qty {i.quantity}
                    </div>
                  </div>
                  <div className="font-wserif text-[16px] text-wink shrink-0">
                    {formatPrice(i.line_total)}
                  </div>
                </div>
              ))}
            </div>

            {/* Free shipping nudge */}
            <FreeShippingNudge subtotal={subtotal} />

            {/* Totals */}
            <dl className="border-t border-wline pt-4 mt-3 flex flex-col gap-2.5 text-[13.5px] text-wmuted">
              <div className="flex justify-between">
                <dt>Subtotal</dt>
                <dd className="text-wink font-medium">{formatPrice(subtotal)}</dd>
              </div>
              {taxAmount > 0 && (
                <div className="flex justify-between">
                  <dt>Tax</dt>
                  <dd className="text-wink font-medium">{formatPrice(taxAmount)}</dd>
                </div>
              )}
              {discountAmount > 0 && (
                <div className="flex justify-between">
                  <dt className="flex items-center gap-1 text-wgreen">
                    <TicketPercent className="size-3.5" aria-hidden="true" />
                    Discount
                    {couponCode && (
                      <span className="font-mono text-[10px] text-wmuted">
                        ({couponCode})
                      </span>
                    )}
                  </dt>
                  <dd className="font-semibold text-wgreen">−{formatPrice(discountAmount)}</dd>
                </div>
              )}
              <div className="flex justify-between">
                <dt>Delivery</dt>
                {quote ? (
                  shippingAmount === 0 ? (
                    <dd className="font-semibold text-wgreen">Free</dd>
                  ) : (
                    <dd className="font-medium text-wink">{formatPrice(shippingAmount)}</dd>
                  )
                ) : pincode ? (
                  <dd className="text-xs text-wmuted">Calculating…</dd>
                ) : (
                  <dd className="text-xs text-wmuted">Add address</dd>
                )}
              </div>
              {(paymentMethod === 'cod' || paymentMethod === 'split_cod') &&
                codSurcharge > 0 && (
                  <div className="flex justify-between">
                    <dt>COD fee</dt>
                    <dd className="font-medium text-wink">{formatPrice(codSurcharge)}</dd>
                  </div>
                )}
              {instrumentApplies && instrumentDiscount > 0 && activeInstrument && (
                <div className="flex justify-between">
                  <dt className="flex items-center gap-1 text-wgreen">
                    <span className="capitalize">{activeInstrument.label}</span>
                    <span className="font-mono text-[10px] text-wmuted">
                      ({instrumentDiscountPct}% off)
                    </span>
                  </dt>
                  <dd className="font-semibold text-wgreen">−{formatPrice(instrumentDiscount)}</dd>
                </div>
              )}
              {paymentMethod === 'split_cod' && (
                <div className="rounded-xl border border-wline bg-wpaper px-3 py-2 text-[11px] text-wmuted mt-1">
                  Now:{' '}
                  <span className="font-semibold text-wink">{formatPrice(splitPrepaid)}</span>
                  {' · '}
                  On delivery:{' '}
                  <span className="font-semibold text-wink">{formatPrice(splitBalance)}</span>
                </div>
              )}
            </dl>

            {/* Grand total */}
            <div className="flex justify-between items-center border-t border-wline pt-3 mt-1">
              <span className="text-[15px] text-wink">Total</span>
              <span className="font-wserif text-[24px] text-wink">{formatPrice(displayedTotal)}</span>
            </div>

            {/* Trust badges */}
            <div className="flex flex-wrap gap-3 justify-center mt-5 pt-[18px] border-t border-wline">
              {TRUST_ITEMS.map(({ Icon, label }) => (
                <div key={label} className="flex items-center gap-1.5 text-[11px] text-wmuted">
                  <span className="text-wgreen">
                    <Icon size={13} />
                  </span>
                  {label}
                </div>
              ))}
            </div>

            {/* Security note */}
            <p className="mt-4 flex items-center justify-center gap-1.5 text-[11px] text-wmuted">
              <LockIcon size={11} />
              {paymentMethod === 'cod'
                ? 'Carrier collects on delivery'
                : paymentMethod === 'split_cod'
                ? `${formatPrice(splitBalance)} collected on delivery`
                : 'Secure encrypted payment'}
            </p>

            {/* Cancel link */}
            <button
              type="button"
              onClick={() => navigate('/cart')}
              className="mt-3 w-full text-center text-[12px] text-wmuted hover:text-wink transition-colors bg-transparent border-0 cursor-pointer"
            >
              Cancel and go back to cart
            </button>
          </div>
        </div>
      )}

      {/* ── Mobile sticky CTA bar (hidden on lg+ where the right rail is visible) ── */}
      {!isLoading && items.length > 0 && (
        <>
          {/* Spacer so content isn't hidden behind the fixed bar */}
          <div className="h-20 lg:hidden" aria-hidden="true" />
          <div className="fixed inset-x-0 bottom-0 z-40 border-t border-wline bg-wcard px-4 pb-4 pt-3 shadow-lg lg:hidden">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs text-wmuted">Total</p>
                <p className="text-base font-bold text-wink">{formatPrice(displayedTotal)}</p>
              </div>
              <button
                type="button"
                onClick={
                  activeStep === 'address'
                    ? () => hasValidAddress && setActiveStep('payment')
                    : activeStep === 'payment'
                    ? () => setActiveStep('review')
                    : startPay
                }
                disabled={
                  (activeStep === 'address' && !hasValidAddress) ||
                  (activeStep === 'review' && (!canSubmit || submitting))
                }
                className="shrink-0 bg-wgreen text-white border-0 rounded-full px-6 py-3 text-[13.5px] font-medium hover:bg-wgreen-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                {activeStep === 'address'
                  ? 'Continue →'
                  : activeStep === 'payment'
                  ? 'Review →'
                  : submitting
                  ? 'Processing…'
                  : paymentMethod === 'cod'
                  ? 'Place Order (COD)'
                  : paymentMethod === 'split_cod'
                  ? `Pay ${formatPrice(splitPrepaid)} Now`
                  : 'Place Order'}
              </button>
            </div>
          </div>
        </>
      )}

      {/* ── COD OTP Modal ── */}
      {otpOpen && (
        <CodOtpModal
          phone={customerPhone.trim()}
          onClose={() => setOtpOpen(false)}
          onVerified={() => {
            setOtpVerified(true);
            setOtpOpen(false);
            submitCheckout();
          }}
        />
      )}
    </Page>
  );
}
