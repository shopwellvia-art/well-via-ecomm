import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Search,
  Shield,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  TicketPercent,
  Coins,
  ShieldCheck,
  Star,
  LogOut,
  Pencil,
  Plus,
  Trash2,
  History,
  RefreshCw,
  Filter,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, listStagger } from '@/lib/motion.js';
import { useAuditEvents } from '@/features/audit/hooks.js';

const PAGE_SIZE = 50;

// ─── Action metadata ──────────────────────────────────────────────────────────

const ACTION_META = {
  'role.create':         { label: 'Role created',      icon: Plus,          tone: 'success' },
  'role.update':         { label: 'Role updated',      icon: Pencil,        tone: 'info'    },
  'role.delete':         { label: 'Role deleted',      icon: Trash2,        tone: 'danger'  },
  'role.assign':         { label: 'Roles assigned',    icon: ShieldCheck,   tone: 'info'    },
  'coupon.create':       { label: 'Coupon created',    icon: Plus,          tone: 'success' },
  'coupon.update':       { label: 'Coupon updated',    icon: TicketPercent, tone: 'info'    },
  'coupon.delete':       { label: 'Coupon deleted',    icon: Trash2,        tone: 'danger'  },
  'loyalty.adjust':      { label: 'Points adjusted',   icon: Coins,         tone: 'warning' },
  'review.admin_create': { label: 'Review added',      icon: Plus,          tone: 'success' },
  'review.admin_update': { label: 'Review moderated',  icon: Star,          tone: 'info'    },
  'review.admin_delete': { label: 'Review deleted',    icon: Trash2,        tone: 'danger'  },
  'session.revoke_all':  { label: 'Sessions revoked',  icon: LogOut,        tone: 'danger'  },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDateTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ─── ActionBadge ─────────────────────────────────────────────────────────────

function ActionBadge({ action }) {
  const meta = ACTION_META[action] || { label: action, icon: History, tone: 'neutral' };
  const Icon = meta.icon;
  return (
    <Badge tone={meta.tone} size="sm">
      <Icon className="size-3" aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}

// ─── MetadataPreview ──────────────────────────────────────────────────────────

function MetadataPreview({ data }) {
  if (!data) return null;

  if (data.changes && typeof data.changes === 'object') {
    const entries = Object.entries(data.changes);
    return (
      <dl className="grid gap-2 text-xs">
        {entries.map(([key, change]) => (
          <div key={key} className="flex flex-wrap items-baseline gap-2">
            <dt className="font-medium text-ink-secondary">{key}</dt>
            {Array.isArray(change) ? (
              <dd className="nums text-ink-primary">{change.join(', ')}</dd>
            ) : change && typeof change === 'object' && 'before' in change ? (
              <dd className="nums font-mono text-ink-primary">
                <span className="text-ink-tertiary line-through">
                  {JSON.stringify(change.before)}
                </span>
                {' → '}
                <span>{JSON.stringify(change.after)}</span>
              </dd>
            ) : (
              <dd className="nums font-mono text-ink-primary">{JSON.stringify(change)}</dd>
            )}
          </div>
        ))}
      </dl>
    );
  }

  return (
    <pre className="overflow-x-auto rounded-md border border-line-subtle bg-bg-sunken p-3 text-[11px] leading-relaxed text-ink-secondary">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// ─── AuditRow ─────────────────────────────────────────────────────────────────

function AuditRow({ event }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = event.extra && Object.keys(event.extra).length > 0;
  const meta = ACTION_META[event.action];

  // Derive severity tone for the left border accent
  const severityBorder = {
    danger:  'border-l-2 border-l-danger/60',
    warning: 'border-l-2 border-l-warning/60',
    success: 'border-l-2 border-l-success/60',
    info:    'border-l-2 border-l-accent/40',
  }[meta?.tone] ?? '';

  return (
    <>
      <motion.tr
        variants={fadeUp}
        className={cn(
          'group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60',
          severityBorder,
        )}
      >
        {/* Action badge */}
        <td className="px-5 py-3.5">
          <ActionBadge action={event.action} />
        </td>

        {/* Summary + target */}
        <td className="px-5 py-3.5">
          <p className="text-sm font-medium text-ink-primary">{event.summary}</p>
          {event.target_label && (
            <p className="mt-0.5 text-[11px] text-ink-tertiary">
              target:{' '}
              <span className="nums font-mono text-ink-secondary">{event.target_label}</span>
              {event.target_id != null && (
                <span className="nums ml-1 text-ink-tertiary/70">#{event.target_id}</span>
              )}
            </p>
          )}
        </td>

        {/* Actor */}
        <td className="px-5 py-3.5">
          {event.actor_email ? (
            <>
              <div className="flex items-center gap-2">
                <div className="grid size-7 shrink-0 place-items-center rounded-full bg-accent/12 text-[10px] font-semibold text-accent">
                  {event.actor_email.charAt(0).toUpperCase()}
                </div>
                <p className="truncate text-sm text-ink-primary">{event.actor_email}</p>
              </div>
              {event.actor_ip && (
                <p className="nums mt-0.5 pl-9 font-mono text-[11px] text-ink-tertiary">
                  {event.actor_ip}
                </p>
              )}
            </>
          ) : (
            <span className="text-sm text-ink-tertiary">—</span>
          )}
        </td>

        {/* Timestamp */}
        <td className="px-5 py-3.5">
          <span className="nums text-xs text-ink-secondary">
            {formatDateTime(event.created_at)}
          </span>
        </td>

        {/* Expand toggle */}
        <td className="px-5 py-3.5 text-right">
          {hasDetail ? (
            <button
              type="button"
              aria-label={expanded ? 'Hide detail' : 'Show detail'}
              onClick={() => setExpanded((v) => !v)}
              className={cn(
                'grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors focus-visible:focus-ring',
                expanded ? 'bg-accent/12 text-accent' : 'hover:bg-fill hover:text-ink-primary',
              )}
            >
              {expanded ? (
                <ChevronUp className="size-4" />
              ) : (
                <ChevronDown className="size-4" />
              )}
            </button>
          ) : (
            <span className="text-[10px] text-ink-tertiary">—</span>
          )}
        </td>
      </motion.tr>

      {/* Expanded detail panel */}
      {expanded && hasDetail && (
        <motion.tr
          variants={fadeUp}
          initial="hidden"
          animate="show"
          className="bg-bg-sunken/50"
        >
          <td colSpan={5} className="px-5 py-4">
            <div className="rounded-lg border border-line-subtle bg-bg-elevated p-4 shadow-sm">
              <p className="mb-2.5 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
                Event detail
              </p>
              <MetadataPreview data={event.extra} />
            </div>
          </td>
        </motion.tr>
      )}
    </>
  );
}

// ─── Skeleton rows ────────────────────────────────────────────────────────────

function TableSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
      <div className="border-b border-line-subtle bg-bg-sunken/60 px-5 py-3">
        <Skeleton variant="text" lines={1} className="w-32" />
      </div>
      <div className="divide-y divide-line-subtle">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-5 py-3.5">
            <Skeleton className="h-6 w-28 rounded-full" />
            <div className="flex-1">
              <Skeleton variant="text" lines={1} className="mb-1 w-56" />
              <Skeleton variant="text" lines={1} className="w-36" />
            </div>
            <Skeleton variant="text" lines={1} className="w-32" />
            <Skeleton variant="text" lines={1} className="w-28" />
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AdminAuditPage() {
  const [action, setAction] = useState('');
  const [targetType, setTargetType] = useState('');
  const [page, setPage] = useState(1);

  useEffect(() => setPage(1), [action, targetType]);

  const opts = useMemo(
    () => ({
      action: action || undefined,
      target_type: targetType || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [action, targetType, page],
  );

  const { data, isLoading, isError, refetch } = useAuditEvents(opts);
  const items = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const actions = data?.available_actions || [];

  const TARGET_TYPES = ['role', 'coupon', 'user', 'review'];

  const isFiltered = !!(action || targetType);

  return (
    <AdminPage
      title="Audit log"
      description="Immutable record of admin actions. Useful for incident response, dispute resolution, and compliance."
    >
      {/* Filter toolbar */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
          <Filter className="size-3.5" aria-hidden="true" />
          Filter
        </div>
        <div className="flex flex-1 flex-wrap gap-3">
          <div className="min-w-[180px] flex-1">
            <Select
              label="Action type"
              value={action}
              onChange={(e) => setAction(e.target.value)}
            >
              <option value="">All actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {ACTION_META[a]?.label || a}
                </option>
              ))}
            </Select>
          </div>
          <div className="min-w-[160px] flex-1">
            <Select
              label="Target type"
              value={targetType}
              onChange={(e) => setTargetType(e.target.value)}
            >
              <option value="">All target types</option>
              {TARGET_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="flex items-end gap-2">
          {isFiltered && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { setAction(''); setTargetType(''); }}
            >
              Clear filters
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => refetch()}
            disabled={isLoading}
            aria-label="Refresh audit log"
          >
            <RefreshCw className={cn('size-4', isLoading && 'animate-spin')} aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Body states */}
      {isError ? (
        <EmptyState
          icon={Shield}
          iconTone="danger"
          title="Couldn't load audit events"
          description="Something went wrong fetching the log. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <TableSkeleton />
      ) : items.length === 0 ? (
        <EmptyState
          icon={History}
          size="sm"
          bordered={false}
          title={isFiltered ? 'No events match your filters' : 'No audit events yet'}
          description={
            isFiltered
              ? 'Try clearing one of the active filters.'
              : 'Sensitive admin actions will appear here as they happen.'
          }
        />
      ) : (
        <>
          {/* Summary strip */}
          <div className="flex items-center gap-3">
            <p className="text-xs text-ink-tertiary">
              <span className="nums font-medium text-ink-secondary">{total.toLocaleString()}</span>{' '}
              event{total === 1 ? '' : 's'}
            </p>
            {isFiltered && (
              <Badge tone="info" size="sm">Filtered</Badge>
            )}
          </div>

          {/* Table */}
          <motion.div
            className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md"
            variants={listStagger(0.03)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[820px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Action
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Summary / Target
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Actor
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    When
                  </th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Detail
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <AuditRow key={e.id} event={e} />
                ))}
              </tbody>
            </table>
          </motion.div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between gap-2">
              <p className="text-xs text-ink-tertiary">
                Page{' '}
                <span className="nums font-medium text-ink-secondary">{page}</span>{' '}
                of{' '}
                <span className="nums font-medium text-ink-secondary">{totalPages}</span>
                {' '}—{' '}
                <span className="nums font-medium text-ink-secondary">
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)}
                </span>{' '}
                of{' '}
                <span className="nums font-medium text-ink-secondary">
                  {total.toLocaleString()}
                </span>{' '}
                events
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  aria-label="Previous page"
                  className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:border-line-strong hover:bg-fill focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  aria-label="Next page"
                  className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:border-line-strong hover:bg-fill focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
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
