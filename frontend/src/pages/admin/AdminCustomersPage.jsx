import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Search,
  Users as UsersIcon,
  ChevronLeft,
  ChevronRight,
  Info,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, staggerContainer } from '@/lib/motion.js';
import { useCustomers } from '@/features/customers/hooks.js';

const PAGE_SIZE = 25;

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'deactivated', label: 'Deactivated' },
  { value: 'deleted', label: 'Deleted' },
];

// The segments the RFM scoring job writes to `agg_customer_snapshot`. These
// MUST match `_SEGMENT_RULES` + `_SEGMENT_FALLBACK` in
// backend/app/services/analytics/aggregation/jobs_customer.py exactly: the
// repository filters on raw equality (`snap.rfm_segment == segment`), so a
// value the job never writes returns HTTP 200 with an empty list rather than an
// error, and the operator reads "no customers match" as a fact about the
// business. The backend now rejects unknown values with a 422 so this list
// cannot drift silently again.
const SEGMENT_OPTIONS = [
  { value: '', label: 'All segments' },
  { value: 'champions', label: 'Champions' },
  { value: 'cant_lose', label: "Can't lose" },
  { value: 'loyal', label: 'Loyal' },
  { value: 'at_risk', label: 'At risk' },
  { value: 'new_customers', label: 'New customers' },
  { value: 'promising', label: 'Promising' },
  { value: 'hibernating', label: 'Hibernating' },
  { value: 'lost', label: 'Lost' },
  { value: 'needs_attention', label: 'Needs attention' },
];

const ORDERED_OPTIONS = [
  { value: '', label: 'Everyone' },
  { value: 'true', label: 'Has ordered' },
  { value: 'false', label: 'Never ordered' },
];

const SORT_OPTIONS = [
  { value: 'recent', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'email', label: 'Email A–Z' },
  { value: 'orders', label: 'Most orders' },
  { value: 'last_order', label: 'Most recent order' },
  // Hidden without analytics.customers.view: ranking by spend discloses spend,
  // which is exactly what nulling the column withholds. The API coerces this
  // key away too, so hiding it here is UX, not the enforcement.
  { value: 'ltv', label: 'Highest spend', needsMoney: true },
];

// Keyed by the same names as SEGMENT_OPTIONS. A miss here is cosmetic (the
// badge falls back to 'neutral'), unlike a miss in the filter values, but the
// two lists drifted together and are fixed together.
const SEGMENT_TONE = {
  champions: 'success',
  loyal: 'success',
  cant_lose: 'warning',
  new_customers: 'info',
  promising: 'info',
  at_risk: 'warning',
  hibernating: 'warning',
  needs_attention: 'warning',
  lost: 'danger',
};

