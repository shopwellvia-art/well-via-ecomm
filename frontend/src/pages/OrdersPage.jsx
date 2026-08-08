import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import {
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Undo2,
  Banknote,
  MapPin,
  AlertTriangle,
  XCircle,
} from 'lucide-react';
import { useAuthStore } from '@/features/auth/store.js';
import RequestReturnModal from '@/features/returns/components/RequestReturnModal.jsx';
import { useMyReturns } from '@/features/returns/hooks.js';
import { useMyOrders } from '@/features/orders/hooks.js';
import { isOrderCancellable } from '@/features/orders/api.js';
import CancelOrderModal from '@/features/orders/components/CancelOrderModal.jsx';
import { formatPrice } from '@/lib/utils.js';
import AccountLayout from '@/components/storefront/AccountLayout.jsx';
import WImage from '@/components/storefront/WImage.jsx';
import { TruckIcon, BagIcon } from '@/components/storefront/Icons.jsx';

// ── Status pill class maps ─────────────────────────────────────────────────
const STATUS_PILL = {
  paid:       'bg-wgreen/10 text-wgreen',
  pending:    'bg-amber-50 text-amber-700',
  processing: 'bg-sky-50 text-sky-700',
  shipped:    'bg-sky-50 text-sky-700',
  delivered:  'bg-wgreen/10 text-wgreen',
  cancelled:  'bg-red-50 text-red-600',
  refunded:   'bg-wmuted/10 text-wmuted',
  failed:     'bg-red-50 text-red-600',
};

const RETURN_PILL = {
  requested: 'bg-amber-50 text-amber-700',
  approved:  'bg-wgreen/10 text-wgreen',
  rejected:  'bg-red-50 text-red-600',
  picked_up: 'bg-sky-50 text-sky-700',
  received:  'bg-sky-50 text-sky-700',
  refunded:  'bg-wgreen/10 text-wgreen',
  cancelled: 'bg-wmuted/10 text-wmuted',
};

// Map status to a human-readable delivery line shown prominently
const STATUS_DELIVERY_LINE = {
  delivered:  'Delivered',
  shipped:    'Shipped',
  processing: 'Processing',
  pending:    'Order placed',
  paid:       'Payment confirmed',
  cancelled:  'Cancelled',
  refunded:   'Refunded',
  failed:     'Payment failed',
};

const EVENT_TONES = {
  delivered:        'text-wgreen',
  out_for_delivery: 'text-wgold',
  in_transit:       'text-sky-600',
  picked_up:        'text-sky-600',
  created:          'text-wmuted',
  returned:         'text-amber-600',
  failed:           'text-amber-600',
  cancelled:        'text-red-500',
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

// ── Reusable pill components ───────────────────────────────────────────────
function StatusPill({ status }) {
  const cls = STATUS_PILL[status] ?? 'bg-wmuted/10 text-wmuted';
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[10.5px] font-semibold tracking-wide capitalize ${cls}`}
    >
      {status}
    </span>
  );
}

function ReturnStatusPill({ status }) {
  const cls = RETURN_PILL[status] ?? 'bg-wmuted/10 text-wmuted';
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[10.5px] font-semibold tracking-wide capitalize ${cls}`}
    >
      {status.replace(/_/g, ' ')}
    </span>
  );
}

