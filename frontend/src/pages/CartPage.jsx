import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  ShoppingBag,
  ArrowRight,
  Lock,
  AlertTriangle,
  TicketPercent,
  X,
  MapPin,
  ShieldCheck,
  Zap,
  Info,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import {
  useCart,
  useRemoveFromCart,
  useApplyCoupon,
  useRemoveCoupon,
  useUpdateCartQuantity,
} from '@/features/cart/hooks.js';
import { useWishlist, useAddToWishlist } from '@/features/wishlist/hooks.js';
import { useAddresses } from '@/features/addresses/hooks.js';
import AddressSelectDrawer from '@/features/addresses/components/AddressSelectDrawer.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import FreeShippingNudge from '@/features/shipping/components/FreeShippingNudge.jsx';
import { useRateQuote, useServiceability } from '@/features/shipping/hooks.js';
import { formatPrice } from '@/lib/utils.js';
import { fadeUp, staggerContainer } from '@/lib/motion.js';

const LABEL_TEXT = { home: 'Home', work: 'Work', other: 'Other' };

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
      <div className="flex items-center justify-between rounded-lg border border-success/25 bg-success/8 px-3 py-2.5 text-sm shadow-glow-success">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-success/12 text-success">
            <TicketPercent className="size-3.5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="truncate font-mono text-xs font-semibold text-ink-primary nums">
              {appliedCode}
            </p>
            <p className="text-[11px] text-success">Saving {formatPrice(discount)}</p>
          </div>
        </div>
        <button
          type="button"
          aria-label={`Remove coupon ${appliedCode}`}
          disabled={remove.isPending}
          onClick={() => remove.mutate()}
          className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring disabled:opacity-50"
        >
          <X className="size-4" />
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleApply}>
      <p className="mb-1.5 text-xs font-medium text-ink-secondary">Have a coupon?</p>
      <div className="flex items-start gap-2">
        <div className="flex-1">
          <Input
            placeholder="WELCOME10"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            error={error}
            className="uppercase placeholder:normal-case"
          />
        </div>
        <Button type="submit" size="md" variant="outline" loading={apply.isPending}>
          Apply
        </Button>
      </div>
    </form>
  );
}

/** "Delivery by Thu, Jun 18" — from the serviceability ETA (max days). */
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

/** Flipkart-style "Deliver to:" bar with the address-change drawer trigger. */
function DeliverToBar({ address, serviceability, onChange }) {
  if (!address) {
    return (
      <Card className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-2.5 text-sm text-ink-secondary">
          <MapPin className="size-4 text-ink-tertiary" aria-hidden="true" />
          Add a delivery address to see delivery dates and charges.
        </div>
        <Button type="button" size="sm" variant="outline" onClick={onChange}>
          Add address
        </Button>
      </Card>
    );
  }

  const labelText = LABEL_TEXT[String(address.label).toLowerCase()] || 'Other';
  const unserviceable = serviceability && !serviceability.serviceable;

  return (
    <Card className="px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-ink-secondary">Deliver to:</span>
            <span className="font-semibold text-ink-primary">
              {address.full_name}, <span className="nums">{address.pincode}</span>
            </span>
            <span className="rounded-sm bg-fill px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-secondary">
              {labelText}
            </span>
          </p>
          <p className="mt-0.5 truncate text-xs text-ink-tertiary">
            {address.line1}
            {address.line2 ? `, ${address.line2}` : ''}, {address.city}, {address.state}
          </p>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={onChange}>
          Change
        </Button>
      </div>
      {unserviceable && (
        <p className="mt-2 flex items-start gap-1.5 border-t border-line-subtle pt-2 text-xs text-danger">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          Sorry — we don&apos;t deliver to {address.pincode} yet. Try another address.
        </p>
      )}
    </Card>
  );
}