function useDebounced(value, ms = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** Thin wrapper over the shared Select so each filter is one line below. */
function Filter({ label, value, onChange, options }) {
  return (
    <Select
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="min-w-[150px]"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </Select>
  );
}

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

function formatMoney(value) {
  if (value == null) return '—';
  return `₹${Number(value).toLocaleString('en-IN', {
    maximumFractionDigits: 0,
  })}`;
}

function CustomerRow({ customer, moneyVisible }) {
  const initial = (customer.email || '?').charAt(0).toUpperCase();
  const snap = customer.snapshot;

  return (
    <motion.tr
      variants={fadeUp}
      className="group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60"
    >
      <td className="px-5 py-3.5">
        <Link
          to={`/admin/customers/${customer.id}`}
          className="flex items-center gap-3 no-underline focus-visible:focus-ring"
        >
          <div className="grid size-9 shrink-0 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
            {initial}
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-ink-primary group-hover:text-accent">
              {customer.full_name || customer.email}
            </p>
            <p className="truncate text-xs text-ink-tertiary">
              {customer.full_name ? customer.email : customer.phone || '—'}
            </p>
          </div>
        </Link>
      </td>
      <td className="px-5 py-3.5">
        <Badge tone={customer.is_active ? 'success' : 'neutral'} dot>
          {customer.account_status === 'active' && !customer.is_active
            ? 'Disabled'
            : customer.account_status.charAt(0).toUpperCase() +
              customer.account_status.slice(1)}
        </Badge>
      </td>
      <td className="px-5 py-3.5 text-sm text-ink-secondary">
        <span className="nums">{snap?.orders_count ?? 0}</span>
      </td>
      <td className="px-5 py-3.5 text-sm text-ink-secondary">
        {formatDate(snap?.last_order_at)}
      </td>
      {moneyVisible && (
        <td className="px-5 py-3.5 text-sm text-ink-secondary">
          <span className="nums">{formatMoney(snap?.gross_ltv)}</span>
        </td>
      )}
      <td className="px-5 py-3.5">
        {snap?.rfm_segment ? (
          <Badge tone={SEGMENT_TONE[snap.rfm_segment] || 'neutral'} size="sm">
            {snap.rfm_segment.replace(/_/g, ' ')}
          </Badge>
        ) : (
          <span className="text-xs text-ink-tertiary">—</span>
        )}
      </td>
      <td className="px-5 py-3.5 text-sm text-ink-secondary">
        <span className="nums">{customer.points_balance}</span>
        {customer.vip_tier && (
          <span className="ml-1.5 text-xs text-ink-tertiary">
            {customer.vip_tier}
          </span>
        )}
      </td>
      <td className="px-5 py-3.5 text-sm text-ink-tertiary">
        {formatDate(customer.created_at)}
      </td>
    </motion.tr>
  );
}

export default function AdminCustomersPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [segment, setSegment] = useState('');
  const [ordered, setOrdered] = useState('');
  const [sort, setSort] = useState('recent');
  const debouncedSearch = useDebounced(search, 250);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, status, segment, ordered, sort]);

  const { data, isLoading, isError, refetch } = useCustomers({
    q: debouncedSearch,
    status: status || undefined,
    segment: segment || undefined,
    has_ordered: ordered === '' ? undefined : ordered === 'true',
    sort,
    page,
    page_size: PAGE_SIZE,
  });

  const items = data?.items || [];
  const total = data?.total || 0;
  const moneyVisible = !!data?.money_visible;
  const snapshotDate = data?.snapshot_date;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <AdminPage
      title="Customers"
      description={
        isLoading
          ? 'Loading…'
          : `${total} customer${total === 1 ? '' : 's'} — everyone who shops here. Staff accounts live under Team.`
      }
    >
      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[220px] flex-1">
          <Input
            icon={Search}
            placeholder="Search email, phone or name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Filter
          label="Status"
          value={status}
          onChange={setStatus}
          options={STATUS_OPTIONS}
        />
        <Filter
          label="Segment"
          value={segment}
          onChange={setSegment}
          options={SEGMENT_OPTIONS}
        />
        <Filter
          label="Orders"
          value={ordered}
          onChange={setOrdered}
          options={ORDERED_OPTIONS}
        />
        <Filter
          label="Sort"
          value={sort}
          onChange={setSort}
          options={SORT_OPTIONS.filter((o) => !o.needsMoney || moneyVisible)}
        />
      </div>

      {/* A rollup figure has to arrive labelled with the day it was computed —
          bare, it reads as "now", which it is not. */}
      {snapshotDate && (
        <p className="flex items-center gap-1.5 text-xs text-ink-tertiary">
          <Info className="size-3.5" aria-hidden="true" />
          Orders, spend and segment are as of{' '}
          <span className="font-medium text-ink-secondary">
            {formatDate(snapshotDate)}
          </span>
          . Status, loyalty and sign-up date are live.
        </p>
      )}

      {isError ? (
        <EmptyState
          icon={UsersIcon}
          iconTone="danger"
          title="Couldn't load customers"
          description="Something went wrong. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <div className="divide-y divide-line-subtle">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                <Skeleton variant="circle" className="size-9 shrink-0" />
                <div className="flex-1">
                  <Skeleton variant="text" lines={1} className="mb-1 w-48" />
                  <Skeleton variant="text" lines={1} className="w-28" />
                </div>
                <Skeleton className="h-5 w-16 rounded-full" />
              </div>
            ))}
          </div>
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title="No customers match these filters"
          description={
            debouncedSearch || status || segment || ordered
              ? 'Try widening the search or clearing a filter.'
              : 'When shoppers register they will appear here.'
          }
        />
      ) : (
        <>
          <motion.div
            className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md"
            variants={staggerContainer(0.02)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[900px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  {[
                    'Customer',
                    'Status',
                    'Orders',
                    'Last order',
                    ...(moneyVisible ? ['Lifetime spend'] : []),
                    'Segment',
                    'Points',
                    'Joined',
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <CustomerRow
                    key={c.id}
                    customer={c}
                    moneyVisible={moneyVisible}
                  />
                ))}
              </tbody>
            </table>
          </motion.div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-ink-tertiary">
                Showing{' '}
                <span className="nums font-medium text-ink-secondary">
                  {(page - 1) * PAGE_SIZE + 1}–
                  {Math.min(page * PAGE_SIZE, total)}
                </span>{' '}
                of <span className="nums font-medium text-ink-secondary">{total}</span>
              </p>
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink-tertiary">
                  Page <span className="nums">{page}</span> of{' '}
                  <span className="nums">{totalPages}</span>
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  aria-label="Previous page"
                  className={cn(
                    'grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors',
                    'hover:border-line-strong hover:bg-fill focus-visible:focus-ring',
                    'disabled:pointer-events-none disabled:opacity-30',
                  )}
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  aria-label="Next page"
                  className={cn(
                    'grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors',
                    'hover:border-line-strong hover:bg-fill focus-visible:focus-ring',
                    'disabled:pointer-events-none disabled:opacity-30',
                  )}
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
