import { Link, Navigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  Package,
  Truck,
  CreditCard,
  MapPin,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Banknote,
} from 'lucide-react';
import { useState } from 'react';
import { apiClient } from '@/services/apiClient.js';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';

// ── Tone maps ────────────────────────────────────────────────────────────────

/** Order-level status → Badge tone (matches OrdersPage.jsx) */
const STATUS_BADGE_TONE = {
  paid: 'success',
  pending: 'warning',
  shipped: 'info',
  delivered: 'success',
  cancelled: 'danger',
  refunded: 'neutral',
  processing: 'info',
  failed: 'danger',
};

/** Payment row status → Badge tone */
const PAYMENT_STATUS_TONE = {
  paid: 'success',
  authorized: 'info',
  initiated: 'warning',
  pending: 'warning',
  failed: 'danger',
  cancelled: 'danger',
  refunded: 'neutral',
  partially_refunded: 'neutral',
};

/** Shipment status → Badge tone */
const SHIPMENT_STATUS_TONE = {
  pending: 'neutral',
  ready_to_ship: 'info',
  pickup_scheduled: 'info',
  shipped: 'info',
  in_transit: 'info',
  out_for_delivery: 'accent',
  delivered: 'success',
  delivery_failed: 'danger',
  rto_initiated: 'warning',
  rto_delivered: 'warning',
  cancelled: 'danger',
};

/** Tracking event status → text color class (matches OrdersPage.jsx EVENT_TONES) */
const EVENT_TONES = {
  delivered: 'text-success',
  out_for_delivery: 'text-accent',
  in_transit: 'text-info',
  picked_up: 'text-info',
  created: 'text-ink-tertiary',
  returned: 'text-warning',
  failed: 'text-warning',
  cancelled: 'text-danger',
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

// ── Section label ─────────────────────────────────────────────────────────────

function SectionLabel({ icon: Icon, children }) {
  return (
    <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
      {Icon && <Icon className="size-3.5 shrink-0" aria-hidden="true" />}
      {children}
    </p>
  );
}

// ── Address rendering helper ─────────────────────────────────────────────────

function AddressBlock({ addr }) {
  // addr can come from addresses[] array or a snapshot object with slightly different shape
  if (!addr) return <p className="text-sm text-ink-tertiary">No address on record.</p>;

  // Normalize: addresses[] uses address_line1/address_line2; snapshots use line1/line2
  const name = addr.full_name || addr.name || null;
  const line1 = addr.address_line1 || addr.line1 || null;
  const line2 = addr.address_line2 || addr.line2 || null;
  const city = addr.city || null;
  const state = addr.state || null;
  const pincode = addr.pincode || null;
  const country = addr.country || null;
  const phone = addr.phone || null;
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
          className={i === 0 ? 'text-sm font-semibold text-ink-primary' : 'text-sm text-ink-secondary'}
        >
          {l}
        </p>
      ))}
    </address>
  );
}

// ── Tracking disclosure (adapted from OrdersPage) ─────────────────────────────

