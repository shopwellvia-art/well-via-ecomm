import { Link, Navigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { apiClient } from '@/services/apiClient.js';
import AccountLayout from '@/components/storefront/AccountLayout.jsx';
import WImage from '@/components/storefront/WImage.jsx';
import { TruckIcon, Check } from '@/components/storefront/Icons.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice, cn } from '@/lib/utils.js';

// ── Status pill styles (no @/components/ui/Badge) ────────────────────────────

const STATUS_PILL = {
  paid:       'bg-wgreen text-white',
  pending:    'bg-amber-50 text-amber-700 border border-amber-200',
  shipped:    'bg-sky-50 text-sky-700 border border-sky-200',
  delivered:  'bg-wgreen text-white',
  cancelled:  'bg-red-50 text-red-700 border border-red-200',
  refunded:   'bg-wcanvas text-wmuted border border-wline',
  processing: 'bg-sky-50 text-sky-700 border border-sky-200',
  failed:     'bg-red-50 text-red-700 border border-red-200',
};

const PAYMENT_PILL = {
  paid:               'bg-wgreen text-white',
  authorized:         'bg-sky-50 text-sky-700 border border-sky-200',
  initiated:          'bg-amber-50 text-amber-700 border border-amber-200',
  pending:            'bg-amber-50 text-amber-700 border border-amber-200',
  failed:             'bg-red-50 text-red-700 border border-red-200',
  cancelled:          'bg-red-50 text-red-700 border border-red-200',
  refunded:           'bg-wcanvas text-wmuted border border-wline',
  partially_refunded: 'bg-wcanvas text-wmuted border border-wline',
};

const SHIPMENT_PILL = {
  pending:           'bg-wcanvas text-wmuted border border-wline',
  ready_to_ship:     'bg-sky-50 text-sky-700 border border-sky-200',
  pickup_scheduled:  'bg-sky-50 text-sky-700 border border-sky-200',
  shipped:           'bg-sky-50 text-sky-700 border border-sky-200',
  in_transit:        'bg-sky-50 text-sky-700 border border-sky-200',
  out_for_delivery:  'bg-amber-50 text-amber-700 border border-amber-200',
  delivered:         'bg-wgreen text-white',
  delivery_failed:   'bg-red-50 text-red-700 border border-red-200',
  rto_initiated:     'bg-amber-50 text-amber-700 border border-amber-200',
  rto_delivered:     'bg-amber-50 text-amber-700 border border-amber-200',
  cancelled:         'bg-red-50 text-red-700 border border-red-200',
};

const EVENT_COLORS = {
  delivered:        'text-wgreen',
  out_for_delivery: 'text-amber-600',
  in_transit:       'text-sky-600',
  picked_up:        'text-sky-600',
  created:          'text-wmuted',
  returned:         'text-amber-600',
  failed:           'text-amber-600',
  cancelled:        'text-red-600',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function humanize(status) {
  return (status || '').replace(/_/g, ' ');
}

function formatEventTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ── Inline pill (no @/components/ui/Badge) ───────────────────────────────────

function Pill({ children, className = '' }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-3 py-0.5 text-[11px] font-semibold capitalize tracking-wide',
        className,
      )}
    >
      {children}
    </span>
  );
}

// ── Gold eyebrow label ────────────────────────────────────────────────────────

function Eyebrow({ children }) {
  return (
    <p className="font-display text-[10px] uppercase tracking-[0.14em] text-wgold mb-1">
      {children}
    </p>
  );
}

// ── Order status progress stepper ─────────────────────────────────────────────

/** Linear fulfillment stages. rank matches STATUS_RANK below. */
const PROGRESS_STEPS = [
  { key: 'placed',    label: 'Placed',    rank: 0 },
  { key: 'confirmed', label: 'Confirmed', rank: 1 },
  { key: 'shipped',   label: 'Shipped',   rank: 2 },
  { key: 'delivered', label: 'Delivered', rank: 3 },
];

