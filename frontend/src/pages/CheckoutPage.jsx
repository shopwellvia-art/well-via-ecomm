import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ArrowLeft,
  Lock,
  ShoppingBag,
  Wallet,
  AlertTriangle,
  TicketPercent,
  Banknote,
  CheckCircle2,
  XCircle,
  Split,
  Smartphone,
  Building2,
  CreditCard,
  WalletCards,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
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
import { fadeUp, staggerContainer, listStagger } from '@/lib/motion.js';

const INSTRUMENT_ICONS = {
  upi: Smartphone,
  netbanking: Building2,
  card: CreditCard,
  wallet: WalletCards,
};

// Mirrors backend compute_line_tax — active rates sum, applied to (price × qty).
function computeBuyNowTax(product, qty) {
  if (!product?.taxes?.length) return 0;
  const rateSum = product.taxes
    .filter((t) => t.is_active !== false)
    .reduce((a, t) => a + Number(t.rate), 0);
  return Math.round(Number(product.price) * qty * rateSum) / 100;
}

function SectionLabel({ step, children }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent/12 text-[11px] font-bold text-accent">
        {step}
      </span>
      <h2 className="text-h3 text-ink-primary tracking-tight">{children}</h2>
    </div>
  );
}

export default function CheckoutPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const buyNowId = params.get('buyNow');
  const buyNowQty = Math.max(1, Number(params.get('qty') || 1));
  const buyNowMode = !!buyNowId;

  const { data: buyNowProduct, isLoading: buyNowLoading } = useProduct(
    buyNowMode ? buyNowId : null,
  );
  const { data: cart, isLoading: cartLoading } = useCart();
  const checkout = useCheckout();

  const [addressPayload, setAddressPayload] = useState({});
  const [billingSameAsShipping, setBillingSameAsShipping] = useState(true);
  const [billingPayload, setBillingPayload] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [paymentMethod, setPaymentMethod] = useState('prepaid');
  const [paymentInstrument, setPaymentInstrument] = useState(null);
  const [customerPhone, setCustomerPhone] = useState(() => user?.phone || '');
  const [phoneUserEdited, setPhoneUserEdited] = useState(false);
  const [otpOpen, setOtpOpen] = useState(false);
  const [otpVerified, setOtpVerified] = useState(false);
  // Gateway selector — only shown when >1 active gateway and method is prepaid/split_cod.
  const [selectedGateway, setSelectedGateway] = useState(null);

  const pincode = addressPayload.pincode || '';

  useEffect(() => {
    if (phoneUserEdited) return;
    const addrPhone = addressPayload.phone || '';
    if (addrPhone && addrPhone !== customerPhone) {
      setCustomerPhone(addrPhone);
    }
  }, [addressPayload.phone, phoneUserEdited]); // eslint-disable-line react-hooks/exhaustive-deps

  const isLoading = buyNowMode ? buyNowLoading : cartLoading;

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

  const { data: quote } = useRateQuote(pincode, checkoutItems);
  const shippingAmount = quote ? Number(quote.amount) : 0;

  const { data: codCheck } = useCodCheck(pincode, checkoutItems);
  const codAvailable = !!codCheck?.available;
  const codSurcharge = codCheck ? Number(codCheck.surcharge_amount) : 0;
  const splitAvailable = !!codCheck?.split_available;
  const splitPrepaid = codCheck ? Number(codCheck.split_prepaid_amount) : 0;

  const { data: instrumentsData } = usePaymentInstruments();
  const enabledInstruments = (instrumentsData?.items || []).filter((i) => i.enabled);
  const suggestedInstrument = enabledInstruments.find((i) => i.suggested);

  useEffect(() => {
    if (paymentInstrument || enabledInstruments.length === 0) return;
    setPaymentInstrument((suggestedInstrument || enabledInstruments[0]).code);
  }, [paymentInstrument, enabledInstruments, suggestedInstrument]);

  useEffect(() => {
    if (!codCheck) return;
    if (paymentMethod === 'cod' && !codAvailable) setPaymentMethod('prepaid');
    if (paymentMethod === 'split_cod' && !splitAvailable) setPaymentMethod('prepaid');
  }, [paymentMethod, codCheck, codAvailable, splitAvailable]);

  useEffect(() => {
    if (!instrumentsData) return;
    if (paymentInstrument && !enabledInstruments.some((i) => i.code === paymentInstrument)) {
      setPaymentInstrument(
        (suggestedInstrument || enabledInstruments[0])?.code || null,
      );
    }
  }, [instrumentsData, paymentInstrument, enabledInstruments, suggestedInstrument]);

  const activeInstrument = enabledInstruments.find((i) => i.code === paymentInstrument);
  const instrumentDiscountPct = activeInstrument
    ? Number(activeInstrument.discount_percent || 0)
    : 0;
  const instrumentApplies = paymentMethod === 'prepaid' || paymentMethod === 'split_cod';
  const instrumentDiscount = instrumentApplies
    ? Math.round(((subtotal + taxAmount) * instrumentDiscountPct) / 100 * 100) / 100
    : 0;

  const codSurchargeApplied =
    paymentMethod === 'cod' || paymentMethod === 'split_cod' ? codSurcharge : 0;

  const displayedTotal = Math.max(
    0,
    total + shippingAmount + codSurchargeApplied - instrumentDiscount,
  );
  const splitBalance = Math.max(0, displayedTotal - splitPrepaid);

  const { data: publicSettings } = usePublicSettings();
  const codOtpRequired =
    paymentMethod === 'cod' &&
    String(publicSettings?.['cod.require_otp'] || 'true').toLowerCase() === 'true';

  // Active payment gateways — used only when method is prepaid or split_cod.
  const { data: activeMethodsData } = useActivePaymentMethods();
  const activeGateways = activeMethodsData?.items ?? [];
  // Gateway selector is only shown when >1 gateway is available for online payment.
  const showGatewaySelector = instrumentApplies && activeGateways.length > 1;
  // Auto-select the first gateway when the list arrives and nothing is chosen yet.
  const resolvedGateway =
    selectedGateway ??
    (activeGateways.length > 0 ? activeGateways[0].code : null);

  const hasValidAddress =
    addressPayload.address_id != null ||
    (addressPayload.address != null && addressPayload.pincode);
  const hasValidBilling =
    billingSameAsShipping ||
    billingPayload.address_id != null ||
    billingPayload.address != null;
  const canSubmit = items.length > 0 && hasValidAddress && hasValidBilling && !submitting;

  if (!user) return <Navigate to="/login" replace />;

  if (!isLoading && items.length === 0) {
    return (
      <Page>
        <h1 className="text-h1 text-ink-primary tracking-tight">Checkout</h1>
        <div className="mt-8">
          <EmptyState
            icon={ShoppingBag}
            title="Your cart is empty"
            description="Add a product before heading to checkout."
            action={
              <Link to="/products">
                <Button size="sm">Browse products</Button>
              </Link>
            }
          />
        </div>
      </Page>
    );
  }

  function startPay() {
    if (codOtpRequired && !otpVerified) {
      setError(null);
      if (!customerPhone || customerPhone.trim().length < 8) {
        setError('Enter your phone number above to receive the OTP.');
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

  return (
    <Page>
      <Breadcrumbs
        items={[{ label: 'Cart', to: '/cart' }]}
        current="Checkout"
        className="mb-5"
      />

      <Link
        to="/cart"
        className="inline-flex items-center gap-1.5 text-sm text-ink-secondary hover:text-ink-primary transition-colors"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to cart
      </Link>
      <h1 className="mt-2 text-h1 text-ink-primary tracking-tight">Checkout</h1>

      {isLoading ? (
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_360px]">
          <div className="flex flex-col gap-4">
            <Skeleton className="h-48 rounded-lg" />
            <Skeleton className="h-64 rounded-lg" />
          </div>
          <Skeleton className="h-72 rounded-lg" />
        </div>
      ) : (
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_360px]">
          {/* Left column: shipping + payment */}
          <motion.div
            variants={staggerContainer(0.1)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-5"
          >
            {/* Step 1 – Shipping address */}
            <motion.div variants={fadeUp}>
              <Card className="p-6">
                <SectionLabel step="1">Delivery address</SectionLabel>
                <p className="mb-4 text-sm text-ink-secondary">
                  Used only for delivery — never shared.
                </p>
                <AddressPicker
                  onChange={(payload) => setAddressPayload(payload)}
                />

                {/* Billing address same as shipping checkbox */}
                <div className="mt-4">
                  <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 text-sm transition-colors hover:border-line-strong">
                    <input
                      type="checkbox"
                      checked={billingSameAsShipping}
                      onChange={(e) => {
                        setBillingSameAsShipping(e.target.checked);
                        if (e.target.checked) setBillingPayload({});
                      }}
                      className="size-4 rounded border-line-subtle bg-bg-elevated text-accent"
                    />
                    <span className="font-medium text-ink-primary">
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
                        <div className="mt-3 rounded-lg border border-line-subtle bg-bg-elevated p-4">
                          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
                            Billing address
                          </p>
                          <AddressPicker
                            onChange={(payload) => setBillingPayload(payload)}
                          />
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>

                <div className="mt-5 border-t border-line-subtle pt-5">
                  <label className="block text-sm font-medium text-ink-primary" htmlFor="ship-phone">
                    Phone number
                    {codOtpRequired && (
                      <span className="ml-1.5 text-xs text-warning font-normal">
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
                    onChange={(e) => {
                      setCustomerPhone(e.target.value);
                      setPhoneUserEdited(true);
                      setOtpVerified(false);
                    }}
                    placeholder="+91 98765 43210"
                    className="mt-2 w-full rounded-lg border border-line-subtle bg-bg-elevated px-3 py-2.5 text-sm text-ink-primary placeholder:text-ink-tertiary transition-colors hover:border-line-strong focus-visible:focus-ring"
                  />
                  <p className="mt-1 text-[11px] text-ink-tertiary">
                    We use this for delivery updates
                    {codOtpRequired ? ' and the COD verification SMS.' : '.'}
                  </p>
                </div>
              </Card>
            </motion.div>

            {/* Step 2 – Payment method */}
            <motion.div variants={fadeUp}>
              <Card className="p-6">
                <SectionLabel step="2">Payment method</SectionLabel>

                <div className="flex flex-col gap-2.5">
                  {enabledInstruments.length > 0 && (
                    <p className="text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary px-0.5">
                      {suggestedInstrument ? 'Suggested' : 'Pay online'}
                    </p>
                  )}

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
                          'flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-all duration-150',
                          selected
                            ? 'border-accent bg-accent/6 shadow-glow-sm'
                            : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm',
                        )}
                      >
                        <span
                          className={cn(
                            'grid size-10 shrink-0 place-items-center rounded-lg transition-colors',
                            selected ? 'bg-accent/20 text-accent' : 'bg-accent/10 text-accent',
                          )}
                        >
                          <Icon className="size-5" aria-hidden="true" />
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-ink-primary">
                              {inst.label}
                            </p>
                            {inst.suggested && (
                              <span className="rounded-full bg-success/12 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-success">
                                Suggested
                              </span>
                            )}
                            {discount > 0 && (
                              <span className="rounded-full bg-accent/12 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
                                {discount}% off
                              </span>
                            )}
                          </div>
                          <p className="mt-0.5 text-xs text-ink-secondary">
                            {inst.description}
                          </p>
                        </div>
                        {selected && (
                          <CheckCircle2 className="size-5 shrink-0 text-accent mt-0.5" aria-hidden="true" />
                        )}
                      </button>
                    );
                  })}

                  {/* COD */}
                  <button
                    type="button"
                    onClick={() => codAvailable && setPaymentMethod('cod')}
                    disabled={!codAvailable}
                    className={cn(
                      'flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-all duration-150',
                      !codAvailable && 'cursor-not-allowed opacity-50',
                      codAvailable && paymentMethod === 'cod'
                        ? 'border-accent bg-accent/6 shadow-glow-sm'
                        : codAvailable
                        ? 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm'
                        : 'border-line-subtle bg-bg-elevated',
                    )}
                  >
                    <span
                      className={cn(
                        'grid size-10 shrink-0 place-items-center rounded-lg',
                        codAvailable && paymentMethod === 'cod'
                          ? 'bg-warning/20 text-warning'
                          : 'bg-warning/10 text-warning',
                      )}
                    >
                      <Banknote className="size-5" aria-hidden="true" />
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-ink-primary">
                          Cash on Delivery
                        </p>
                        {codCheck && codAvailable && codSurcharge > 0 && (
                          <span className="rounded-full bg-warning/12 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning">
                            +{formatPrice(codSurcharge)} fee
                          </span>
                        )}
                      </div>
                      {codAvailable ? (
                        <p className="mt-0.5 text-xs text-ink-secondary">
                          Pay {formatPrice(displayedTotal)} on delivery.
                        </p>
                      ) : codCheck?.reasons?.length ? (
                        <ul className="mt-1 list-disc pl-4 text-xs text-ink-tertiary">
                          {codCheck.reasons.slice(0, 3).map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-0.5 text-xs text-ink-tertiary">
                          Checking availability…
                        </p>
                      )}
                    </div>
                    {codAvailable && paymentMethod === 'cod' ? (
                      <CheckCircle2 className="size-5 shrink-0 text-accent mt-0.5" aria-hidden="true" />
                    ) : !codAvailable && codCheck ? (
                      <XCircle className="size-5 shrink-0 text-ink-tertiary mt-0.5" aria-hidden="true" />
                    ) : null}
                  </button>

                  {/* Split COD */}
                  {splitAvailable && (
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('split_cod')}
                      className={cn(
                        'flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-all duration-150',
                        paymentMethod === 'split_cod'
                          ? 'border-accent bg-accent/6 shadow-glow-sm'
                          : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm',
                      )}
                    >
                      <span
                        className={cn(
                          'grid size-10 shrink-0 place-items-center rounded-lg',
                          paymentMethod === 'split_cod'
                            ? 'bg-info/20 text-info'
                            : 'bg-info/10 text-info',
                        )}
                      >
                        <Split className="size-5" aria-hidden="true" />
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-ink-primary">
                            Split COD
                          </p>
                          {codSurcharge > 0 && (
                            <span className="rounded-full bg-warning/12 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning">
                              +{formatPrice(codSurcharge)} fee
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-ink-secondary">
                          Pay{' '}
                          <span className="font-semibold text-ink-primary nums">
                            {formatPrice(splitPrepaid)}
                          </span>{' '}
                          now,{' '}
                          <span className="font-semibold text-ink-primary nums">
                            {formatPrice(splitBalance)}
                          </span>{' '}
                          on delivery.
                        </p>
                      </div>
                      {paymentMethod === 'split_cod' && (
                        <CheckCircle2 className="size-5 shrink-0 text-accent mt-0.5" aria-hidden="true" />
                      )}
                    </button>
                  )}
                </div>

                {/* Gateway selector — only when >1 active gateway and online payment */}
                {showGatewaySelector && (
                  <div className="mt-5">
                    <p className="px-1 text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                      Pay via
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {activeGateways.map((gw) => {
                        const active = resolvedGateway === gw.code;
                        return (
                          <button
                            key={gw.code}
                            type="button"
                            onClick={() => setSelectedGateway(gw.code)}
                            className={cn(
                              'rounded-sm border px-3 py-1.5 text-xs font-medium transition-colors',
                              active
                                ? 'border-accent bg-accent/8 text-accent'
                                : 'border-line-subtle bg-bg-elevated text-ink-secondary hover:border-line-strong hover:text-ink-primary',
                            )}
                          >
                            {gw.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {error && (
                  <div className="mt-5 flex items-start gap-2.5 rounded-lg border border-danger/35 bg-danger/8 p-3.5 text-sm text-danger shadow-glow-danger">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                    <span>{error}</span>
                  </div>
                )}
              </Card>
            </motion.div>
          </motion.div>

          {/* Right column — sticky summary */}
          <div className="lg:sticky lg:top-24 lg:self-start">
            <Card className="overflow-hidden p-0">
              {/* Accent band */}
              <div className="h-1 w-full bg-gradient-to-r from-accent via-accent-mid to-accent/60" aria-hidden="true" />

              <div className="p-5">
                <h2 className="text-h3 text-ink-primary tracking-tight">Order summary</h2>
                <div className="mt-3">
                  <FreeShippingNudge subtotal={subtotal} />
                </div>

                {/* Item list */}
                <motion.ul
                  variants={listStagger(0.04)}
                  initial="hidden"
                  animate="show"
                  className="mt-4 flex flex-col gap-1.5"
                >
                  {items.map((i) => (
                    <li
                      key={i.product_id}
                      className="flex items-baseline justify-between gap-3 text-sm text-ink-secondary"
                    >
                      <span className="truncate">
                        {i.name}{' '}
                        <span className="text-ink-tertiary nums">× {i.quantity}</span>
                      </span>
                      <span className="shrink-0 font-medium text-ink-primary nums">
                        {formatPrice(i.line_total)}
                      </span>
                    </li>
                  ))}
                </motion.ul>

                {/* Totals */}
                <dl className="mt-4 flex flex-col gap-2 border-t border-line-subtle pt-4 text-sm">
                  <div className="flex justify-between text-ink-secondary">
                    <dt>Subtotal</dt>
                    <dd className="font-medium text-ink-primary nums">{formatPrice(subtotal)}</dd>
                  </div>
                  {taxAmount > 0 && (
                    <div className="flex justify-between text-ink-secondary">
                      <dt>Tax</dt>
                      <dd className="font-medium text-ink-primary nums">{formatPrice(taxAmount)}</dd>
                    </div>
                  )}
                  {discountAmount > 0 && (
                    <div className="flex justify-between">
                      <dt className="flex items-center gap-1 text-success">
                        <TicketPercent className="size-3.5" aria-hidden="true" />
                        Discount
                        {couponCode && (
                          <span className="font-mono text-[10px] text-ink-tertiary">
                            ({couponCode})
                          </span>
                        )}
                      </dt>
                      <dd className="font-semibold text-success nums">−{formatPrice(discountAmount)}</dd>
                    </div>
                  )}
                  <div className="flex justify-between text-ink-secondary">
                    <dt>Delivery</dt>
                    {quote ? (
                      <dd className="font-medium text-ink-primary nums">{formatPrice(shippingAmount)}</dd>
                    ) : pincode ? (
                      <dd className="text-xs text-ink-tertiary">Calculating…</dd>
                    ) : (
                      <dd className="text-xs text-ink-tertiary">Add address</dd>
                    )}
                  </div>
                  {(paymentMethod === 'cod' || paymentMethod === 'split_cod') &&
                    codSurcharge > 0 && (
                      <div className="flex justify-between text-ink-secondary">
                        <dt>COD fee</dt>
                        <dd className="font-medium text-ink-primary nums">{formatPrice(codSurcharge)}</dd>
                      </div>
                    )}
                  {instrumentApplies && instrumentDiscount > 0 && activeInstrument && (
                    <div className="flex justify-between">
                      <dt className="flex items-center gap-1 text-success">
                        <span className="capitalize">{activeInstrument.label}</span>
                        <span className="font-mono text-[10px] text-ink-tertiary nums">
                          ({instrumentDiscountPct}% off)
                        </span>
                      </dt>
                      <dd className="font-semibold text-success nums">−{formatPrice(instrumentDiscount)}</dd>
                    </div>
                  )}
                  {paymentMethod === 'split_cod' && (
                    <div className="rounded-lg border border-line-subtle bg-bg-sunken px-3 py-2 text-[11px] text-ink-tertiary mt-1">
                      Now:{' '}
                      <span className="font-semibold text-ink-primary nums">{formatPrice(splitPrepaid)}</span>
                      {' · '}
                      On delivery:{' '}
                      <span className="font-semibold text-ink-primary nums">{formatPrice(splitBalance)}</span>
                    </div>
                  )}
                </dl>

                <div className="mt-4 flex items-baseline justify-between border-t border-line-subtle pt-4">
                  <span className="text-sm font-medium text-ink-secondary">Total</span>
                  <span className="text-h3 font-semibold text-ink-primary nums">
                    {formatPrice(displayedTotal)}
                  </span>
                </div>

                <Button
                  block
                  size="lg"
                  className="mt-5 accent-halo"
                  onClick={startPay}
                  disabled={!canSubmit}
                  loading={submitting}
                >
                  {paymentMethod === 'cod'
                    ? `Place order · ${formatPrice(displayedTotal)} on delivery`
                    : paymentMethod === 'split_cod'
                    ? `Pay ${formatPrice(splitPrepaid)} now`
                    : 'Pay with PhonePe'}
                </Button>

                <p className="mt-3 flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
                  <Lock className="size-3" aria-hidden="true" />
                  {paymentMethod === 'cod'
                    ? 'Carrier collects on delivery'
                    : paymentMethod === 'split_cod'
                    ? `${formatPrice(splitBalance)} collected on delivery`
                    : 'Encrypted handoff to PhonePe'}
                </p>

                <button
                  type="button"
                  onClick={() => navigate('/cart')}
                  className="mt-2 w-full text-center text-xs text-ink-tertiary hover:text-ink-secondary transition-colors"
                >
                  Cancel
                </button>
              </div>
            </Card>
          </div>
        </div>
      )}

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
