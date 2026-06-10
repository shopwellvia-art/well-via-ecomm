import { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  Package,
  ShoppingBag,
  Truck,
  ChevronDown,
  ChevronUp,
  Undo2,
  Banknote,
  MapPin,
} from 'lucide-react';
import { apiClient } from '@/services/apiClient.js';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import RequestReturnModal from '@/features/returns/components/RequestReturnModal.jsx';
import { useMyReturns } from '@/features/returns/hooks.js';
import { formatPrice } from '@/lib/utils.js';
import { fadeUp, staggerContainer, listStagger } from '@/lib/motion.js';

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
  shipped: 'accent',
  delivered: 'success',
  cancelled: 'danger',
  refunded: 'neutral',
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
    <div className="mt-3 rounded-lg border border-line-subtle bg-bg-sunken px-3 py-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left focus-visible:focus-ring"
      >
        <span className="flex items-center gap-2 text-xs">
          <Truck className="size-3.5 text-ink-tertiary" aria-hidden="true" />
          <span className="text-ink-secondary font-medium">Tracking</span>
          <code className="font-mono text-[10px] text-ink-tertiary nums">
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
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.2 }}
          className="mt-3 overflow-hidden"
        >
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
        </motion.div>
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
  const { data: returns, isLoading } = useMyReturns();
  if (isLoading) return null;
  if (!returns || returns.length === 0) return null;

  return (
    <motion.div variants={fadeUp} initial="hidden" animate="show">
      <Card className="mt-8 overflow-hidden p-0">
        <div className="flex items-center gap-2.5 border-b border-line-subtle px-5 py-4">
          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-accent/12 text-accent">
            <Undo2 className="size-4" aria-hidden="true" />
          </span>
          <h2 className="text-sm font-semibold text-ink-primary">Your returns</h2>
        </div>
        <ul className="flex flex-col divide-y divide-line-subtle">
          {returns.map((r) => (
            <li
              key={r.id}
              className="flex flex-col gap-2 px-5 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div>
                <p className="text-sm font-medium text-ink-primary">
                  Return #{r.id}{' '}
                  <span className="text-ink-tertiary">· Order #{r.order_id}</span>
                </p>
                <p className="mt-0.5 text-[11px] text-ink-tertiary capitalize">
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
      </Card>
    </motion.div>
  );
}

export default function OrdersPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading } = useMyOrders();
  const orders = data ?? [];
  const [returnOrder, setReturnOrder] = useState(null);

  if (!user) return <Navigate to="/login" replace />;

  return (
    <Page>
      <Breadcrumbs current="Orders" className="mb-5" />

      <h1 className="text-h1 text-ink-primary tracking-tight">Your orders</h1>
      <p className="mt-1 text-sm text-ink-secondary">
        Everything you've bought, newest first.
      </p>

      {isLoading ? (
        <div className="mt-8 grid gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32 rounded-lg" />
          ))}
        </div>
      ) : orders.length === 0 ? (
        <div className="mt-8">
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
        </div>
      ) : (
        <motion.ul
          variants={staggerContainer(0.05)}
          initial="hidden"
          animate="show"
          className="mt-8 flex flex-col gap-3"
        >
          {orders.map((o) => {
            const badgeTone = STATUS_BADGE_TONE[o.status] ?? 'neutral';
            return (
              <motion.li key={o.id} variants={fadeUp}>
                <Card className="overflow-hidden p-0">
                  <div className="p-5">
                    {/* Header row */}
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-tertiary">
                            Order #{o.id}
                          </p>
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
                                {formatPrice(o.cod_balance, o.currency)} on delivery
                              </span>
                            )}
                        </div>

                        <p className="mt-2 text-sm text-ink-primary">
                          <span className="nums font-semibold">{o.items.length}</span>{' '}
                          item{o.items.length === 1 ? '' : 's'} ·{' '}
                          <strong className="nums">{formatPrice(o.total_amount, o.currency)}</strong>
                        </p>

                        <p className="mt-1 text-xs text-ink-tertiary">
                          Placed {new Date(o.created_at).toLocaleString()}
                        </p>

                        {o.shipping_address && (
                          <p className="mt-1 flex items-center gap-1 truncate text-xs text-ink-secondary">
                            <MapPin className="size-3 shrink-0 text-ink-tertiary" aria-hidden="true" />
                            {o.shipping_address}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Item names preview */}
                    {o.items.length > 0 && (
                      <motion.ul
                        variants={listStagger(0.03)}
                        initial="hidden"
                        animate="show"
                        className="mt-3 flex flex-wrap gap-1.5"
                      >
                        {o.items.slice(0, 4).map((it, idx) => (
                          <li key={idx}>
                            <span className="rounded-md border border-line-subtle bg-bg-sunken px-2 py-0.5 text-[11px] text-ink-secondary">
                              {it.name || `Product #${it.product_id}`}
                              {it.quantity > 1 && (
                                <span className="ml-1 text-ink-tertiary nums">×{it.quantity}</span>
                              )}
                            </span>
                          </li>
                        ))}
                        {o.items.length > 4 && (
                          <li>
                            <span className="rounded-md border border-line-subtle bg-bg-sunken px-2 py-0.5 text-[11px] text-ink-tertiary">
                              +{o.items.length - 4} more
                            </span>
                          </li>
                        )}
                      </motion.ul>
                    )}

                    <TrackingDisclosure order={o} />

                    {/* Return CTA */}
                    {o.status === 'delivered' && (
                      <div className="mt-4 flex justify-end border-t border-line-subtle pt-3">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setReturnOrder(o)}
                        >
                          <Undo2 className="size-4" aria-hidden="true" />
                          Request return
                        </Button>
                      </div>
                    )}
                  </div>
                </Card>
              </motion.li>
            );
          })}
        </motion.ul>
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
