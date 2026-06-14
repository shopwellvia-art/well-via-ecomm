import { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Package,
  ShoppingBag,
  Truck,
  ChevronDown,
  ChevronUp,
  Undo2,
  Banknote,
  MapPin,
  AlertTriangle,
} from 'lucide-react';
import { apiClient } from '@/services/apiClient.js';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import RequestReturnModal from '@/features/returns/components/RequestReturnModal.jsx';
import { useMyReturns } from '@/features/returns/hooks.js';
import { formatPrice } from '@/lib/utils.js';

const RETURN_STATUS_TONE = {
  requested: 'warning',
  approved: 'accent',
  rejected: 'danger',
  picked_up: 'info',
  received: 'info',
  refunded: 'success',
  cancelled: 'neutral',
};

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

// Map status to a human-readable delivery line shown prominently
const STATUS_DELIVERY_LINE = {
  delivered: 'Delivered',
  shipped: 'Shipped',
  processing: 'Processing',
  pending: 'Order placed',
  paid: 'Payment confirmed',
  cancelled: 'Cancelled',
  refunded: 'Refunded',
  failed: 'Payment failed',
};

function formatEventTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function humanize(status) {
  return (status || '').replace(/_/g, ' ');
}

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

function TrackingDisclosure({ order }) {
  const [open, setOpen] = useState(false);
  if (!order.shipping_awb) return null;
  const events = order.tracking_events || [];

  return (
    <div className="mt-3 rounded-sm border border-line-subtle bg-bg-sunken px-3 py-2.5">
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? 'Collapse tracking' : 'Show tracking'}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left focus-visible:focus-ring"
      >
        <span className="flex items-center gap-2 text-xs">
          <Truck className="size-3.5 text-ink-tertiary" aria-hidden="true" />
          <span className="font-medium text-ink-secondary">Tracking</span>
          <code className="nums font-mono text-[10px] text-ink-tertiary">
            {order.shipping_awb}
          </code>
        </span>
        {open ? (
          <ChevronUp className="size-3.5 text-ink-tertiary" aria-hidden="true" />
        ) : (
          <ChevronDown className="size-3.5 text-ink-tertiary" aria-hidden="true" />
        )}
      </button>

      {open && (
        <div className="mt-3 overflow-hidden">
          {events.length === 0 ? (
            <p className="text-xs text-ink-tertiary">No updates yet from the carrier.</p>
          ) : (
            <ol className="flex flex-col gap-2 border-l-2 border-line-subtle pl-4">
              {[...events]
                .sort((a, b) =>
                  (b.occurred_at || '').localeCompare(a.occurred_at || ''),
                )
                .map((e, i) => (
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

function useMyOrders() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: ['orders'],
    queryFn: () => apiClient.get('/orders').then((r) => r.data),
    enabled: !!token,
    retry: false,
  });
}

function ReturnsSummary() {
  const { data: returns, isLoading, isError } = useMyReturns();
  if (isLoading) return null;
  if (isError) return <p className="mt-4 text-xs text-danger">Couldn't load returns.</p>;
  if (!returns || returns.length === 0) return null;

  return (
    <div className="mt-6 rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
      {/* Header */}
      <div className="flex items-center gap-2.5 border-b border-line-subtle px-4 py-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-sm bg-accent/12 text-accent">
          <Undo2 className="size-4" aria-hidden="true" />
        </span>
        <h2 className="text-sm font-semibold text-ink-primary">Your returns</h2>
      </div>
      <ul className="flex flex-col divide-y divide-line-subtle">
        {returns.map((r) => (
          <li
            key={r.id}
            className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div>
              <p className="text-sm font-medium text-ink-primary">
                Return #{r.id}{' '}
                <span className="text-ink-tertiary">· Order #{r.order_id}</span>
              </p>
              <p className="mt-0.5 text-[11px] capitalize text-ink-tertiary">
                {r.reason.replace(/_/g, ' ')} · requested{' '}
                {new Date(r.requested_at).toLocaleDateString()}
              </p>
            </div>
            <Badge
              tone={RETURN_STATUS_TONE[r.status] || 'neutral'}
              size="md"
              className="shrink-0 self-start capitalize sm:self-auto"
            >
              {r.status.replace(/_/g, ' ')}
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function OrdersPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError, refetch } = useMyOrders();
  const orders = data ?? [];
  const [returnOrder, setReturnOrder] = useState(null);

  if (!user) return <Navigate to="/login" replace />;

  return (
    <Page>
      <Breadcrumbs current="Orders" className="mb-4" />

      {/* Page title bar — Flipkart style */}
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink-primary">My Orders</h1>
        {!isLoading && orders.length > 0 && (
          <span className="text-xs text-ink-secondary">
            {orders.length} order{orders.length === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title="Couldn't load your orders"
          description="Something went wrong. Please try again."
          action={<Button size="sm" onClick={() => refetch()}>Retry</Button>}
        />
      ) : isLoading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32 rounded-sm" />
          ))}
        </div>
      ) : orders.length === 0 ? (
        <EmptyState
          icon={Package}
          title="No orders yet"
          description="Once you place an order it will show up here."
          action={
            <Link to="/products">
              <Button size="sm">
                <ShoppingBag className="size-4" aria-hidden="true" />
                Start shopping
              </Button>
            </Link>
          }
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {orders.map((o) => {
            const badgeTone = STATUS_BADGE_TONE[o.status] ?? 'neutral';
            const deliveryLine = STATUS_DELIVERY_LINE[o.status] ?? o.status;

            return (
              <li key={o.id}>
                {/* Flipkart-style order card: white block, left accent strip on delivered */}
                <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
                  {/* Top meta row */}
                  <div className="flex flex-col gap-1 border-b border-line-subtle bg-bg-sunken px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="max-w-[8rem] truncate text-[11px] font-semibold uppercase tracking-wider text-ink-tertiary sm:max-w-none">
                        Order #{o.id}
                      </span>
                      <Badge tone={badgeTone} size="md" className="capitalize">
                        {o.status}
                      </Badge>
                      {o.payment_method === 'cod' &&
                        Number(o.cod_balance) > 0 &&
                        o.status !== 'delivered' &&
                        o.status !== 'cancelled' &&
                        o.status !== 'refunded' && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-warning/12 px-2 py-0.5 text-[10px] font-semibold text-warning">
                            <Banknote className="size-3" aria-hidden="true" />
                            {formatPrice(o.cod_balance, o.currency)} COD
                          </span>
                        )}
                    </div>
                    <span className="shrink-0 text-[11px] text-ink-tertiary">
                      {new Date(o.created_at).toLocaleDateString(undefined, {
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                      })}
                    </span>
                  </div>

                  <div className="px-4 py-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      {/* Item thumbnails + names */}
                      <div className="flex min-w-0 flex-1 flex-col gap-2">
                        {o.items.length > 0 && (
                          <div className="flex gap-2 overflow-x-auto pb-0.5">
                            {o.items.slice(0, 3).map((it, idx) => (
                              <div
                                key={it.id ?? it.product_id ?? idx}
                                className={`flex shrink-0 items-center gap-2${idx === 2 ? ' hidden sm:flex' : ''}`}
                              >
                                {/* Thumbnail */}
                                <div className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-sm border border-line-subtle bg-bg-sunken">
                                  {it.image_url ? (
                                    <>
                                      <img
                                        src={it.image_url}
                                        alt={it.name || 'Product'}
                                        loading="lazy"
                                        className="size-full object-contain"
                                        onError={(e) => {
                                          e.currentTarget.style.display = 'none';
                                          e.currentTarget.nextElementSibling?.removeAttribute('hidden');
                                        }}
                                      />
                                      <Package
                                        hidden
                                        className="size-5 text-ink-tertiary"
                                        aria-hidden="true"
                                      />
                                    </>
                                  ) : (
                                    <Package className="size-5 text-ink-tertiary" aria-hidden="true" />
                                  )}
                                </div>
                                <div className="min-w-0">
                                  <p className="max-w-[160px] truncate text-xs font-medium text-ink-primary">
                                    {it.name || `Product #${it.product_id}`}
                                  </p>
                                  {it.quantity > 1 && (
                                    <p className="nums text-[11px] text-ink-tertiary">
                                      Qty: {it.quantity}
                                    </p>
                                  )}
                                </div>
                              </div>
                            ))}
                            {o.items.length > 3 && (
                              <span className="self-center rounded-sm border border-line-subtle bg-bg-sunken px-2 py-0.5 text-[11px] text-ink-tertiary">
                                +{o.items.length - 3} more
                              </span>
                            )}
                          </div>
                        )}

                        {/* Address */}
                        {o.shipping_address && (
                          <p className="flex items-center gap-1 truncate text-xs text-ink-secondary">
                            <MapPin className="size-3 shrink-0 text-ink-tertiary" aria-hidden="true" />
                            {o.shipping_address}
                          </p>
                        )}
                        {o.billing_address_snapshot &&
                          (() => {
                            const ship = o.shipping_address_snapshot;
                            const bill = o.billing_address_snapshot;
                            const differs =
                              o.billing_address_id != null &&
                              o.billing_address_id !== o.shipping_address_id
                                ? true
                                : JSON.stringify(ship) !== JSON.stringify(bill);
                            if (!differs) return null;
                            const parts = [
                              bill.full_name,
                              bill.line1,
                              bill.line2,
                              bill.city,
                              bill.state,
                              bill.pincode,
                            ]
                              .filter(Boolean)
                              .join(', ');
                            return (
                              <p className="flex items-center gap-1 truncate text-xs text-ink-tertiary">
                                <MapPin className="size-3 shrink-0" aria-hidden="true" />
                                <span className="font-medium">Bill to:</span> {parts}
                              </p>
                            );
                          })()}
                      </div>

                      {/* Right: status + total */}
                      <div className="flex shrink-0 flex-col items-end gap-1 sm:items-end">
                        <p className="text-sm font-semibold text-ink-primary">
                          {deliveryLine}
                        </p>
                        <p className="nums text-base font-semibold text-ink-primary">
                          {formatPrice(o.total_amount, o.currency)}
                        </p>
                        <p className="text-[11px] text-ink-tertiary">
                          {o.items.length} item{o.items.length === 1 ? '' : 's'}
                        </p>
                      </div>
                    </div>

                    <TrackingDisclosure order={o} />

                    {/* Actions */}
                    {o.status === 'delivered' && (
                      <div className="mt-4 flex justify-end border-t border-line-subtle pt-3">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setReturnOrder(o)}
                        >
                          <Undo2 className="size-4" aria-hidden="true" />
                          Request return
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <ReturnsSummary />

      {returnOrder && (
        <RequestReturnModal
          order={returnOrder}
          onClose={() => setReturnOrder(null)}
        />
      )}
    </Page>
  );
}
