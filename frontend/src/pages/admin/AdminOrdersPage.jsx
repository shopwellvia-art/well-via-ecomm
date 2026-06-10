import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Search,
  Package,
  ChevronLeft,
  ChevronRight,
  AlertTriangle,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useAdminOrders } from '@/features/admin-orders/hooks.js';
import { listStagger, fadeUp } from '@/lib/motion.js';

const PAGE_SIZE = 25;

const STATUSES = [
  { value: '',          label: 'All' },
  { value: 'pending',   label: 'Pending' },
  { value: 'paid',      label: 'Paid' },
  { value: 'shipped',   label: 'Shipped' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'refunded',  label: 'Refunded' },
];

/** Maps order status to a Badge tone */
const STATUS_TONE = {
  pending:   'neutral',
  paid:      'accent',
  shipped:   'info',
  delivered: 'success',
  cancelled: 'warning',
  refunded:  'danger',
};

function useDebounced(v, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function AdminOrdersPage() {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const debounced = useDebounced(search, 250);

  useEffect(() => setPage(1), [debounced, status]);

  const opts = useMemo(
    () => ({
      q: debounced || undefined,
      status: status || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [debounced, status, page],
  );

  const { data, isLoading, isError, refetch } = useAdminOrders(opts);
  const items = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const counts = data?.counts_by_status || {};

  return (
    <AdminPage
      title="Orders"
      description={`${total.toLocaleString()} order${total === 1 ? '' : 's'} — track fulfillment, issue refunds, and follow the customer journey.`}
    >
      {/* Status filter chips with live counts */}
      <div className="mb-5 flex flex-wrap gap-2">
        {STATUSES.map((s) => {
          const count = s.value ? counts[s.value] : total;
          const active = status === s.value;
          return (
            <button
              key={s.value || 'all'}
              type="button"
              onClick={() => setStatus(s.value)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:focus-ring',
                active
                  ? 'border-accent bg-accent/12 text-accent'
                  : 'border-line-subtle bg-bg-elevated text-ink-secondary hover:border-line-strong hover:text-ink-primary',
              )}
            >
              {s.label}
              {count != null && (
                <span className={cn(
                  'nums rounded-full px-1.5 py-px text-[10px] font-semibold',
                  active ? 'bg-accent/20 text-accent' : 'bg-bg-sunken text-ink-tertiary',
                )}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Search bar */}
      <div className="mb-5 w-full max-w-sm">
        <Input
          icon={Search}
          placeholder="Search by order # or customer email…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title="Couldn't load orders"
          description="An error occurred while fetching orders. Try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated shadow-md">
          <div className="border-b border-line-subtle bg-bg-sunken px-5 py-3">
            <Skeleton variant="text" lines={1} className="w-48" />
          </div>
          <div className="flex flex-col divide-y divide-line-subtle">
            {Array.from({ length: 7 }).map((_, i) => (
              <div key={i} className="flex items-center gap-5 px-5 py-3.5">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-4 w-40 flex-1" />
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-4 w-8" />
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-28" />
              </div>
            ))}
          </div>
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={Package}
          title={search || status ? 'No orders match your filters' : 'No orders yet'}
          description={
            search || status
              ? 'Try clearing a filter or searching for a different term.'
              : 'Once customers place orders they appear here.'
          }
        />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-md">
            <table className="w-full min-w-[760px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Order
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Customer
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Status
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Items
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Total
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Placed
                  </th>
                </tr>
              </thead>
              <motion.tbody
                variants={listStagger(0.03)}
                initial="hidden"
                animate="show"
              >
                {items.map((o) => (
                  <motion.tr
                    key={o.id}
                    variants={fadeUp}
                    className="border-t border-line-subtle transition-colors duration-150 hover:bg-fill/50"
                  >
                    <td className="px-5 py-3.5">
                      <Link
                        to={`/admin/orders/${o.id}`}
                        className="nums font-mono text-sm font-semibold text-accent hover:underline focus-visible:focus-ring"
                      >
                        #{o.id}
                      </Link>
                    </td>
                    <td className="px-5 py-3.5 text-sm text-ink-primary">
                      {o.customer_email}
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge tone={STATUS_TONE[o.status] ?? 'neutral'} dot>
                        {o.status}
                      </Badge>
                    </td>
                    <td className="px-5 py-3.5 nums text-sm text-ink-secondary">
                      {o.item_count}
                    </td>
                    <td className="px-5 py-3.5 nums text-sm font-semibold text-ink-primary">
                      {formatPrice(o.total_amount, o.currency)}
                    </td>
                    <td className="px-5 py-3.5 text-xs text-ink-tertiary">
                      {formatDate(o.created_at)}
                    </td>
                  </motion.tr>
                ))}
              </motion.tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between gap-2">
              <p className="text-xs text-ink-tertiary">
                Showing{' '}
                <span className="nums font-medium text-ink-secondary">
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)}
                </span>{' '}
                of{' '}
                <span className="nums font-medium text-ink-secondary">
                  {total.toLocaleString()}
                </span>
              </p>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  aria-label="Previous page"
                  className="grid size-8 place-items-center rounded-sm border border-line-subtle text-ink-secondary transition-colors hover:bg-fill focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <span className="nums min-w-[4rem] text-center text-xs text-ink-tertiary">
                  {page} / {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  aria-label="Next page"
                  className="grid size-8 place-items-center rounded-sm border border-line-subtle text-ink-secondary transition-colors hover:bg-fill focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronRight className="size-4" />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </AdminPage>
  );
}
