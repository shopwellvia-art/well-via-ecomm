import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Loader2,
  MapPin,
  LocateFixed,
  TicketPercent,
  Zap,
} from 'lucide-react';
import { LockIcon, CloseIcon, ShieldIcon } from '@/components/storefront/Icons';
import WImage from '@/components/storefront/WImage';
import {
  useCart,
  useRemoveFromCart,
  useApplyCoupon,
  useRemoveCoupon,
  useUpdateCartQuantity,
} from '@/features/cart/hooks.js';
import { useWishlist, useAddToWishlist } from '@/features/wishlist/hooks.js';
import {
  useAddresses,
  useCurrentLocation,
  friendlyGeoError,
} from '@/features/addresses/hooks.js';
import AddressSelectDrawer from '@/features/addresses/components/AddressSelectDrawer.jsx';
import { toast } from '@/components/ui/Toaster.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import FreeShippingNudge from '@/features/shipping/components/FreeShippingNudge.jsx';
import { useRateQuote, useServiceability } from '@/features/shipping/hooks.js';
import { cartUnitCount } from '@/features/cart/summary.js';
import { cn, formatPrice } from '@/lib/utils.js';

const LABEL_TEXT = { home: 'Home', work: 'Work', other: 'Other' };

/* ── Coupon block ─────────────────────────────────────────────────────── */
function CouponBlock({ appliedCode, discount }) {
  const [code, setCode] = useState('');
  const [error, setError] = useState(null);
  const apply = useApplyCoupon();
  const remove = useRemoveCoupon();

  async function handleApply(e) {
    e.preventDefault();
    setError(null);
    const trimmed = code.trim();
    if (!trimmed) {
      setError('Enter a coupon code.');
      return;
    }
    try {
      await apply.mutateAsync(trimmed.toUpperCase());
      setCode('');
    } catch (err) {
      setError(err.response?.data?.error?.message || "That code couldn't be applied.");
    }
  }

  if (appliedCode) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-wgold/30 bg-wgold/10 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="grid size-8 place-items-center rounded-full bg-wgold/20 text-wgold">
            <TicketPercent className="size-4" aria-hidden="true" />
          </div>
          <div>
            <p className="font-mono text-sm font-semibold tracking-wider text-wink">
              {appliedCode}
            </p>
            <p className="text-xs text-wgold">Saving {formatPrice(discount)}</p>
          </div>
        </div>
        <button
          type="button"
          aria-label={`Remove coupon ${appliedCode}`}
          disabled={remove.isPending}
          onClick={() => remove.mutate()}
          className="grid size-8 place-items-center rounded-full text-wmuted transition-colors hover:bg-red-50 hover:text-red-500 disabled:opacity-50"
        >
          <CloseIcon size={16} />
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleApply}>
      <p className="mb-2 text-sm font-medium text-wmuted">Have a coupon?</p>
      <div className="flex items-start gap-2">
        <div className="flex-1">
          <input
            type="text"
            placeholder="WELCOME10"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="w-full rounded-full border border-wline bg-wpaper px-4 py-2.5 text-sm uppercase text-wink placeholder:normal-case placeholder:text-wmuted/60 transition-colors focus:border-wgreen/60 focus:outline-none"
          />
          {error && (
            <p className="mt-1 px-2 text-xs text-red-500">{error}</p>
          )}
        </div>
        <button
          type="submit"
          disabled={apply.isPending}
          className="shrink-0 rounded-full border border-wline bg-wpaper px-5 py-2.5 text-sm font-semibold text-wink transition-colors hover:border-wgreen hover:text-wgreen disabled:opacity-50"
        >
          {apply.isPending ? 'Applying…' : 'Apply'}
        </button>
      </div>
    </form>
  );
}