/** Bottom "Place Order" bar — totals on the left, CTA on the right. */
function PlaceOrderBar({ mrpTotal, total, disabled, onPlaceOrder, className = '' }) {
  return (
    <div className={`flex items-center justify-between gap-4 ${className}`}>
      <div className="flex items-baseline gap-2">
        {mrpTotal > total && (
          <span className="text-xs text-ink-tertiary line-through nums">
            {formatPrice(mrpTotal)}
          </span>
        )}
        <span className="text-lg font-bold text-ink-primary nums">{formatPrice(total)}</span>
        <Info className="size-3.5 text-ink-tertiary" aria-hidden="true" />
      </div>
      <Button size="lg" className="accent-halo px-8" disabled={disabled} onClick={onPlaceOrder}>
        Place Order
      </Button>
    </div>
  );
}

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

  const selectedAddress = useMemo(() => {
    if (!addresses?.length) return null;
    return (
      addresses.find((a) => a.id === pickedAddressId) ||
      addresses.find((a) => a.is_default) ||
      addresses[0]
    );
  }, [addresses, pickedAddressId]);

  const items = data?.items ?? [];
  const subtotal = Number(data?.subtotal ?? 0);
  const taxAmount = Number(data?.tax_amount ?? 0);
  const discountAmount = Number(data?.discount_amount ?? 0);
  const cartTotal = Number(data?.total ?? subtotal + taxAmount - discountAmount);
  const couponCode = data?.coupon_code;
  const status = error?.response?.status;

  const pincode = selectedAddress?.pincode || '';
  const { data: serviceability } = useServiceability(pincode);
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
    } finally {
      setSavingForLater(null);
    }
  }

  function placeOrder() {
    navigate('/checkout');
  }

  if (!user || status === 401) {
    return (
      <Page>
        <h1 className="text-h1 text-ink-primary tracking-tight">Your cart</h1>
        <div className="mt-8">
          <EmptyState
            icon={Lock}
            title="Sign in to view your cart"
            description="Your cart is saved to your account so it's here wherever you shop."
            action={
              <Link to="/login">
                <Button size="sm">Sign in</Button>
              </Link>
            }
          />
        </div>
      </Page>
    );
  }

  if (isError) {
    return (
      <Page>
        <h1 className="text-h1 text-ink-primary tracking-tight">Your cart</h1>
        <div className="mt-8">
          <EmptyState
            icon={AlertTriangle}
            iconTone="danger"
            title="We couldn't load your cart"
            description="Something went wrong on our end. Please try again."
            action={
              <Button size="sm" onClick={() => refetch()}>
                Retry
              </Button>
            }
          />
        </div>
      </Page>
    );
  }

  return (
    <Page>
      <Breadcrumbs current="Cart" className="mb-5" />

      <div className="flex items-baseline justify-between gap-3">
        <h1 className="text-h1 text-ink-primary tracking-tight">Your cart</h1>
        {!isLoading && items.length > 0 && (
          <p className="text-sm text-ink-tertiary nums">
            {items.length} item{items.length === 1 ? '' : 's'}
          </p>
        )}
      </div>

      {isLoading ? (
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_340px]">
          <div className="flex flex-col gap-3">
            <Skeleton className="h-14 rounded-lg" />
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-36 rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-72 rounded-lg" />
        </div>
      ) : items.length === 0 ? (
        <div className="mt-8">
          <EmptyState
            icon={ShoppingBag}
            title="Your cart is empty"
            description="Browse the collection and add something you love."
            action={
              <Link to="/products">
                <Button size="sm">
                  Browse products
                  <ArrowRight className="size-4" aria-hidden="true" />
                </Button>
              </Link>
            }
          />
        </div>
      ) : (
        <div className="mt-8 grid items-start gap-6 lg:grid-cols-[1fr_340px]">
          {/* ── Left column: deliver-to bar + item list ── */}
          <div className="flex flex-col gap-3">
            <DeliverToBar
              address={selectedAddress}
              serviceability={serviceability}
              onChange={() => setDrawerOpen(true)}
            />

            <Card className="overflow-hidden p-0">
              <motion.ul
                variants={staggerContainer(0.06)}
                initial="hidden"
                animate="show"
                className="divide-y divide-line-subtle"
              >
                {items.map((item) => {
                  const mrp =
                    item.compare_at_price &&
                    Number(item.compare_at_price) > Number(item.unit_price)
                      ? Number(item.compare_at_price)
                      : null;
                  const savePct = mrp
                    ? Math.round(((mrp - Number(item.unit_price)) / mrp) * 100)
                    : 0;
                  const qtyOptions = Array.from(
                    { length: Math.max(10, item.quantity) },
                    (_, i) => i + 1,
                  );
                  const busy =
                    savingForLater === item.product_id ||
                    removeItem.isPending ||
                    updateQty.isPending;

                  return (
                    <motion.li key={item.product_id} variants={fadeUp} className="p-4 sm:p-5">
                      <div className="flex gap-4">
                        {/* Thumbnail + qty dropdown below it (Flipkart layout) */}
                        <div className="flex w-[88px] shrink-0 flex-col items-center gap-2.5 sm:w-[104px]">
                          <Link
                            to={`/products/${item.product_id}`}
                            className="grid aspect-square w-full place-items-center overflow-hidden rounded-lg border border-line-subtle bg-gradient-to-br from-accent/20 via-bg-elevated to-bg-sunken text-xl font-semibold text-ink-primary/25"
                          >
                            {item.image_url ? (
                              <img
                                src={item.image_url}
                                alt=""
                                loading="lazy"
                                className="size-full object-cover"
                              />
                            ) : (
                              item.name.charAt(0).toUpperCase()
                            )}
                          </Link>
                          <label className="flex items-center gap-1.5 text-xs font-medium text-ink-secondary">
                            Qty:
                            <select
                              value={item.quantity}
                              disabled={updateQty.isPending}
                              onChange={(e) =>
                                updateQty.mutate({
                                  productId: item.product_id,
                                  quantity: Number(e.target.value),
                                })
                              }
                              aria-label={`Quantity for ${item.name}`}
                              className="rounded-md border border-line-subtle bg-bg-elevated px-1.5 py-1 text-xs font-semibold text-ink-primary nums transition-colors hover:border-line-strong focus-visible:focus-ring disabled:opacity-50"
                            >
                              {qtyOptions.map((n) => (
                                <option key={n} value={n}>
                                  {n}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>

                        {/* Details */}
                        <div className="min-w-0 flex-1">
                          <Link
                            to={`/products/${item.product_id}`}
                            className="line-clamp-2 text-sm font-medium text-ink-primary leading-snug transition-colors hover:text-accent"
                          >
                            {item.name}
                          </Link>

                          <div className="mt-2 flex flex-wrap items-baseline gap-2">
                            {savePct > 0 && (
                              <span className="text-sm font-bold text-success">
                                ↓{savePct}%
                              </span>
                            )}
                            {mrp && (
                              <span className="text-sm text-ink-tertiary line-through nums">
                                {formatPrice(mrp)}
                              </span>
                            )}
                            <span className="text-lg font-bold text-ink-primary nums">
                              {formatPrice(item.unit_price)}
                            </span>
                          </div>

                          {Number(item.line_tax) > 0 && (
                            <p className="mt-0.5 text-[11px] text-ink-tertiary">
                              incl. {formatPrice(item.line_tax)} tax · line total{' '}
                              <span className="nums">{formatPrice(item.line_total)}</span>
                            </p>
                          )}

                          {deliveryBy && (
                            <p className="mt-1.5 text-xs text-ink-secondary">
                              Delivery by <span className="font-semibold">{deliveryBy}</span>
                            </p>
                          )}
                        </div>
                      </div>

                      {/* Action bar: Save for later | Remove | Buy this now */}
                      <div className="mt-4 grid grid-cols-3 divide-x divide-line-subtle rounded-lg border border-line-subtle bg-bg-sunken text-center">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => handleSaveForLater(item)}
                          className="py-2.5 text-xs font-semibold text-ink-secondary transition-colors hover:bg-bg-elevated hover:text-ink-primary focus-visible:focus-ring disabled:opacity-50"
                        >
                          {savingForLater === item.product_id ? 'Saving…' : 'Save for later'}
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => removeItem.mutate(item.product_id)}
                          className="py-2.5 text-xs font-semibold text-ink-secondary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring disabled:opacity-50"
                        >
                          Remove
                        </button>
                        <button
                          type="button"
                          disabled={busy || serviceable === false}
                          onClick={() =>
                            navigate(
                              `/checkout?buyNow=${item.product_id}&qty=${item.quantity}`,
                            )
                          }
                          className="inline-flex items-center justify-center gap-1 py-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/8 focus-visible:focus-ring disabled:opacity-50"
                        >
                          <Zap className="size-3.5" aria-hidden="true" />
                          Buy this now
                        </button>
                      </div>
                    </motion.li>
                  );
                })}
              </motion.ul>
            </Card>
          </div>

          {/* ── Right rail: price details ── */}
          <div className="flex flex-col gap-3 lg:sticky lg:top-24">
            <Card className="p-0">
              <h2 className="border-b border-line-subtle px-5 py-3.5 text-xs font-bold uppercase tracking-widest text-ink-tertiary">
                Price Details
              </h2>

              <div className="px-5 py-4">
                <FreeShippingNudge subtotal={subtotal} />

                <dl className="mt-3 flex flex-col gap-2.5 text-sm">
                  <div className="flex justify-between text-ink-secondary">
                    <dt>
                      Price ({items.length} item{items.length === 1 ? '' : 's'})
                    </dt>
                    <dd className="font-medium text-ink-primary nums">
                      {formatPrice(mrpTotal)}
                    </dd>
                  </div>
                  {mrpSavings > 0 && (
                    <div className="flex justify-between">
                      <dt className="text-ink-secondary">Discount</dt>
                      <dd className="font-semibold text-success nums">
                        −{formatPrice(mrpSavings)}
                      </dd>
                    </div>
                  )}
                  {discountAmount > 0 && (
                    <div className="flex justify-between">
                      <dt className="text-ink-secondary">
                        Coupon
                        {couponCode && (
                          <span className="ml-1 font-mono text-[10px] text-ink-tertiary">
                            ({couponCode})
                          </span>
                        )}
                      </dt>
                      <dd className="font-semibold text-success nums">
                        −{formatPrice(discountAmount)}
                      </dd>
                    </div>
                  )}
                  {taxAmount > 0 && (
                    <div className="flex justify-between text-ink-secondary">
                      <dt>Taxes</dt>
                      <dd className="font-medium text-ink-primary nums">
                        {formatPrice(taxAmount)}
                      </dd>
                    </div>
                  )}
                  <div className="flex justify-between text-ink-secondary">
                    <dt>Delivery Charges</dt>
                    {quote ? (
                      shippingAmount === 0 ? (
                        <dd className="font-semibold text-success">Free</dd>
                      ) : (
                        <dd className="font-medium text-ink-primary nums">
                          {formatPrice(shippingAmount)}
                        </dd>
                      )
                    ) : (
                      <dd className="text-xs text-ink-tertiary">
                        {selectedAddress ? 'Calculating…' : 'Select address'}
                      </dd>
                    )}
                  </div>
                </dl>

                <div className="mt-4 flex items-baseline justify-between border-t border-dashed border-line-strong pt-4">
                  <span className="text-sm font-semibold text-ink-primary">Total Amount</span>
                  <span className="text-h3 font-bold text-ink-primary nums">
                    {formatPrice(total)}
                  </span>
                </div>

                {totalSavings > 0 && (
                  <p className="mt-3 rounded-md bg-success/12 px-3 py-2 text-center text-xs font-semibold text-success">
                    You&apos;ll save {formatPrice(totalSavings)} on this order!
                  </p>
                )}
              </div>

              <p className="flex items-start gap-2.5 border-t border-line-subtle px-5 py-3.5 text-xs text-ink-tertiary">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                Safe and secure payments. Easy returns. 100% authentic products.
              </p>
            </Card>

            <Card className="p-4">
              <CouponBlock appliedCode={couponCode} discount={discountAmount} />
            </Card>

            {/* Desktop place-order bar */}
            <Card className="hidden p-4 lg:block">
              <PlaceOrderBar
                mrpTotal={mrpTotal + taxAmount + shippingAmount}
                total={total}
                disabled={serviceable === false}
                onPlaceOrder={placeOrder}
              />
              {serviceable === false && (
                <p className="mt-2 text-center text-xs text-danger">
                  Delivery isn&apos;t available at the selected address.
                </p>
              )}
            </Card>
          </div>

          {/* Mobile sticky place-order bar */}
          <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line-subtle bg-bg-elevated px-4 py-3 shadow-xl lg:hidden">
            <PlaceOrderBar
              mrpTotal={mrpTotal + taxAmount + shippingAmount}
              total={total}
              disabled={serviceable === false}
              onPlaceOrder={placeOrder}
            />
          </div>
          {/* Spacer so the fixed bar doesn't cover the rail on mobile */}
          <div className="h-16 lg:hidden" aria-hidden="true" />
        </div>
      )}

      <AddressSelectDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        selectedId={selectedAddress?.id ?? null}
        onSelect={(addr) => setPickedAddressId(addr.id)}
      />
    </Page>
  );
}