function TrackingDisclosure({ awb, events }) {
  const [open, setOpen] = useState(false);
  if (!awb) return null;
  const sorted = [...(events || [])].sort((a, b) =>
    (b.occurred_at || '').localeCompare(a.occurred_at || ''),
  );

  return (
    <div className="mt-3 rounded-sm border border-line-subtle bg-bg-sunken px-3 py-2.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left focus-visible:focus-ring"
      >
        <span className="flex items-center gap-2 text-xs">
          <Truck className="size-3.5 text-ink-tertiary" aria-hidden="true" />
          <span className="font-medium text-ink-secondary">Tracking events</span>
          <code className="nums font-mono text-[10px] text-ink-tertiary">{awb}</code>
        </span>
        {open ? (
          <ChevronUp className="size-3.5 text-ink-tertiary" aria-hidden="true" />
        ) : (
          <ChevronDown className="size-3.5 text-ink-tertiary" aria-hidden="true" />
        )}
      </button>

      {open && (
        <div className="mt-3 overflow-hidden">
          {sorted.length === 0 ? (
            <p className="text-xs text-ink-tertiary">No updates yet from the carrier.</p>
          ) : (
            <ol className="flex flex-col gap-2 border-l-2 border-line-subtle pl-4">
              {sorted.map((e, i) => (
                <li
                  key={`${e.status}-${e.occurred_at}-${i}`}
                  className="relative text-xs before:absolute before:-left-[1.35rem] before:top-1 before:size-1.5 before:rounded-full before:bg-line-strong"
                >
                  <span
                    className={`font-semibold capitalize ${EVENT_TONES[e.status] || 'text-ink-secondary'}`}
                  >
                    {humanize(e.status)}
                  </span>
                  <span className="text-ink-tertiary">
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
      <Page>
        <Breadcrumbs items={[{ label: 'Orders', to: '/orders' }]} current="Order" className="mb-4" />
        <div className="space-y-4">
          <Skeleton className="h-10 w-64 rounded-sm" />
          <Skeleton className="h-28 rounded-sm" />
          <Skeleton className="h-36 rounded-sm" />
          <Skeleton className="h-48 rounded-sm" />
          <Skeleton className="h-32 rounded-sm" />
        </div>
      </Page>
    );
  }

  // ── Error / 404 ──
  if (isError || !order) {
    const is404 = error?.response?.status === 404;
    return (
      <Page>
        <Breadcrumbs items={[{ label: 'Orders', to: '/orders' }]} current="Order" className="mb-4" />
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title={is404 ? 'Order not found' : 'Could not load order'}
          description={
            is404
              ? 'This order does not exist or does not belong to your account.'
              : 'Something went wrong fetching this order. Please try again.'
          }
          action={
            <Link to="/orders">
              <Button size="sm" variant="outline">
                <ArrowLeft className="size-4" aria-hidden="true" />
                Back to orders
              </Button>
            </Link>
          }
        />
      </Page>
    );
  }

  // ── Derived values ──
  const orderLabel = order.order_number ?? `#${order.id}`;
  const payments = order.payments ?? [];
  const shipments = order.shipments ?? [];
  const addresses = order.addresses ?? [];

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

  return (
    <Page>
      <Breadcrumbs
        items={[{ label: 'Orders', to: '/orders' }]}
        current={orderLabel}
        className="mb-4"
      />

      {/* Back link */}
      <Link
        to="/orders"
        className="mb-5 inline-flex items-center gap-1.5 rounded-sm text-sm text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        All orders
      </Link>

      {/* ── a) Header ─────────────────────────────────────────────────────── */}
      <div className="mb-5 overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
        <div className="flex flex-col gap-1 border-b border-line-subtle bg-bg-sunken px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink-primary">{orderLabel}</span>
            <Badge tone={STATUS_BADGE_TONE[order.status] ?? 'neutral'} size="md" className="capitalize">
              {order.status}
            </Badge>
            {order.payment_method === 'cod' &&
              Number(order.cod_balance) > 0 &&
              !['delivered', 'cancelled', 'refunded'].includes(order.status) && (
                <span className="inline-flex items-center gap-1 rounded-full bg-warning/12 px-2 py-0.5 text-[10px] font-semibold text-warning">
                  <Banknote className="size-3" aria-hidden="true" />
                  {formatPrice(order.cod_balance, order.currency)} COD
                </span>
              )}
          </div>
          <span className="shrink-0 text-xs text-ink-tertiary">
            Placed {formatDate(order.created_at)}
          </span>
        </div>
        <div className="flex items-center justify-between px-4 py-3">
          <p className="text-sm text-ink-secondary">
            {order.items?.length ?? 0} item{(order.items?.length ?? 0) === 1 ? '' : 's'}
          </p>
          <p className="nums text-base font-semibold text-ink-primary">
            {formatPrice(order.total_amount, order.currency)}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-5">

        {/* ── b) Payment status ──────────────────────────────────────────── */}
        <Card>
          <CardHeader title={<SectionLabel icon={CreditCard}>Payment</SectionLabel>} />
          <div className="p-5">
            {payments.length > 0 ? (
              <ul className="flex flex-col divide-y divide-line-subtle">
                {payments.map((p) => (
                  <li key={p.id} className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium capitalize text-ink-primary">
                          {humanize(p.payment_method || p.gateway || 'payment')}
                        </span>
                        {p.gateway && p.gateway !== p.payment_method && (
                          <span className="text-[11px] text-ink-tertiary">via {p.gateway}</span>
                        )}
                        <Badge
                          tone={PAYMENT_STATUS_TONE[p.payment_status] ?? 'neutral'}
                          size="sm"
                          className="capitalize"
                        >
                          {humanize(p.payment_status)}
                        </Badge>
                      </div>
                      <span className="nums text-sm font-semibold text-ink-primary">
                        {formatPrice(p.amount, p.currency || order.currency)}
                      </span>
                    </div>
                    {p.transaction_reference && (
                      <p className="font-mono text-[11px] text-ink-tertiary">
                        Ref: {p.transaction_reference}
                      </p>
                    )}
                    {p.paid_at && (
                      <p className="text-[11px] text-ink-tertiary">
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
                  <div className="flex items-center gap-2">
                    <Banknote className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
                    <div>
                      <p className="text-sm font-medium text-ink-primary">
                        Cash on delivery
                      </p>
                      {Number(order.cod_balance) > 0 && (
                        <p className="text-[11px] text-ink-tertiary">
                          {formatPrice(order.cod_balance, order.currency)} — collected on delivery
                        </p>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="text-sm capitalize text-ink-secondary">
                    {order.payment_method
                      ? humanize(order.payment_method)
                      : 'No payment details recorded.'}
                  </p>
                )}
                {order.payment_instrument && (
                  <p className="font-mono text-[11px] text-ink-tertiary">
                    {order.payment_instrument}
                  </p>
                )}
              </div>
            )}
          </div>
        </Card>

        {/* ── c) Delivery / Tracking ─────────────────────────────────────── */}
        <Card>
          <CardHeader title={<SectionLabel icon={Truck}>Delivery</SectionLabel>} />
          <div className="p-5 space-y-4">
            {shipments.length > 0 ? (
              <ul className="flex flex-col divide-y divide-line-subtle">
                {shipments.map((s) => {
                  const shipAwb = s.awb_number || s.tracking_number || null;
                  return (
                    <li key={s.id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-ink-primary capitalize">
                            {s.courier_partner || 'Courier'}
                            {s.courier_service ? ` · ${s.courier_service}` : ''}
                          </span>
                          <Badge
                            tone={SHIPMENT_STATUS_TONE[s.shipment_status] ?? 'neutral'}
                            size="sm"
                            className="capitalize"
                          >
                            {humanize(s.shipment_status)}
                          </Badge>
                        </div>
                        {shipAwb && (
                          <code className="font-mono text-[11px] text-ink-tertiary">
                            {shipAwb}
                          </code>
                        )}
                      </div>
                      {/* Per-leg timestamps */}
                      <div className="grid gap-1 text-[11px] text-ink-tertiary sm:grid-cols-2">
                        {s.shipped_at && (
                          <span>Shipped: {formatDateTime(s.shipped_at)}</span>
                        )}
                        {s.in_transit_at && (
                          <span>In transit: {formatDateTime(s.in_transit_at)}</span>
                        )}
                        {s.out_for_delivery_at && (
                          <span>Out for delivery: {formatDateTime(s.out_for_delivery_at)}</span>
                        )}
                        {s.delivered_at && (
                          <span>Delivered: {formatDateTime(s.delivered_at)}</span>
                        )}
                        {s.failed_delivery_at && (
                          <span>Delivery failed: {formatDateTime(s.failed_delivery_at)}</span>
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
                          className="inline-flex items-center gap-1 text-xs text-accent hover:underline focus-visible:focus-ring"
                        >
                          Track on courier site
                        </a>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-sm text-ink-tertiary">
                {['pending', 'paid'].includes(order.status)
                  ? 'Your order is being prepared for dispatch.'
                  : 'No shipment information is available yet.'}
              </p>
            )}

            {/* Tracking events timeline (works off order-level tracking_events) */}
            <TrackingDisclosure awb={awb} events={order.tracking_events} />
          </div>
        </Card>

        {/* ── d) Order items ─────────────────────────────────────────────── */}
        <Card>
          <CardHeader title={<SectionLabel icon={Package}>Items</SectionLabel>} />
          <div className="p-5">
            {(!order.items || order.items.length === 0) ? (
              <p className="text-sm text-ink-tertiary">No items found.</p>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line-subtle text-left">
                    <th className="pb-2.5 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                      Product
                    </th>
                    <th className="pb-2.5 text-center text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                      Qty
                    </th>
                    <th className="pb-2.5 text-right text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                      Unit
                    </th>
                    <th className="pb-2.5 text-right text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                      Total
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {order.items.map((it, idx) => (
                    <tr key={it.id ?? it.product_id ?? idx} className="border-t border-line-subtle">
                      <td className="py-3 text-sm text-ink-primary">
                        {it.name ? (
                          <span>{it.name}</span>
                        ) : (
                          <Link
                            to={`/products/${it.product_id}`}
                            className="text-accent hover:underline focus-visible:focus-ring"
                          >
                            Product #{it.product_id}
                          </Link>
                        )}
                      </td>
                      <td className="py-3 text-center nums text-sm text-ink-secondary">
                        ×{it.quantity}
                      </td>
                      <td className="py-3 text-right nums text-sm text-ink-secondary">
                        {formatPrice(it.unit_price, order.currency)}
                      </td>
                      <td className="py-3 text-right nums text-sm font-semibold text-ink-primary">
                        {formatPrice(Number(it.unit_price) * it.quantity, order.currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* Totals summary */}
            <dl className="mt-4 space-y-1.5 border-t border-line-subtle pt-4 text-sm">
              {Number(order.subtotal) > 0 && (
                <div className="flex justify-between text-ink-secondary">
                  <dt>Subtotal</dt>
                  <dd className="nums text-ink-primary">{formatPrice(order.subtotal, order.currency)}</dd>
                </div>
              )}
              {Number(order.tax_amount) > 0 && (
                <div className="flex justify-between text-ink-secondary">
                  <dt>Tax</dt>
                  <dd className="nums text-ink-primary">{formatPrice(order.tax_amount, order.currency)}</dd>
                </div>
              )}
              {Number(order.shipping_amount) > 0 && (
                <div className="flex justify-between text-ink-secondary">
                  <dt>Shipping</dt>
                  <dd className="nums text-ink-primary">{formatPrice(order.shipping_amount, order.currency)}</dd>
                </div>
              )}
              {Number(order.discount_amount) > 0 && (
                <div className="flex justify-between text-success">
                  <dt>Discount{order.coupon_code ? ` (${order.coupon_code})` : ''}</dt>
                  <dd className="nums">−{formatPrice(order.discount_amount, order.currency)}</dd>
                </div>
              )}
              <div className="flex justify-between border-t border-line-subtle pt-3 text-ink-primary">
                <dt className="font-semibold">Total</dt>
                <dd className="nums text-base font-semibold">
                  {formatPrice(order.total_amount, order.currency)}
                </dd>
              </div>
            </dl>
          </div>
        </Card>

        {/* ── e) Shipping address ────────────────────────────────────────── */}
        <Card>
          <CardHeader title={<SectionLabel icon={MapPin}>Shipping address</SectionLabel>} />
          <div className="p-5">
            {shippingAddr ? (
              <AddressBlock addr={shippingAddr} />
            ) : order.shipping_address ? (
              <p className="whitespace-pre-line text-sm text-ink-secondary">{order.shipping_address}</p>
            ) : (
              <p className="text-sm text-ink-tertiary">No shipping address on record.</p>
            )}
          </div>
        </Card>

      </div>
    </Page>
  );
}