/* ── Delivery ETA helper ──────────────────────────────────────────────── */
function deliveryByText(serviceability) {
  const days = serviceability?.eta_days_max ?? serviceability?.eta_days_min;
  if (!serviceability?.serviceable || !days) return null;
  const date = new Date();
  date.setDate(date.getDate() + Number(days));
  return date.toLocaleDateString('en-IN', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

/* ── Deliver-to bar ───────────────────────────────────────────────────── */
function DeliverToBar({
  address,
  detectedPincode,
  serviceability,
  onChange,
  onDetect,
  detectPending,
  detectError,
}) {
  const unserviceable = serviceability && !serviceability.serviceable;

  if (!address) {
    return (
      <div className="border-b border-wline bg-wpaper/60 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-wmuted">
            <MapPin className="size-4 shrink-0 text-wgold" aria-hidden="true" />
            {detectedPincode ? (
              <span>
                Deliver to{' '}
                <span className="font-semibold text-wink">{detectedPincode}</span>
                <span className="ml-1">(your location)</span>
              </span>
            ) : (
              <span>Add a delivery address to see delivery dates and charges.</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={detectPending}
              onClick={onDetect}
              className="inline-flex items-center gap-1.5 text-xs font-semibold text-wgreen transition-colors hover:text-wgreen-dark disabled:opacity-50"
            >
              {detectPending ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <LocateFixed className="size-3.5" aria-hidden="true" />
              )}
              {detectPending ? 'Detecting…' : 'Detect my location'}
            </button>
            <span className="text-wmuted">·</span>
            <button
              type="button"
              onClick={onChange}
              className="text-xs font-semibold text-wgreen transition-colors hover:text-wgreen-dark"
            >
              Add address
            </button>
          </div>
        </div>
        {detectError && (
          <p className="mt-2 text-xs text-red-500">{detectError}</p>
        )}
      </div>
    );
  }

  const labelText = LABEL_TEXT[String(address.label).toLowerCase()] || 'Other';

  return (
    <div className="border-b border-wline bg-wpaper/60 px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <MapPin className="size-4 shrink-0 text-wgold" aria-hidden="true" />
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-wmuted">Deliver to:</span>
              <span className="font-semibold text-wink">
                {address.full_name}, {address.pincode}
              </span>
              <span className="rounded-full bg-wcanvas px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-wmuted">
                {labelText}
              </span>
            </p>
            <p className="mt-0.5 truncate text-xs text-wmuted">
              {address.line1}
              {address.line2 ? `, ${address.line2}` : ''}, {address.city}, {address.state}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onChange}
          className="shrink-0 text-sm font-semibold text-wgreen transition-colors hover:text-wgreen-dark"
        >
          Change
        </button>
      </div>
      {unserviceable && (
        <p className="mt-2 flex items-center gap-1.5 border-t border-wline pt-2.5 text-xs text-red-500">
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
          Sorry — we don&apos;t deliver to {address.pincode} yet. Try another address.
        </p>
      )}
    </div>
  );
}

/* ── Mobile sticky bottom bar ─────────────────────────────────────────── */
function PlaceOrderBar({ total, disabled, onPlaceOrder }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-wmuted">Total</p>
        <p className="font-wserif text-2xl text-wink">{formatPrice(total)}</p>
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={onPlaceOrder}
        className="rounded-full bg-wgreen px-8 py-3 text-sm font-semibold text-white transition-colors hover:bg-wgreen-dark disabled:pointer-events-none disabled:opacity-50"
      >
        Place Order
      </button>
    </div>
  );
}

/* ── Main page ────────────────────────────────────────────────────────── */
export default function CartPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useCart();
  const removeItem = useRemoveFromCart();
  const updateQty = useUpdateCartQuantity();
  const addToWishlist = useAddToWishlist();
  const { data: wishlist } = useWishlist();
  const { data: addresses } = useAddresses();

  // Deliver-to address: user's pick, else default, else first saved.
  const [pickedAddressId, setPickedAddressId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Which item's "Save for later" is in flight (product_id).
  const [savingForLater, setSavingForLater] = useState(null);
  // Per-item pending state for remove and qty update (product_id).
  const [removingId, setRemovingId] = useState(null);
  const [updatingId, setUpdatingId] = useState(null);
  // Pincode detected via geolocation (no saved address path).
  const [detectedPincode, setDetectedPincode] = useState('');
  const [detectError, setDetectError] = useState(null);
  const detectLocation = useCurrentLocation();

  const selectedAddress = useMemo(() => {
    if (!addresses?.length) return null;
    return (
      addresses.find((a) => a.id === pickedAddressId) ||
      addresses.find((a) => a.is_default) ||
      addresses[0]
    );
  }, [addresses, pickedAddressId]);

  const items = useMemo(() => data?.items ?? [], [data]);
  // Units, not lines. The header badge already counts units, and "Price (N items)"
  // sits next to an amount covering every unit — counting lines here made a
  // 3-unit cart read "Price (1 item) ₹1,197.00".
  const unitCount = useMemo(() => cartUnitCount(items), [items]);
  const subtotal = Number(data?.subtotal ?? 0);
  const taxAmount = Number(data?.tax_amount ?? 0);
  const discountAmount = Number(data?.discount_amount ?? 0);
  const cartTotal = Number(data?.total ?? subtotal + taxAmount - discountAmount);
  const couponCode = data?.coupon_code;
  const status = error?.response?.status;

  const pincode = selectedAddress?.pincode || detectedPincode || '';
  const { data: serviceability, isLoading: svcLoading } = useServiceability(pincode);
  const serviceable = serviceability?.serviceable ?? null;

  const rateInputItems = useMemo(
    () =>
      serviceable
        ? items.map((i) => ({ product_id: i.product_id, quantity: i.quantity }))
        : [],
    [serviceable, items],
  );
  const { data: quote } = useRateQuote(serviceable ? pincode : '', rateInputItems);
  const shippingAmount = quote ? Number(quote.amount) : 0;
  const total = cartTotal + shippingAmount;

  // MRP math for the Price Details rail — mirrors Flipkart's "Price / Discount" rows.
  const { mrpTotal, mrpSavings } = useMemo(() => {
    let mrp = 0;
    for (const item of items) {
      const compareAt = Number(item.compare_at_price || 0);
      const unit = Number(item.unit_price);
      mrp += (compareAt > unit ? compareAt : unit) * item.quantity;
    }
    return { mrpTotal: mrp, mrpSavings: Math.max(0, mrp - subtotal) };
  }, [items, subtotal]);
  const totalSavings = mrpSavings + discountAmount;

  const deliveryBy = deliveryByText(serviceability);
  const wishlistIds = useMemo(
    () => new Set((wishlist ?? []).map((w) => w.product_id)),
    [wishlist],
  );

  // Stale pick (address deleted elsewhere) → fall back to default.
  useEffect(() => {
    if (pickedAddressId && addresses && !addresses.some((a) => a.id === pickedAddressId)) {
      setPickedAddressId(null);
    }
  }, [addresses, pickedAddressId]);

  async function handleSaveForLater(item) {
    setSavingForLater(item.product_id);
    try {
      if (!wishlistIds.has(item.product_id)) {
        await addToWishlist.mutateAsync(item.product_id);
      }
      await removeItem.mutateAsync(item.product_id);
    } catch (err) {
      toast.error(
        err?.response?.data?.error?.message ||
          'Could not save this item for later. Please try again.',
      );
    } finally {
      setSavingForLater(null);
    }
  }

  function handleRemoveItem(productId) {
    setRemovingId(productId);
    removeItem.mutate(productId, {
      onError: (err) =>
        toast.error(
          err?.response?.data?.error?.message ||
            'Could not remove this item. Please try again.',
        ),
      onSettled: () => setRemovingId(null),
    });
  }

  function handleUpdateQty(productId, quantity) {
    setUpdatingId(productId);
    updateQty.mutate(
      { productId, quantity },
      {
        onError: (err) =>
          toast.error(
            err?.response?.data?.error?.message ||
              'Could not update the quantity. Please try again.',
          ),
        onSettled: () => setUpdatingId(null),
      },
    );
  }

  function handleDetectLocation() {
    setDetectError(null);
    detectLocation.mutate(undefined, {
      onSuccess(data) {
        if (data.found) {
          setDetectedPincode(data.pincode || '');
        } else {
          setDetectError(
            "We couldn't find a pincode for your location — please enter your address manually.",
          );
        }
      },
      onError(err) {
        setDetectError(friendlyGeoError(err));
      },
    });
  }

  function placeOrder() {
    navigate('/checkout');
  }

  /* ── Auth gate — only for stale signed-in sessions (server cart 401).
        Guests get the client-side cart, matching the drawer's guest flow. ── */
  if (status === 401) {
    return (
      <div className="mx-auto flex min-h-[60vh] max-w-[1320px] flex-col items-center justify-center px-4 py-20 text-center">
        <div className="mb-5 grid size-16 place-items-center rounded-full bg-wgold/10 text-wgold">
          <LockIcon size={28} stroke="#B49A63" />
        </div>
        <h2 className="font-wserif mb-2 text-3xl font-medium text-wink">
          Sign in to view your cart
        </h2>
        <p className="mb-8 max-w-xs text-wmuted">
          Your cart is saved to your account so it&apos;s here wherever you shop.
        </p>
        <Link
          to="/login"
          className="rounded-full bg-wgreen px-8 py-3.5 text-sm font-semibold text-white transition-colors hover:bg-wgreen-dark"
        >
          Sign in
        </Link>
      </div>
    );
  }

  /* ── Error state ── */
  if (isError) {
    return (
      <div className="mx-auto flex min-h-[60vh] max-w-[1320px] flex-col items-center justify-center px-4 py-20 text-center">
        <div className="mb-5 grid size-16 place-items-center rounded-full bg-red-50 text-red-400">
          <AlertTriangle className="size-8" aria-hidden="true" />
        </div>
        <h2 className="font-wserif mb-2 text-3xl font-medium text-wink">
          We couldn&apos;t load your cart
        </h2>
        <p className="mb-8 max-w-xs text-wmuted">
          Something went wrong on our end. Please try again.
        </p>
        <button
          onClick={() => refetch()}
          className="rounded-full bg-wgreen px-8 py-3.5 text-sm font-semibold text-white transition-colors hover:bg-wgreen-dark"
        >
          Retry
        </button>
      </div>
    );
  }

  /* ── Main ── */
  return (
    <div className="mx-auto max-w-[1320px] px-4 py-8 sm:px-6 lg:px-8">
      {/* Breadcrumb */}
      <nav
        className="mb-6 flex items-center gap-2 text-sm text-wmuted"
        aria-label="Breadcrumb"
      >
        <Link to="/" className="transition-colors hover:text-wink">
          Home
        </Link>
        <span>/</span>
        <span className="text-wink">Cart</span>
      </nav>

      {isLoading ? (
        /* ── Loading skeleton ── */
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_380px]">
          <div className="flex flex-col gap-4">
            <div className="h-16 animate-pulse rounded-xl2 bg-wcanvas" />
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-40 animate-pulse rounded-xl2 bg-wcanvas" />
            ))}
          </div>
          <div className="h-96 animate-pulse rounded-xl3 bg-wcanvas" />
        </div>
      ) : items.length === 0 ? (
        /* ── Empty state ── */
        <div className="flex flex-col items-center justify-center py-24 text-center animate-rise">
          <div className="mb-6 grid size-20 place-items-center rounded-full bg-wgold/10 text-wgold">
            {/* Shopping bag */}
            <svg
              width="36"
              height="36"
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
          <h2 className="font-wserif mb-2 text-[clamp(26px,4vw,40px)] font-medium text-wink">
            Your cart is empty
          </h2>
          <p className="mb-8 max-w-xs text-wmuted">
            Browse the collection and add something you love.
          </p>
          <Link
            to="/products"
            className="rounded-full bg-wgreen px-8 py-3.5 text-sm font-semibold text-white transition-colors hover:bg-wgreen-dark"
          >
            Continue Shopping
          </Link>
        </div>
      ) : (
        /* ── Filled cart ── */
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1fr_380px]">

          {/* ── LEFT: item list ── */}
          <section>
            {/* Cart heading + deliver-to quick label */}
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
              <h1 className="font-wserif text-[clamp(28px,4vw,44px)] font-medium text-wink">
                Your Cart{' '}
                <span className="text-wmuted text-2xl font-light">
                  ({unitCount})
                </span>
              </h1>
              {(selectedAddress || detectedPincode) && (
                <span className="text-sm text-wmuted">
                  Deliver to{' '}
                  <b className="font-semibold text-wink">
                    {selectedAddress?.pincode || detectedPincode}
                  </b>
                </span>
              )}
            </div>

            {/* Card: deliver-to bar + item rows */}
            <div className="overflow-hidden rounded-xl2 border border-wline bg-wcard">
              <DeliverToBar
                address={selectedAddress}
                detectedPincode={detectedPincode}
                serviceability={serviceability}
                onChange={() =>
                  user ? setDrawerOpen(true) : navigate('/login?next=/cart')
                }
                onDetect={handleDetectLocation}
                detectPending={detectLocation.isPending}
                detectError={detectError}
              />

              <ul className="divide-y divide-wline">
                {items.map((item) => {
                  const mrp =
                    item.compare_at_price &&
                    Number(item.compare_at_price) > Number(item.unit_price)
                      ? Number(item.compare_at_price)
                      : null;
                  const savePct = mrp
                    ? Math.round(((mrp - Number(item.unit_price)) / mrp) * 100)
                    : 0;
                  const busy =
                    savingForLater === item.product_id ||
                    removingId === item.product_id ||
                    updatingId === item.product_id;

                  return (
                    <li key={item.product_id} className="flex gap-4 p-5 sm:gap-5">
                      {/* Thumbnail */}
                      <Link
                        to={`/products/${item.product_id}`}
                        aria-label={item.name}
                        className="size-[88px] shrink-0 sm:size-[104px]"
                      >
                        <WImage
                          src={item.image_url}
                          alt={item.name}
                          shape="rounded"
                          className="size-full border border-wline"
                        />
                      </Link>

                      {/* Details */}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            {/* Brand eyebrow */}
                            {item.brand && (
                              <p className="text-[11px] font-bold uppercase tracking-wider text-wmuted">
                                {item.brand}
                              </p>
                            )}
                            {/* Product name */}
                            <Link
                              to={`/products/${item.product_id}`}
                              className="font-wserif line-clamp-2 text-[18px] leading-snug text-wink transition-colors hover:text-wgreen"
                            >
                              {item.name}
                            </Link>
                            <p className="mt-0.5 text-xs text-wmuted">
                              Seller: ShopWell Retail
                            </p>
                          </div>
                          {/* Rating pill */}
                          {item.rating && (
                            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-wgold/15 px-2 py-0.5 text-xs font-semibold text-wgold">
                              {Number(item.rating).toFixed(1)}&nbsp;★
                            </span>
                          )}
                        </div>

                        {/* Price row */}
                        <div className="mt-2 flex flex-wrap items-baseline gap-2">
                          <span className="font-wserif text-xl text-wink">
                            {formatPrice(item.unit_price)}
                          </span>
                          {mrp && (
                            <span className="text-sm text-wmuted line-through">
                              {formatPrice(mrp)}
                            </span>
                          )}
                          {savePct > 0 && (
                            <span className="text-sm font-semibold text-wgold">
                              {savePct}% off
                            </span>
                          )}
                        </div>

                        {Number(item.line_tax) > 0 && (
                          <p className="mt-0.5 text-[11px] text-wmuted">
                            incl. {formatPrice(item.line_tax)} tax · line total{' '}
                            {formatPrice(item.line_total)}
                          </p>
                        )}

                        {deliveryBy && (
                          <p className="mt-1 text-xs text-wmuted">
                            Delivery by{' '}
                            <span className="font-semibold text-wgreen">{deliveryBy}</span>
                          </p>
                        )}

                        {/* Controls row */}
                        <div className="mt-3.5 flex flex-wrap items-center gap-4">
                          {/* Quantity stepper — pill shape, wellness style */}
                          <div
                            className="inline-flex items-center overflow-hidden rounded-full border border-wline"
                            aria-label={`Quantity for ${item.name}`}
                          >
                            <button
                              type="button"
                              disabled={busy || item.quantity <= 1}
                              aria-label="Decrease quantity"
                              onClick={() =>
                                handleUpdateQty(item.product_id, item.quantity - 1)
                              }
                              className="px-3.5 py-2 text-[15px] text-wink transition-colors hover:text-wgreen disabled:opacity-40"
                            >
                              −
                            </button>
                            <span className="min-w-[24px] text-center text-sm font-semibold text-wink">
                              {updatingId === item.product_id ? (
                                <Loader2
                                  className="mx-auto size-3.5 animate-spin text-wmuted"
                                  aria-hidden="true"
                                />
                              ) : (
                                item.quantity
                              )}
                            </span>
                            <button
                              type="button"
                              disabled={busy}
                              aria-label="Increase quantity"
                              onClick={() =>
                                handleUpdateQty(item.product_id, item.quantity + 1)
                              }
                              className="px-3.5 py-2 text-[15px] text-wink transition-colors hover:text-wgreen disabled:opacity-40"
                            >
                              +
                            </button>
                          </div>

                          {/* Save for later — wishlist is per-account, so signed-in only */}
                          {user && (
                            <button
                              type="button"
                              disabled={busy}
                              aria-label={`Save ${item.name} for later`}
                              onClick={() => handleSaveForLater(item)}
                              className="text-sm font-medium text-wmuted transition-colors hover:text-wgreen disabled:opacity-50"
                            >
                              {savingForLater === item.product_id
                                ? 'Saving…'
                                : 'Save for later'}
                            </button>
                          )}

                          {/* Remove */}
                          <button
                            type="button"
                            disabled={busy}
                            aria-label={`Remove ${item.name} from cart`}
                            onClick={() => handleRemoveItem(item.product_id)}
                            className="text-sm font-medium text-wmuted transition-colors hover:text-red-500 disabled:opacity-50"
                          >
                            Remove
                          </button>

                          {/* Buy now */}
                          <button
                            type="button"
                            disabled={busy || serviceable === false}
                            aria-label={`Buy ${item.name} now`}
                            onClick={() =>
                              navigate(
                                `/checkout?buyNow=${item.product_id}&qty=${item.quantity}`,
                              )
                            }
                            className="inline-flex items-center gap-1 text-sm font-semibold text-wgold transition-colors hover:text-wgold/75 disabled:opacity-50"
                          >
                            <Zap className="size-3.5" aria-hidden="true" />
                            Buy this now
                          </button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>

          {/* ── RIGHT: order summary rail ── */}
          <aside className="lg:sticky lg:top-[120px] lg:self-start">

            {/* Price Details card */}
            <div className="overflow-hidden rounded-xl3 border border-wline bg-wcard">
              {/* Card header */}
              <div className="border-b border-wline px-6 py-4">
                <h2 className="font-wserif text-xl font-semibold text-wink">
                  Order Summary
                </h2>
              </div>

              {/* Line items */}
              <div className="space-y-3 px-6 py-5 text-sm">
                <FreeShippingNudge subtotal={subtotal} />

                <div className="flex justify-between">
                  <span className="text-wmuted">
                    Price ({unitCount} item{unitCount === 1 ? '' : 's'})
                  </span>
                  <span className="font-medium text-wink">{formatPrice(mrpTotal)}</span>
                </div>

                {mrpSavings > 0 && (
                  <div className="flex justify-between">
                    <span className="text-wmuted">Discount</span>
                    <span className="font-semibold text-wgold">
                      −{formatPrice(mrpSavings)}
                    </span>
                  </div>
                )}

                {discountAmount > 0 && (
                  <div className="flex justify-between">
                    <span className="text-wmuted">
                      Coupon
                      {couponCode && (
                        <span className="ml-1 font-mono text-[10px] text-wmuted">
                          ({couponCode})
                        </span>
                      )}
                    </span>
                    <span className="font-semibold text-wgold">
                      −{formatPrice(discountAmount)}
                    </span>
                  </div>
                )}

                {taxAmount > 0 && (
                  <div className="flex justify-between">
                    <span className="text-wmuted">Taxes</span>
                    <span className="font-medium text-wink">{formatPrice(taxAmount)}</span>
                  </div>
                )}

                <div className="flex justify-between">
                  <span className="text-wmuted">Delivery charges</span>
                  {quote ? (
                    shippingAmount === 0 ? (
                      <span className="font-semibold text-wgreen">FREE</span>
                    ) : (
                      <span className="font-medium text-wink">
                        {formatPrice(shippingAmount)}
                      </span>
                    )
                  ) : (
                    <span className="text-xs text-wmuted">
                      {selectedAddress ? 'Calculating…' : 'FREE'}
                    </span>
                  )}
                </div>

                {/* Total row */}
                <div className="flex items-baseline justify-between border-t border-dashed border-wline pt-4">
                  <span className="text-base font-semibold text-wink">Total</span>
                  <span className="font-wserif text-2xl text-wink">
                    {formatPrice(total)}
                  </span>
                </div>
              </div>

              {/* Savings banner */}
              {totalSavings > 0 && (
                <div className="border-t border-wline bg-wgold/10 px-6 py-3">
                  <p className="text-sm font-semibold text-wgold">
                    You save {formatPrice(totalSavings)} on this order
                  </p>
                </div>
              )}

              {/* Place Order CTA */}
              <div className="px-6 pb-6 pt-4">
                <button
                  type="button"
                  disabled={(svcLoading && !!pincode) || serviceable === false}
                  onClick={placeOrder}
                  className={cn(
                    'w-full rounded-full bg-wgreen py-4 text-sm font-semibold tracking-wide text-white',
                    'transition-colors hover:bg-wgreen-dark',
                    'disabled:pointer-events-none disabled:opacity-50',
                  )}
                >
                  Proceed to Checkout
                </button>
                {serviceable === false && (
                  <p className="mt-2 text-center text-xs text-red-500">
                    Delivery isn&apos;t available at the selected address.
                  </p>
                )}
              </div>

              {/* Encrypted checkout badge */}
              <div className="flex items-center justify-center gap-1.5 border-t border-wline px-6 py-3.5 text-xs text-wmuted">
                <LockIcon size={13} stroke="#B49A63" />
                <span>Checkout is encrypted &amp; secure</span>
              </div>
            </div>

            {/* Coupon card — coupons apply to server carts only (drawer parity) */}
            <div className="mt-4 rounded-xl2 border border-wline bg-wcard p-5">
              {user ? (
                <CouponBlock appliedCode={couponCode} discount={discountAmount} />
              ) : (
                <div>
                  <p className="mb-2 text-sm font-medium text-wmuted">Have a coupon?</p>
                  <Link
                    to="/login?next=/cart"
                    className="text-sm font-semibold text-wgreen underline underline-offset-4 hover:text-wgreen-dark"
                  >
                    Log in to apply coupons
                  </Link>
                </div>
              )}
            </div>

            {/* Continue shopping link */}
            <div className="mt-4 text-center">
              <Link
                to="/products"
                className="text-sm font-medium text-wgreen underline underline-offset-4 hover:text-wgreen-dark"
              >
                Continue Shopping
              </Link>
            </div>

            {/* Shield assurance */}
            <div className="mt-4 flex items-start gap-2.5 rounded-xl2 border border-wline bg-wcard px-4 py-3.5 text-sm text-wmuted">
              <ShieldIcon size={20} stroke="#183A2E" strokeWidth={1.5} />
              <span>
                Safe and secure payments. Easy returns. 100% authentic products.
              </span>
            </div>
          </aside>

          {/* ── Mobile sticky place-order bar ── */}
          <div
            className="fixed inset-x-0 bottom-0 z-40 border-t border-wline bg-wcard px-4 pt-3 shadow-lg lg:hidden"
            style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
          >
            <PlaceOrderBar
              total={total}
              disabled={(svcLoading && !!pincode) || serviceable === false}
              onPlaceOrder={placeOrder}
            />
          </div>
          {/* Spacer so the fixed bar doesn't cover the rail on mobile */}
          <div className="h-24 lg:hidden" aria-hidden="true" />
        </div>
      )}

      {/* Address select drawer — preserved exactly */}
      <AddressSelectDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        selectedId={selectedAddress?.id ?? null}
        onSelect={(addr) => setPickedAddressId(addr.id)}
      />
    </div>
  );
}
