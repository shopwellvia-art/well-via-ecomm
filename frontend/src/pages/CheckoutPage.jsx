import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
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
  ChevronDown,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { Input } from '@/components/ui/Input.jsx';
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

export default function CheckoutPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [params] = useSearchParams();

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
  // Step expansion: 'address' | 'payment' — null means both collapsed (shouldn't happen in normal flow)
  const [activeStep, setActiveStep] = useState('address');

  const pincode = addressPayload.pincode || '';

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

  const addressDone = hasValidAddress && activeStep !== 'address';

  if (!user) return <Navigate to="/login" replace />;

  // Finding 1 & 16: fetch error guard — show retry banner instead of misleading empty state.
  if (!isLoading && isError) {
    return (
      <Page>
        <h1 className="text-xl font-semibold text-ink-primary">Checkout</h1>
        <div className="mt-8">
          <EmptyState
            icon={AlertTriangle}
            title="Could not load checkout"
            description="There was a problem loading your items. Please try again."
            action={
              <Button size="sm" onClick={() => refetch()}>
                Try again
              </Button>
            }
          />
        </div>
      </Page>
    );
  }

  // Finding 16: buy-now product not found guard.
  if (buyNowMode && !buyNowLoading && !buyNowError && buyNowProduct == null) {
    return (
      <Page>
        <h1 className="text-xl font-semibold text-ink-primary">Checkout</h1>
        <div className="mt-8">
          <EmptyState
            icon={AlertTriangle}
            title="Product not found"
            description="This product could not be found. Please browse and try again."
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

  if (!isLoading && items.length === 0) {
    return (
      <Page>
        <h1 className="text-xl font-semibold text-ink-primary">Checkout</h1>
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
        setPhoneError('Enter a valid phone number to receive the OTP.');
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
        className="mb-4"
      />

      {/* Finding 3: visually-hidden h1 so heading hierarchy is h1 → h2 */}
      <h1 className="sr-only">Checkout</h1>

      <Link
        to="/cart"
        className="inline-flex items-center gap-1.5 text-sm text-ink-secondary hover:text-ink-primary transition-colors focus-visible:focus-ring"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to cart
      </Link>

      {isLoading ? (
        <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_360px]">
          <div className="flex flex-col gap-3">
            <Skeleton className="h-48 rounded-sm" />
            <Skeleton className="h-64 rounded-sm" />
          </div>
          <Skeleton className="h-72 rounded-sm" />
        </div>
      ) : (
        <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_360px]">
          {/* ── Left column: stepped cards ── */}
          <div className="flex flex-col gap-3">

            {/* ══ STEP 1 — Delivery Address ══ */}
            <Card className="overflow-hidden p-0">
              <div
                role="button"
                aria-label="Delivery Address step"
                aria-expanded={activeStep === 'address'}
                tabIndex={0}
                className={cn(
                  'flex cursor-pointer select-none items-center px-5 py-4',
                  activeStep === 'address' ? 'bg-accent text-white' : 'bg-bg-elevated',
                )}
                onClick={() => activeStep !== 'address' && setActiveStep('address')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    if (activeStep !== 'address') setActiveStep('address');
                  }
                }}
              >
                <span
                  className={cn(
                    'mr-3 grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold',
                    activeStep === 'address'
                      ? 'bg-white/20 text-white'
                      : addressDone
                      ? 'bg-accent text-white'
                      : 'bg-accent/12 text-accent',
                  )}
                >
                  {addressDone && activeStep !== 'address' ? (
                    <CheckCircle2 className="size-3.5" aria-hidden="true" />
                  ) : (
                    '1'
                  )}
                </span>
                <span
                  className={cn(
                    'flex-1 text-sm font-bold uppercase tracking-wide',
                    activeStep === 'address' ? 'text-white' : 'text-ink-primary',
                  )}
                >
                  Delivery Address
                </span>
                {addressDone && activeStep !== 'address' && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); setActiveStep('address'); }}
                    className="text-xs font-semibold text-accent hover:underline focus-visible:focus-ring"
                  >
                    CHANGE
                  </button>
                )}
                {activeStep !== 'address' && !addressDone && (
                  <ChevronDown className="size-4 text-ink-tertiary" aria-hidden="true" />
                )}
              </div>

              <AnimatePresence initial={false}>
                {activeStep === 'address' && (
                  <motion.div
                    key="address-body"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2, ease: 'easeInOut' }}
                    className="overflow-hidden"
                  >
                    <div className="px-5 pb-5 pt-4">
                      <AddressPicker
                        onChange={(payload) => setAddressPayload(payload)}
                      />

                      {/* Billing address same as shipping checkbox */}
                      <div className="mt-4">
                        <label className="flex cursor-pointer items-center gap-2.5 rounded-sm border border-line-subtle bg-bg-sunken px-4 py-3 text-sm transition-colors hover:border-line-strong">
                          <input
                            type="checkbox"
                            checked={billingSameAsShipping}
                            onChange={(e) => {
                              setBillingSameAsShipping(e.target.checked);
                              if (e.target.checked) setBillingPayload({});
                            }}
                            className="size-4 rounded-xs border-line-subtle bg-bg-elevated text-accent"
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
                              <div className="mt-3 rounded-sm border border-line-subtle bg-bg-elevated p-4">
                                <p className="mb-3 text-[11px] font-bold uppercase tracking-widest text-ink-tertiary">
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

                      <div className="mt-4 border-t border-line-subtle pt-4">
                        <Input
                          id="ship-phone"
                          label={
                            codOtpRequired ? (
                              <span>
                                Phone number{' '}
                                <span className="ml-1 text-xs text-warning font-normal">
                                  (OTP required for COD)
                                </span>
                              </span>
                            ) : (
                              'Phone number'
                            )
                          }
                          type="tel"
                          inputMode="tel"
                          autoComplete="tel"
                          value={customerPhone}
                          placeholder="+91 98765 43210"
                          error={phoneError || undefined}
                          helper={
                            phoneError
                              ? undefined
                              : codOtpRequired
                              ? 'We use this for delivery updates and the COD verification SMS.'
                              : 'We use this for delivery updates.'
                          }
                          onChange={(e) => {
                            setCustomerPhone(e.target.value);
                            setPhoneUserEdited(true);
                            setOtpVerified(false);
                            if (phoneError) setPhoneError(null);
                          }}
                        />
                      </div>

                      <div className="mt-5">
                        <Button
                          variant="primary"
                          size="md"
                          disabled={!hasValidAddress}
                          onClick={() => hasValidAddress && setActiveStep('payment')}
                        >
                          Deliver here
                        </Button>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Collapsed summary of address step */}
              {addressDone && activeStep !== 'address' && (
                <div className="border-t border-line-subtle px-5 py-3 text-xs text-ink-secondary">
                  Delivering to pincode{' '}
                  <span className="font-semibold text-ink-primary nums">{pincode}</span>
                </div>
              )}
            </Card>

            {/* ══ STEP 2 — Payment Method ══ */}
            <Card className="overflow-hidden p-0">
              <div
                role="button"
                aria-label="Payment step"
                aria-expanded={activeStep === 'payment'}
                tabIndex={addressDone ? 0 : -1}
                aria-disabled={!addressDone}
                className={cn(
                  'flex select-none items-center px-5 py-4',
                  activeStep === 'payment'
                    ? 'bg-accent text-white cursor-default'
                    : addressDone
                    ? 'cursor-pointer bg-bg-elevated'
                    : 'cursor-not-allowed bg-bg-sunken opacity-70',
                )}
                onClick={() => addressDone && activeStep !== 'payment' && setActiveStep('payment')}
                onKeyDown={(e) => {
                  if ((e.key === 'Enter' || e.key === ' ') && addressDone) {
                    e.preventDefault();
                    if (activeStep !== 'payment') setActiveStep('payment');
                  }
                }}
              >
                <span
                  className={cn(
                    'mr-3 grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold',
                    activeStep === 'payment'
                      ? 'bg-white/20 text-white'
                      : 'bg-accent/12 text-accent',
                  )}
                >
                  2
                </span>
                <span
                  className={cn(
                    'flex-1 text-sm font-bold uppercase tracking-wide',
                    activeStep === 'payment' ? 'text-white' : 'text-ink-primary',
                  )}
                >
                  Payment
                </span>
              </div>

              <AnimatePresence initial={false}>
                {activeStep === 'payment' && (
                  <motion.div
                    key="payment-body"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2, ease: 'easeInOut' }}
                    className="overflow-hidden"
                  >
                    <div className="px-5 pb-5 pt-4">
                      <div className="flex flex-col gap-2">
                        {enabledInstruments.length > 0 && (
                          <p className="text-[10px] font-bold uppercase tracking-widest text-ink-tertiary px-0.5">
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
                                'flex w-full items-start gap-3 rounded-sm border p-4 text-left transition-colors duration-150',
                                selected
                                  ? 'border-accent bg-accent/12'
                                  : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm',
                              )}
                            >
                              <span
                                className={cn(
                                  'grid size-9 shrink-0 place-items-center rounded-sm transition-colors',
                                  selected ? 'bg-accent/12 text-accent' : 'bg-accent/12 text-accent',
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
                                    <span className="rounded-xs bg-success/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-success">
                                      Suggested
                                    </span>
                                  )}
                                  {discount > 0 && (
                                    <span className="rounded-xs bg-accent/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-accent">
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
                            'flex w-full items-start gap-3 rounded-sm border p-4 text-left transition-colors duration-150',
                            !codAvailable && 'cursor-not-allowed opacity-50',
                            codAvailable && paymentMethod === 'cod'
                              ? 'border-accent bg-accent/12'
                              : codAvailable
                              ? 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm'
                              : 'border-line-subtle bg-bg-elevated',
                          )}
                        >
                          <span
                            className={cn(
                              'grid size-9 shrink-0 place-items-center rounded-sm',
                              codAvailable && paymentMethod === 'cod'
                                ? 'bg-warning/12 text-warning'
                                : 'bg-warning/12 text-warning',
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
                                <span className="rounded-xs bg-warning/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-warning">
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
                              'flex w-full items-start gap-3 rounded-sm border p-4 text-left transition-colors duration-150',
                              paymentMethod === 'split_cod'
                                ? 'border-accent bg-accent/12'
                                : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm',
                            )}
                          >
                            <span
                              className={cn(
                                'grid size-9 shrink-0 place-items-center rounded-sm',
                                paymentMethod === 'split_cod'
                                  ? 'bg-info/12 text-info'
                                  : 'bg-info/12 text-info',
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
                                  <span className="rounded-xs bg-warning/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-warning">
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
                        <div className="mt-4">
                          <p className="px-0.5 text-[10px] font-bold uppercase tracking-wide text-ink-tertiary">
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
                                    'rounded-xs border px-3 py-1.5 min-h-[44px] text-xs font-medium transition-colors',
                                    active
                                      ? 'border-accent bg-accent/12 text-accent'
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
                        <div className="mt-4 flex items-start gap-2.5 rounded-sm border border-danger/35 bg-danger/8 p-3.5 text-sm text-danger">
                          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                          <span>{error}</span>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </Card>
          </div>

          {/* ── Right rail: sticky price summary + place-order CTA ── */}
          <div className="lg:sticky lg:top-24 lg:self-start">
            <Card className="overflow-hidden p-0">
              <h2 className="border-b border-line-subtle px-5 py-3 text-[11px] font-bold uppercase tracking-widest text-ink-tertiary">
                Price Details
              </h2>

              <div className="px-5 py-4">
                <FreeShippingNudge subtotal={subtotal} />

                {/* Item list */}
                <ul className="mt-3 flex flex-col gap-1.5">
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
                </ul>

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
                      shippingAmount === 0 ? (
                        <dd className="font-semibold text-success">Free</dd>
                      ) : (
                        <dd className="font-medium text-ink-primary nums">{formatPrice(shippingAmount)}</dd>
                      )
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
                    <div className="rounded-xs border border-line-subtle bg-bg-sunken px-3 py-2 text-[11px] text-ink-tertiary mt-1">
                      Now:{' '}
                      <span className="font-semibold text-ink-primary nums">{formatPrice(splitPrepaid)}</span>
                      {' · '}
                      On delivery:{' '}
                      <span className="font-semibold text-ink-primary nums">{formatPrice(splitBalance)}</span>
                    </div>
                  )}
                </dl>

                <div className="mt-4 flex items-baseline justify-between border-t border-dashed border-line-strong pt-4">
                  <span className="text-sm font-bold text-ink-primary">Total Amount</span>
                  <span className="text-lg font-bold text-ink-primary nums">
                    {formatPrice(displayedTotal)}
                  </span>
                </div>
              </div>

              <div className="border-t border-line-subtle px-5 pb-5 pt-4">
                <Button
                  variant="cta"
                  block
                  size="lg"
                  onClick={startPay}
                  disabled={!canSubmit || activeStep !== 'payment'}
                  loading={submitting}
                >
                  {paymentMethod === 'cod'
                    ? `PLACE ORDER · ${formatPrice(displayedTotal)} COD`
                    : paymentMethod === 'split_cod'
                    ? `PAY ${formatPrice(splitPrepaid)} NOW`
                    : 'PLACE ORDER'}
                </Button>

                <p className="mt-3 flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
                  <Lock className="size-3" aria-hidden="true" />
                  {paymentMethod === 'cod'
                    ? 'Carrier collects on delivery'
                    : paymentMethod === 'split_cod'
                    ? `${formatPrice(splitBalance)} collected on delivery`
                    : 'Secure encrypted payment'}
                </p>

                <button
                  type="button"
                  onClick={() => navigate('/cart')}
                  className="mt-3 w-full text-center text-xs text-ink-tertiary hover:text-ink-secondary transition-colors focus-visible:focus-ring"
                >
                  Cancel and go back to cart
                </button>
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* Findings 4 & 10: mobile/tablet sticky bottom CTA bar — hidden on lg+ where the rail CTA is already visible */}
      {!isLoading && items.length > 0 && (
        <>
          {/* Spacer so content isn't hidden behind the fixed bar */}
          <div className="h-20 lg:hidden" aria-hidden="true" />
          <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line-subtle bg-bg-surface px-4 pb-4 pt-3 shadow-lg lg:hidden">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs text-ink-tertiary">Total</p>
                <p className="text-base font-bold text-ink-primary nums">
                  {formatPrice(displayedTotal)}
                </p>
              </div>
              <Button
                variant="cta"
                size="md"
                onClick={startPay}
                disabled={!canSubmit || activeStep !== 'payment'}
                loading={submitting}
                className="shrink-0"
              >
                {paymentMethod === 'cod'
                  ? 'PLACE ORDER (COD)'
                  : paymentMethod === 'split_cod'
                  ? `PAY ${formatPrice(splitPrepaid)} NOW`
                  : 'PLACE ORDER'}
              </Button>
            </div>
          </div>
        </>
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