const STATUS_RANK = { pending: 0, paid: 1, processing: 1, shipped: 2, delivered: 3 };

function OrderProgress({ order }) {
  const status = order.status;

  // Off-path terminal states — clear banner instead of the linear tracker.
  if (status === 'cancelled' || status === 'refunded') {
    const isRefund = status === 'refunded';
    const at = isRefund ? order.refunded_at : order.cancelled_at;
    return (
      <div
        className={cn(
          'mb-5 flex items-start gap-3 rounded-xl2 border px-5 py-4',
          isRefund
            ? 'border-wline bg-wcard'
            : 'border-red-200 bg-red-50',
        )}
      >
        <span
          className={cn(
            'mt-0.5 shrink-0 text-base leading-none font-semibold',
            isRefund ? 'text-wmuted' : 'text-red-600',
          )}
          aria-hidden="true"
        >
          ✕
        </span>
        <div>
          <p className="text-sm font-semibold text-wink">
            {isRefund ? 'Order refunded' : 'Order cancelled'}
          </p>
          <p className="mt-0.5 text-xs text-wmuted">
            {at ? formatDateTime(at) : 'This order is no longer active.'}
          </p>
        </div>
      </div>
    );
  }

  const currentRank = STATUS_RANK[status] ?? 0;
  const stepAt = {
    placed:    order.created_at,
    confirmed: order.paid_at,
    shipped:   order.shipped_at,
    delivered: order.delivered_at,
  };

  return (
    <div className="mb-5 bg-wcard border border-wline rounded-xl2 p-6">
      <Eyebrow>Order Progress</Eyebrow>
      <ol className="flex items-start mt-4">
        {PROGRESS_STEPS.map((step, i) => {
          const done = currentRank >= step.rank;
          const isCurrent = currentRank === step.rank;
          const at = stepAt[step.key];
          return (
            <li
              key={step.key}
              className="relative flex flex-1 flex-col items-center text-center"
            >
              {/* Connector line — gold when this step has been reached */}
              {i > 0 && (
                <span
                  className={cn(
                    'absolute right-1/2 top-[9px] h-0.5 w-full',
                    done ? 'bg-wgold' : 'bg-wline',
                  )}
                  aria-hidden="true"
                />
              )}

              {/* Step dot */}
              <span
                className={cn(
                  'relative z-10 grid size-[18px] place-items-center rounded-full border-2',
                  done
                    ? 'border-wgold bg-wgold'
                    : 'border-wline bg-wpaper',
                )}
              >
                {done ? (
                  <Check size={9} stroke="white" strokeWidth={2.5} />
                ) : (
                  <span
                    className="size-1.5 rounded-full bg-wline"
                    aria-hidden="true"
                  />
                )}
              </span>

              <span
                className={cn(
                  'mt-2.5 text-[11px] leading-tight',
                  isCurrent
                    ? 'font-semibold text-wink'
                    : done
                    ? 'font-medium text-wink'
                    : 'text-wmuted',
                )}
              >
                {step.label}
              </span>
              {at && (
                <span className="mt-0.5 text-[10px] text-wmuted">
                  {formatDate(at)}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ── Address block ─────────────────────────────────────────────────────────────

function AddressBlock({ addr }) {
  // addr can come from addresses[] array or a snapshot object (slightly different shape)
  if (!addr) return <p className="text-sm text-wmuted">No address on record.</p>;

  // Normalize: addresses[] uses address_line1/2; snapshots use line1/2
  const name     = addr.full_name || addr.name || null;
  const line1    = addr.address_line1 || addr.line1 || null;
  const line2    = addr.address_line2 || addr.line2 || null;
  const city     = addr.city || null;
  const state    = addr.state || null;
  const pincode  = addr.pincode || null;
  const country  = addr.country || null;
  const phone    = addr.phone || null;
  const landmark = addr.landmark || null;

  const lines = [
    name,
    line1,
    line2,
    landmark,
    [city, state, pincode].filter(Boolean).join(', '),
    country,
    phone ? `Phone: ${phone}` : null,
  ].filter(Boolean);

  return (
    <address className="not-italic space-y-0.5">
      {lines.map((l, i) => (
        <p
          key={i}
          className={
            i === 0
              ? 'text-sm font-semibold text-wink'
              : 'text-[13.5px] text-wmuted font-light leading-[1.7]'
          }
        >
          {l}
        </p>
      ))}
    </address>
  );
}

// ── Tracking disclosure ────────────────────────────────────────────────────────

function TrackingDisclosure({ awb, events }) {
  const [open, setOpen] = useState(false);
  if (!awb) return null;

  const sorted = [...(events || [])].sort((a, b) =>
    (b.occurred_at || '').localeCompare(a.occurred_at || ''),
  );

  return (
    <div className="mt-3 rounded-xl border border-wline bg-wcanvas px-4 py-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="flex items-center gap-2 text-xs">
          <TruckIcon size={14} stroke="#6F6A60" />
          <span className="font-medium text-wmuted">Tracking events</span>
          <code className="font-mono text-[10px] text-wmuted">{awb}</code>
        </span>
        <span className="text-wmuted text-[10px]" aria-hidden="true">
          {open ? '▲' : '▼'}
        </span>
      </button>

      {open && (
        <div className="mt-3">
          {sorted.length === 0 ? (
            <p className="text-xs text-wmuted">No updates yet from the carrier.</p>
          ) : (
            <ol className="flex flex-col gap-2 border-l-2 border-wgold pl-4">
              {sorted.map((e, i) => (
                <li
                  key={`${e.status}-${e.occurred_at}-${i}`}
                  className="relative text-xs before:absolute before:-left-[1.35rem] before:top-1 before:size-1.5 before:rounded-full before:bg-wgold"
                >
                  <span
                    className={cn(
                      'font-semibold capitalize',
                      EVENT_COLORS[e.status] || 'text-wink',
                    )}
                  >
                    {humanize(e.status)}
                  </span>
                  <span className="text-wmuted">
                    {' · '}
                    {formatEventTime(e.occurred_at)}
                    {e.location ? ` · ${e.location}` : ''}
                    {e.note ? ` — ${e.note}` : ''}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

// ── Data hook ────────────────────────────────────────────────────────────────

function useOrderDetail(id) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: ['order', id],
    queryFn: () => apiClient.get(`/orders/${id}`).then((r) => r.data),
    enabled: !!token && !!id,
    retry: false,
  });
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function OrderDetailPage() {
  const { id } = useParams();
  const user = useAuthStore((s) => s.user);

  const { data: order, isLoading, isError, error } = useOrderDetail(id);

  if (!user) return <Navigate to="/login" replace />;

  // ── Loading state ──
  if (isLoading) {
    return (
      <AccountLayout active="orders">
        <Link
          to="/orders"
          className="text-[13px] text-wgold no-underline mb-4 inline-block hover:underline"
        >
          ← All orders
        </Link>
        <div className="space-y-4 animate-pulse">
          <div className="h-10 w-64 bg-wcanvas rounded-xl2" />
          <div className="h-28 bg-wcanvas rounded-xl2" />
          <div className="h-36 bg-wcanvas rounded-xl2" />
          <div className="h-48 bg-wcanvas rounded-xl2" />
          <div className="h-32 bg-wcanvas rounded-xl2" />
        </div>
      </AccountLayout>
    );
  }

  // ── Error / 404 ──
  if (isError || !order) {
    const is404 = error?.response?.status === 404;
    return (
      <AccountLayout active="orders">
        <Link
          to="/orders"
          className="text-[13px] text-wgold no-underline mb-5 inline-block hover:underline"
        >
          ← All orders
        </Link>
        <div className="bg-wcard border border-wline rounded-xl2 p-10 text-center">
          <p className="font-wserif text-[22px] text-wink mb-2">
            {is404 ? 'Order not found' : 'Could not load order'}
          </p>
          <p className="text-sm text-wmuted mb-6">
            {is404
              ? 'This order does not exist or does not belong to your account.'
              : 'Something went wrong fetching this order. Please try again.'}
          </p>
          <Link
            to="/orders"
            className="inline-flex items-center gap-2 bg-wgreen text-white rounded-full px-6 py-2.5 text-[13px] hover:bg-wgreen-dark transition-colors no-underline"
          >
            ← Back to orders
          </Link>
        </div>
      </AccountLayout>
    );
  }

  // ── Derived values ──
  const orderLabel = order.order_number ?? `#${order.id}`;
  const payments   = order.payments  ?? [];
  const shipments  = order.shipments ?? [];
  const addresses  = order.addresses ?? [];

  const shippingAddr =
    addresses.find((a) => a.address_type === 'shipping') ||
    order.shipping_address_snapshot ||
    null;

  // Best AWB from shipments[], fallback to order-level awb
  const primaryShipment = shipments[0] ?? null;
  const awb =
    primaryShipment?.awb_number ||
    primaryShipment?.tracking_number ||
    order.shipping_awb ||
    null;

  const statusPillCls = STATUS_PILL[order.status] || 'bg-wcanvas text-wmuted border border-wline';

  return (
    <AccountLayout active="orders">
      {/* Back link */}
      <Link
        to="/orders"
        className="text-[13px] text-wgold no-underline mb-3.5 inline-block hover:underline"
      >
        ← All orders
      </Link>

      {/* ── a) Header ────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap justify-between items-end gap-3 mb-[22px]">
        <div>
          <h1 className="font-wserif font-medium text-[clamp(26px,3.2vw,38px)] text-wink m-0 mb-1 leading-tight">
            Order {orderLabel}
          </h1>
          <p className="text-[13.5px] text-wmuted font-light m-0">
            Placed {formatDate(order.created_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Pill className={statusPillCls}>{order.status}</Pill>
          {order.payment_method === 'cod' &&
            Number(order.cod_balance) > 0 &&
            !['delivered', 'cancelled', 'refunded'].includes(order.status) && (
              <Pill className="bg-amber-50 text-amber-700 border border-amber-200">
                {formatPrice(order.cod_balance, order.currency)} COD
              </Pill>
            )}
          <span className="text-[13.5px] text-wmuted">
            {order.items?.length ?? 0}{' '}
            item{(order.items?.length ?? 0) === 1 ? '' : 's'}
            {' · '}
            <span className="font-wserif text-[16px] text-wink">
              {formatPrice(order.total_amount, order.currency)}
            </span>
          </span>
        </div>
      </div>

      {/* ── b) Status timeline ───────────────────────────────────────────── */}
      <OrderProgress order={order} />

      {/* ── c) 2-col card grid ───────────────────────────────────────────── */}
      <div className="grid sm:grid-cols-2 gap-4">

        {/* ── Items card (left) ──────────────────────────────────────────── */}
        <div className="bg-wcard border border-wline rounded-xl2 p-[22px] flex flex-col gap-4">
          <div>
            <Eyebrow>Products</Eyebrow>
            <h2 className="font-wserif text-[18px] text-wink">Items Ordered</h2>
          </div>

          {!order.items || order.items.length === 0 ? (
            <p className="text-sm text-wmuted">No items found.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-wline">
              {order.items.map((it, idx) => (
                <li
                  key={it.id ?? it.product_id ?? idx}
                  className="flex items-center gap-3 py-3 first:pt-0 last:pb-0"
                >
                  {/* Thumbnail — WImage handles fallback */}
                  <Link
                    to={`/products/${it.product_id}`}
                    className="shrink-0 block w-[54px] h-[62px] overflow-hidden rounded-xl border border-wline"
                  >
                    <WImage
                      src={it.image_url}
                      alt={it.name || 'Product'}
                      shape="rounded"
                      className="w-full h-full"
                    />
                  </Link>

                  {/* Name + per-unit detail */}
                  <div className="flex-1 min-w-0">
                    <Link
                      to={`/products/${it.product_id}`}
                      className="line-clamp-2 text-[14px] text-wink hover:text-wgold no-underline transition-colors"
                    >
                      {it.name || `Product #${it.product_id}`}
                    </Link>
                    <p className="text-[11.5px] text-wmuted mt-0.5">
                      Qty {it.quantity} · {formatPrice(it.unit_price, order.currency)} each
                    </p>
                  </div>

                  {/* Line total */}
                  <p className="font-wserif text-[16px] text-wink shrink-0">
                    {formatPrice(Number(it.unit_price) * it.quantity, order.currency)}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {/* Totals summary */}
          <dl className="space-y-1.5 border-t border-wline pt-4 text-[13.5px]">
            {Number(order.subtotal) > 0 && (
              <div className="flex justify-between text-wmuted">
                <dt>Subtotal</dt>
                <dd className="text-wink">{formatPrice(order.subtotal, order.currency)}</dd>
              </div>
            )}
            {Number(order.tax_amount) > 0 && (
              <div className="flex justify-between text-wmuted">
                <dt>Tax</dt>
                <dd className="text-wink">{formatPrice(order.tax_amount, order.currency)}</dd>
              </div>
            )}
            {Number(order.shipping_amount) > 0 && (
              <div className="flex justify-between text-wmuted">
                <dt>Shipping</dt>
                <dd className="text-wink">{formatPrice(order.shipping_amount, order.currency)}</dd>
              </div>
            )}
            {Number(order.discount_amount) > 0 && (
              <div className="flex justify-between text-wgreen">
                <dt>
                  Discount
                  {order.coupon_code ? ` (${order.coupon_code})` : ''}
                </dt>
                <dd>−{formatPrice(order.discount_amount, order.currency)}</dd>
              </div>
            )}
            <div className="flex justify-between border-t border-wline pt-3 text-wink">
              <dt className="font-semibold">Total Paid</dt>
              <dd className="font-wserif text-[18px]">
                {formatPrice(order.total_amount, order.currency)}
              </dd>
            </div>
          </dl>
        </div>

        {/* ── Right column: Delivery + Address + Payment cards ─────────────── */}
        <div className="flex flex-col gap-4">

          {/* ── Delivery / Tracking card ──────────────────────────────────── */}
          <div className="bg-wcard border border-wline rounded-xl2 p-[22px]">
            <div className="mb-3.5">
              <Eyebrow>Fulfilment</Eyebrow>
              <h2 className="font-wserif text-[18px] text-wink">Delivery</h2>
            </div>

            {shipments.length > 0 ? (
              <ul className="flex flex-col divide-y divide-wline">
                {shipments.map((s) => {
                  const shipAwb = s.awb_number || s.tracking_number || null;
                  return (
                    <li
                      key={s.id}
                      className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[13.5px] font-medium text-wink capitalize">
                            {s.courier_partner || 'Courier'}
                            {s.courier_service ? ` · ${s.courier_service}` : ''}
                          </span>
                          <Pill
                            className={
                              SHIPMENT_PILL[s.shipment_status] ||
                              'bg-wcanvas text-wmuted border border-wline'
                            }
                          >
                            {humanize(s.shipment_status)}
                          </Pill>
                        </div>
                        {shipAwb && (
                          <code className="font-mono text-[11px] text-wmuted">
                            {shipAwb}
                          </code>
                        )}
                      </div>

                      {/* Per-leg timestamps */}
                      <div className="flex flex-col gap-0.5 text-[11px] text-wmuted">
                        {s.shipped_at && (
                          <span>Shipped: {formatDateTime(s.shipped_at)}</span>
                        )}
                        {s.in_transit_at && (
                          <span>In transit: {formatDateTime(s.in_transit_at)}</span>
                        )}
                        {s.out_for_delivery_at && (
                          <span>
                            Out for delivery:{' '}
                            {formatDateTime(s.out_for_delivery_at)}
                          </span>
                        )}
                        {s.delivered_at && (
                          <span>Delivered: {formatDateTime(s.delivered_at)}</span>
                        )}
                        {s.failed_delivery_at && (
                          <span>
                            Delivery failed:{' '}
                            {formatDateTime(s.failed_delivery_at)}
                          </span>
                        )}
                        {s.returned_at && (
                          <span>Returned: {formatDateTime(s.returned_at)}</span>
                        )}
                      </div>

                      {s.tracking_url && (
                        <a
                          href={s.tracking_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-wgold hover:underline"
                        >
                          Track on courier site →
                        </a>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-[13.5px] text-wmuted font-light leading-[1.7]">
                {['pending', 'paid'].includes(order.status)
                  ? 'Your order is being prepared for dispatch.'
                  : 'No shipment information is available yet.'}
              </p>
            )}

            {/* Tracking events timeline (from order-level tracking_events) */}
            <TrackingDisclosure awb={awb} events={order.tracking_events} />
          </div>

          {/* ── Shipping address card ─────────────────────────────────────── */}
          <div className="bg-wcard border border-wline rounded-xl2 p-[22px]">
            <div className="mb-3.5">
              <Eyebrow>Destination</Eyebrow>
              <h2 className="font-wserif text-[18px] text-wink">Shipping Address</h2>
            </div>

            {shippingAddr ? (
              <AddressBlock addr={shippingAddr} />
            ) : order.shipping_address ? (
              <p className="whitespace-pre-line text-[13.5px] text-wmuted font-light leading-[1.7]">
                {order.shipping_address}
              </p>
            ) : (
              <p className="text-sm text-wmuted">No shipping address on record.</p>
            )}
          </div>

          {/* ── Payment card ──────────────────────────────────────────────── */}
          <div className="bg-wcard border border-wline rounded-xl2 p-[22px]">
            <div className="mb-3.5">
              <Eyebrow>Transaction</Eyebrow>
              <h2 className="font-wserif text-[18px] text-wink">Payment</h2>
            </div>

            {payments.length > 0 ? (
              <ul className="flex flex-col divide-y divide-wline">
                {payments.map((p) => (
                  <li
                    key={p.id}
                    className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-medium capitalize text-wink">
                          {humanize(p.payment_method || p.gateway || 'payment')}
                        </span>
                        {p.gateway && p.gateway !== p.payment_method && (
                          <span className="text-[11px] text-wmuted">
                            via {p.gateway}
                          </span>
                        )}
                        <Pill
                          className={
                            PAYMENT_PILL[p.payment_status] ||
                            'bg-wcanvas text-wmuted border border-wline'
                          }
                        >
                          {humanize(p.payment_status)}
                        </Pill>
                      </div>
                      <span className="font-wserif text-[16px] text-wink">
                        {formatPrice(p.amount, p.currency || order.currency)}
                      </span>
                    </div>
                    {p.transaction_reference && (
                      <p className="font-mono text-[11px] text-wmuted">
                        Ref: {p.transaction_reference}
                      </p>
                    )}
                    {p.paid_at && (
                      <p className="text-[11px] text-wmuted">
                        Paid {formatDateTime(p.paid_at)}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              /* Graceful fallback for older orders without payments[] */
              <div className="space-y-1.5">
                {order.payment_method === 'cod' ? (
                  <div>
                    <p className="text-[13.5px] font-medium text-wink">
                      Cash on delivery
                    </p>
                    {Number(order.cod_balance) > 0 && (
                      <p className="text-[11px] text-wmuted mt-0.5">
                        {formatPrice(order.cod_balance, order.currency)} — collected
                        on delivery
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-[13.5px] capitalize text-wmuted">
                    {order.payment_method
                      ? humanize(order.payment_method)
                      : 'No payment details recorded.'}
                  </p>
                )}
                {order.payment_instrument && (
                  <p className="font-mono text-[11px] text-wmuted">
                    {order.payment_instrument}
                  </p>
                )}
              </div>
            )}
          </div>

        </div>
      </div>
    </AccountLayout>
  );
}
