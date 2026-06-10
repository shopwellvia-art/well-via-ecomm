import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  ShoppingBag,
  Trash2,
  ArrowRight,
  Lock,
  AlertTriangle,
  TicketPercent,
  X,
  Plus,
  Minus,
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
import { useAuthStore } from '@/features/auth/store.js';
import PincodeCheck from '@/features/shipping/components/PincodeCheck.jsx';
import FreeShippingNudge from '@/features/shipping/components/FreeShippingNudge.jsx';
import { useRateQuote } from '@/features/shipping/hooks.js';
import { formatPrice } from '@/lib/utils.js';
import { fadeUp, staggerContainer, hoverLift, tapPress } from '@/lib/motion.js';

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
      <div className="mt-4 flex items-center justify-between rounded-lg border border-success/25 bg-success/8 px-3 py-2.5 text-sm shadow-glow-success">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-success/12 text-success">
            <TicketPercent className="size-3.5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="truncate font-mono text-xs font-semibold text-ink-primary nums">
              {appliedCode}
            </p>
            <p className="text-[11px] text-success">
              Saving {formatPrice(discount)}
            </p>
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
    <form onSubmit={handleApply} className="mt-4">
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

export default function CartPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError, error, refetch } = useCart();
  const removeItem = useRemoveFromCart();
  const updateQty = useUpdateCartQuantity();
  const [serviceable, setServiceable] = useState(null);
  const [activePincode, setActivePincode] = useState('');

  const items = data?.items ?? [];
  const subtotal = Number(data?.subtotal ?? 0);
  const taxAmount = Number(data?.tax_amount ?? 0);
  const discountAmount = Number(data?.discount_amount ?? 0);
  const cartTotal = Number(data?.total ?? subtotal + taxAmount - discountAmount);
  const rateInputItems = serviceable
    ? items.map((i) => ({ product_id: i.product_id, quantity: i.quantity }))
    : [];
  const { data: quote } = useRateQuote(
    serviceable ? activePincode : '',
    rateInputItems,
  );
  const shippingAmount = quote ? Number(quote.amount) : 0;
  const total = cartTotal + shippingAmount;
  const couponCode = data?.coupon_code;
  const status = error?.response?.status;

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
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_320px]">
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-28 rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-64 rounded-lg" />
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
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_320px]">
          {/* Line items */}
          <motion.ul
            className="flex flex-col gap-3"
            variants={staggerContainer(0.06)}
            initial="hidden"
            animate="show"
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

              return (
                <motion.li key={item.product_id} variants={fadeUp}>
                  <motion.div whileHover={hoverLift} whileTap={tapPress}>
                    <Card className="flex items-start gap-4 p-4 transition-shadow duration-200">
                      {/* Product thumbnail */}
                      <div className="grid size-18 w-[72px] shrink-0 place-items-center overflow-hidden rounded-lg border border-line-subtle bg-gradient-to-br from-accent/20 via-bg-elevated to-bg-sunken text-xl font-semibold text-ink-primary/25 shadow-sm">
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
                      </div>

                      <div className="min-w-0 flex-1">
                        <h3 className="truncate text-sm font-semibold text-ink-primary leading-snug">
                          {item.name}
                        </h3>

                        <div className="mt-1 flex items-baseline gap-2">
                          <p className="text-sm font-semibold text-ink-primary nums">
                            {formatPrice(item.unit_price)}
                          </p>
                          {mrp && (
                            <span className="text-xs text-ink-tertiary line-through nums">
                              {formatPrice(mrp)}
                            </span>
                          )}
                          {savePct > 0 && (
                            <span className="rounded-md bg-success/12 px-1.5 py-0.5 text-[10px] font-semibold text-success">
                              {savePct}% off
                            </span>
                          )}
                        </div>

                        {Number(item.line_tax) > 0 && (
                          <p className="mt-0.5 text-[11px] text-ink-tertiary">
                            incl. {formatPrice(item.line_tax)} tax
                          </p>
                        )}

                        {/* Quantity stepper */}
                        <div className="mt-3 inline-flex items-center gap-0 rounded-lg border border-line-subtle bg-bg-sunken overflow-hidden">
                          <button
                            type="button"
                            aria-label="Decrease quantity"
                            disabled={updateQty.isPending || item.quantity <= 1}
                            onClick={() =>
                              updateQty.mutate({
                                productId: item.product_id,
                                quantity: item.quantity - 1,
                              })
                            }
                            className="grid size-8 place-items-center text-ink-secondary transition-colors hover:bg-bg-elevated hover:text-ink-primary disabled:opacity-30 focus-visible:focus-ring"
                          >
                            <Minus className="size-3.5" />
                          </button>
                          <span className="min-w-[2.5ch] border-x border-line-subtle px-1 text-center text-xs font-semibold tabular-nums text-ink-primary nums">
                            {item.quantity}
                          </span>
                          <button
                            type="button"
                            aria-label="Increase quantity"
                            disabled={updateQty.isPending}
                            onClick={() =>
                              updateQty.mutate({
                                productId: item.product_id,
                                quantity: item.quantity + 1,
                              })
                            }
                            className="grid size-8 place-items-center text-ink-secondary transition-colors hover:bg-bg-elevated hover:text-ink-primary disabled:opacity-30 focus-visible:focus-ring"
                          >
                            <Plus className="size-3.5" />
                          </button>
                        </div>
                      </div>

                      {/* Right side: line total + remove */}
                      <div className="flex flex-col items-end justify-between self-stretch">
                        <button
                          type="button"
                          aria-label={`Remove ${item.name} from cart`}
                          onClick={() => removeItem.mutate(item.product_id)}
                          className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
                        >
                          <Trash2 className="size-4" />
                        </button>
                        <span className="text-sm font-semibold text-ink-primary nums">
                          {formatPrice(item.line_total)}
                        </span>
                      </div>
                    </Card>
                  </motion.div>
                </motion.li>
              );
            })}
          </motion.ul>

          {/* Summary — sticky on desktop */}
          <div className="lg:sticky lg:top-24 lg:self-start">
            <Card className="overflow-hidden p-0">
              {/* Accent top band */}
              <div className="h-1 w-full bg-gradient-to-r from-accent via-accent-mid to-accent/60" aria-hidden="true" />

              <div className="p-5">
                <h2 className="text-h3 text-ink-primary tracking-tight">Order summary</h2>

                <div className="mt-3">
                  <FreeShippingNudge subtotal={subtotal} />
                </div>

                <dl className="mt-4 flex flex-col gap-2.5 text-sm">
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
                      <dt className="text-success">
                        Discount
                        {couponCode && (
                          <span className="ml-1 font-mono text-[10px] text-ink-tertiary">
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
                    ) : (
                      <dd className="text-xs text-ink-tertiary">Check pincode below</dd>
                    )}
                  </div>
                </dl>

                <div className="mt-4 flex items-baseline justify-between border-t border-line-subtle pt-4">
                  <span className="text-sm font-medium text-ink-secondary">Total</span>
                  <span className="text-h3 font-semibold text-ink-primary nums">
                    {formatPrice(total)}
                  </span>
                </div>

                <CouponBlock appliedCode={couponCode} discount={discountAmount} />

                <div className="mt-4">
                  <PincodeCheck
                    onResult={(r) => {
                      setServiceable(r.serviceable);
                      setActivePincode(r.pincode);
                    }}
                  />
                </div>

                {serviceable === false ? (
                  <Button block size="lg" disabled className="mt-5">
                    Delivery unavailable
                  </Button>
                ) : (
                  <Link to="/checkout" className="mt-5 block">
                    <Button block size="lg" className="accent-halo">
                      Proceed to checkout
                      <ArrowRight className="size-4" aria-hidden="true" />
                    </Button>
                  </Link>
                )}

                <p className="mt-3 flex items-center justify-center gap-1.5 text-xs text-ink-tertiary">
                  <Lock className="size-3" aria-hidden="true" />
                  Secure, encrypted payment
                </p>
              </div>
            </Card>
          </div>
        </div>
      )}
    </Page>
  );
}