// ── Tracking disclosure (expandable AWB timeline) ──────────────────────────
function TrackingDisclosure({ order }) {
  const [open, setOpen] = useState(false);
  if (!order.shipping_awb) return null;
  const events = order.tracking_events || [];

  return (
    <div className="mt-3 rounded-xl border border-wline bg-wpaper px-3.5 py-2.5">
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? 'Collapse tracking' : 'Show tracking'}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="flex items-center gap-2 text-xs">
          <span className="text-wmuted">
            <TruckIcon size={14} />
          </span>
          <span className="font-medium text-wink">Tracking</span>
          <code className="font-mono text-[10px] text-wmuted">{order.shipping_awb}</code>
        </span>
        {open ? (
          <ChevronUp className="size-3.5 text-wmuted" aria-hidden="true" />
        ) : (
          <ChevronDown className="size-3.5 text-wmuted" aria-hidden="true" />
        )}
      </button>

      {open && (
        <div className="mt-3 overflow-hidden">
          {events.length === 0 ? (
            <p className="text-xs text-wmuted">No updates yet from the carrier.</p>
          ) : (
            <ol className="flex flex-col gap-2 border-l-2 border-wline pl-4">
              {[...events]
                .sort((a, b) => (b.occurred_at || '').localeCompare(a.occurred_at || ''))
                .map((e, i) => (
                  <li
                    key={`${e.status}-${e.occurred_at}-${i}`}
                    className="relative text-xs before:absolute before:-left-[1.35rem] before:top-1 before:size-1.5 before:rounded-full before:bg-wgold/40"
                  >
                    <span
                      className={`font-semibold capitalize ${EVENT_TONES[e.status] || 'text-wink'}`}
                    >
                      {humanize(e.status)}
                    </span>
                    <span className="text-wmuted">
                      {' · '}{formatEventTime(e.occurred_at)}
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

// ── Returns summary panel ──────────────────────────────────────────────────
function ReturnsSummary() {
  const { data: returns, isLoading, isError } = useMyReturns();
  if (isLoading) return null;
  if (isError)
    return <p className="mt-4 text-xs text-red-500">Couldn't load returns.</p>;
  if (!returns || returns.length === 0) return null;

  return (
    <div className="mt-6 overflow-hidden rounded-xl2 border border-wline bg-wcard">
      {/* Header */}
      <div className="flex items-center gap-2.5 border-b border-wline bg-wpaper px-5 py-3.5">
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-wgold/15 text-wgold">
          <Undo2 className="size-4" aria-hidden="true" />
        </span>
        <h2 className="font-wserif text-[17px] text-wink">Your Returns</h2>
      </div>

      <ul className="flex flex-col divide-y divide-wline">
        {returns.map((r) => (
          <li
            key={r.id}
            className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between"
          >
            <div>
              <p className="text-sm font-medium text-wink">
                Return #{r.id}{' '}
                <span className="text-wmuted">· Order #{r.order_id}</span>
              </p>
              <p className="mt-0.5 text-[11px] capitalize text-wmuted">
                {r.reason.replace(/_/g, ' ')} · requested{' '}
                {new Date(r.requested_at).toLocaleDateString()}
              </p>
            </div>
            <ReturnStatusPill
              status={r.status}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Page component ─────────────────────────────────────────────────────────
export default function OrdersPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const { data, isLoading, isError, refetch } = useMyOrders();
  const orders = data ?? [];
  const [returnOrder, setReturnOrder] = useState(null);
  const [cancelOrder, setCancelOrder] = useState(null);

  if (!user) return <Navigate to="/login" replace />;

  return (
    <AccountLayout active="orders">
      {/* Page heading */}
      <h1 className="font-wserif font-medium text-[clamp(26px,3vw,38px)] leading-tight text-wink mb-1">
        Your Orders
      </h1>
      <p className="text-[14px] text-wmuted mb-6 font-light">
        Track, review and revisit your wellness rituals.
      </p>

      {/* Order count */}
      {!isLoading && !isError && orders.length > 0 && (
        <p className="text-xs text-wmuted mb-4">
          {orders.length} order{orders.length === 1 ? '' : 's'}
        </p>
      )}

      {/* ── Error state ── */}
      {isError ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <AlertTriangle className="size-10 text-wgold mb-4" aria-hidden="true" />
          <h2 className="font-wserif text-xl text-wink mb-2">
            Couldn't load your orders
          </h2>
          <p className="text-wmuted text-sm mb-6">
            Something went wrong. Please try again.
          </p>
          <button
            onClick={() => refetch()}
            className="rounded-full border border-wline bg-transparent px-6 py-2.5 text-[13px] text-wink transition-colors hover:border-wgreen hover:text-wgreen"
          >
            Retry
          </button>
        </div>
      ) : isLoading ? (
        /* ── Loading skeleton ── */
        <div className="flex flex-col gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-36 animate-pulse rounded-xl2 border border-wline bg-wcard"
            />
          ))}
        </div>
      ) : orders.length === 0 ? (
        /* ── Empty state ── */
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <span className="mb-4 text-wgold">
            <BagIcon size={40} />
          </span>
          <h2 className="font-wserif text-xl text-wink mb-2">No orders yet</h2>
          <p className="text-wmuted text-sm mb-6">
            Once you place an order, it will show up here.
          </p>
          <Link
            to="/products"
            className="rounded-full bg-wgreen px-7 py-3 text-[13px] font-medium text-white transition-colors hover:bg-wgreen-dark no-underline"
          >
            Start shopping
          </Link>
        </div>
      ) : (
        /* ── Order list ── */
        <ul className="flex flex-col gap-3.5">
          {orders.map((o) => {
            const deliveryLine = STATUS_DELIVERY_LINE[o.status] ?? o.status;

            return (
              <li key={o.id}>
                {/* Card — whole surface is a click target */}
                <div
                  role="link"
                  tabIndex={0}
                  aria-label={`View ${o.order_number ?? `order #${o.id}`} details`}
                  onClick={() => navigate(`/orders/${o.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      navigate(`/orders/${o.id}`);
                    }
                  }}
                  className="group cursor-pointer overflow-hidden rounded-xl2 border border-wline bg-wcard transition-all hover:border-wgold/50 hover:shadow-sm"
                >
                  {/* ── Top meta row ── */}
                  <div className="flex flex-col gap-1.5 border-b border-wline bg-wpaper px-5 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Order number */}
                      <span className="text-[11px] font-semibold uppercase tracking-wider text-wmuted">
                        {o.order_number ?? `#${o.id}`}
                      </span>

                      {/* Status pill */}
                      <StatusPill status={o.status} />

                      {/* COD balance badge */}
                      {o.payment_method === 'cod' &&
                        Number(o.cod_balance) > 0 &&
                        o.status !== 'delivered' &&
                        o.status !== 'cancelled' &&
                        o.status !== 'refunded' && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-wgold/10 px-2.5 py-0.5 text-[10px] font-semibold text-wgold">
                            <Banknote className="size-3" aria-hidden="true" />
                            {formatPrice(o.cod_balance, o.currency)} COD
                          </span>
                        )}
                    </div>

                    <div className="flex shrink-0 items-center gap-1.5">
                      <span className="text-[11px] text-wmuted">
                        {new Date(o.created_at).toLocaleDateString(undefined, {
                          day: 'numeric',
                          month: 'short',
                          year: 'numeric',
                        })}
                      </span>
                      <ChevronRight
                        className="size-4 text-wmuted transition-transform group-hover:translate-x-0.5 group-hover:text-wgreen"
                        aria-hidden="true"
                      />
                    </div>
                  </div>

                  {/* ── Card body ── */}
                  <div className="px-5 py-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      {/* Left: item thumbnails + names + address */}
                      <div className="flex min-w-0 flex-1 flex-col gap-2">
                        {o.items.length > 0 && (
                          <div className="flex gap-3 overflow-x-auto pb-0.5">
                            {o.items.slice(0, 3).map((it, idx) => (
                              <div
                                key={it.id ?? it.product_id ?? idx}
                                className={`flex shrink-0 items-center gap-2.5${idx === 2 ? ' hidden sm:flex' : ''}`}
                              >
                                {/* WImage thumbnail */}
                                <WImage
                                  src={it.image_url}
                                  alt={it.name || 'Product'}
                                  shape="rounded"
                                  className="w-[58px] h-[68px] shrink-0 border border-wline"
                                />
                                <div className="min-w-0">
                                  <p className="max-w-[160px] truncate text-xs font-medium text-wink">
                                    {it.name || `Product #${it.product_id}`}
                                  </p>
                                  {it.quantity > 1 && (
                                    <p className="text-[11px] text-wmuted">
                                      Qty: {it.quantity}
                                    </p>
                                  )}
                                </div>
                              </div>
                            ))}
                            {o.items.length > 3 && (
                              <span className="self-center rounded-full border border-wline bg-wpaper px-3 py-1 text-[11px] text-wmuted">
                                +{o.items.length - 3} more
                              </span>
                            )}
                          </div>
                        )}

                        {/* Shipping address (string form) */}
                        {o.shipping_address && (
                          <p className="flex items-center gap-1 truncate text-xs text-wmuted">
                            <MapPin className="size-3 shrink-0" aria-hidden="true" />
                            {o.shipping_address}
                          </p>
                        )}

                        {/* Billing address snapshot (shown only when different) */}
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
                              <p className="flex items-center gap-1 truncate text-xs text-wmuted">
                                <MapPin className="size-3 shrink-0" aria-hidden="true" />
                                <span className="font-medium">Bill to:</span> {parts}
                              </p>
                            );
                          })()}
                      </div>

                      {/* Right: delivery line + total + item count */}
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <p className="text-sm font-semibold text-wink">{deliveryLine}</p>
                        <p className="font-wserif text-[20px] leading-none text-wink">
                          {formatPrice(o.total_amount, o.currency)}
                        </p>
                        <p className="text-[11px] text-wmuted">
                          {o.items.length} item{o.items.length === 1 ? '' : 's'}
                        </p>
                      </div>
                    </div>

                    {/* Tracking disclosure */}
                    <TrackingDisclosure order={o} />

                    {/* ── Actions row (always visible; stopPropagation per button) ── */}
                    <div className="mt-4 flex items-center justify-end gap-3 border-t border-wline pt-3">
                      {isOrderCancellable(o) && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setCancelOrder(o);
                          }}
                          className="flex items-center gap-1.5 rounded-full border border-wline bg-transparent px-5 py-2 text-[13px] text-wink transition-colors hover:border-red-400 hover:text-red-600"
                        >
                          <XCircle className="size-3.5" aria-hidden="true" />
                          Cancel
                        </button>
                      )}
                      {o.status === 'delivered' && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setReturnOrder(o);
                          }}
                          className="flex items-center gap-1.5 rounded-full border border-wline bg-transparent px-5 py-2 text-[13px] text-wink transition-colors hover:border-wgreen hover:text-wgreen"
                        >
                          <Undo2 className="size-3.5" aria-hidden="true" />
                          Request return
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/orders/${o.id}`);
                        }}
                        className="rounded-full border border-wline bg-transparent px-5 py-2 text-[13px] text-wink transition-colors hover:border-wgreen hover:text-wgreen"
                      >
                        View
                      </button>
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* Returns summary panel */}
      <ReturnsSummary />

      {/* Request-return modal */}
      {returnOrder && (
        <RequestReturnModal
          order={returnOrder}
          onClose={() => setReturnOrder(null)}
        />
      )}

      {/* Cancel-order confirm modal */}
      {cancelOrder && (
        <CancelOrderModal
          order={cancelOrder}
          onClose={() => setCancelOrder(null)}
        />
      )}
    </AccountLayout>
  );
}
