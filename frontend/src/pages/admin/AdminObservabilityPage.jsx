import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  Search,
  Zap,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { KPICard } from '@/components/admin/KPICard.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import {
  useObservabilityOverview,
  useObservabilityRequests,
  useObservabilityRoutes,
  useObservabilitySlowQueries,
} from '@/features/observability/hooks.js';
import { fadeUp, listStagger } from '@/lib/motion.js';

// ─── Constants ────────────────────────────────────────────────────────────────

const PERIODS = [
  { value: '1h', label: '1h' },
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
];

const PAGE_SIZE = 50;

const TOOLTIP_STYLE = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--line-subtle)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--ink-primary)',
  boxShadow: 'var(--shadow-md)',
};

const AXIS_TICK = { fontSize: 11, fill: 'currentColor' };

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatMs(ms) {
  if (ms == null) return '—';
  const n = Number(ms);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}s`;
  return `${Math.round(n)}ms`;
}

function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function statusTone(status) {
  const s = Number(status);
  if (s >= 500) return 'text-danger bg-danger/10';
  if (s >= 400) return 'text-warning bg-warning/10';
  if (s >= 300) return 'text-ink-secondary bg-fill';
  if (s >= 200) return 'text-success bg-success/10';
  return 'text-ink-tertiary bg-fill';
}

// ─── Segmented control ─────────────────────────────────────────────────────────

function SegmentedControl({ options, value, onChange }) {
  return (
    <div
      className="inline-flex rounded-sm border border-line-subtle bg-bg-elevated"
      role="group"
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          aria-pressed={value === opt.value}
          className={cn(
            'px-3 py-1.5 text-xs font-medium transition-colors',
            'first:rounded-l-sm last:rounded-r-sm focus-visible:focus-ring',
            value === opt.value
              ? 'bg-accent text-ink-inverse shadow-glow-sm'
              : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ─── Latency over time chart ───────────────────────────────────────────────────

function LatencySeriesChart({ series, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;

  const hasData = Array.isArray(series) && series.length > 0;

  return (
    <Card>
      <CardHeader title="Latency over time" />
      <div className="p-5 pt-4">
        {!hasData ? (
          <EmptyState
            icon={Clock}
            size="sm"
            bordered={false}
            title="No data yet"
            description="Generate some traffic to see latency trends."
          />
        ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={series}
                margin={{ top: 8, right: 8, left: -20, bottom: 0 }}
              >
                <defs>
                  <linearGradient id="obsAvgFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#6366f1" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="obsMaxFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#f87171" stopOpacity={0.12} />
                    <stop offset="100%" stopColor="#f87171" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--grid-line)"
                  vertical={false}
                />
                <XAxis
                  dataKey="bucket"
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                  minTickGap={40}
                />
                <YAxis
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                  tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: 'var(--ink-tertiary)', marginBottom: 4 }}
                  formatter={(value, name) => {
                    if (name === 'avg_ms') return [formatMs(value), 'Avg latency'];
                    if (name === 'max_ms') return [formatMs(value), 'Max latency'];
                    if (name === 'count') return [value, 'Requests'];
                    return [value, name];
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="avg_ms"
                  stroke="#6366f1"
                  strokeWidth={2}
                  fill="url(#obsAvgFill)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#6366f1', strokeWidth: 0 }}
                />
                <Area
                  type="monotone"
                  dataKey="max_ms"
                  stroke="#f87171"
                  strokeWidth={1.5}
                  fill="url(#obsMaxFill)"
                  strokeDasharray="4 3"
                  dot={false}
                  activeDot={{ r: 3, fill: '#f87171', strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

// ─── Slowest routes chart ──────────────────────────────────────────────────────

function SlowestRoutesChart({ routes, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;

  const hasData = Array.isArray(routes) && routes.length > 0;

  return (
    <Card>
      <CardHeader title="Slowest routes (avg)" />
      <div className="p-5 pt-4">
        {!hasData ? (
          <EmptyState
            icon={Zap}
            size="sm"
            bordered={false}
            title="No route data yet"
            description="Route timing will appear as requests come in."
          />
        ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={routes}
                layout="vertical"
                margin={{ top: 0, right: 8, left: 4, bottom: 0 }}
                barCategoryGap="28%"
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--grid-line)"
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                  tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)}
                />
                <YAxis
                  type="category"
                  dataKey="route"
                  width={140}
                  tick={{ ...AXIS_TICK, width: 136 }}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                  tickFormatter={(v) => (v.length > 22 ? `…${v.slice(-20)}` : v)}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: 'var(--ink-tertiary)', marginBottom: 4 }}
                  formatter={(value, name) => {
                    if (name === 'avg_ms') return [formatMs(value), 'Avg latency'];
                    return [value, name];
                  }}
                />
                <Bar
                  dataKey="avg_ms"
                  fill="#6366f1"
                  radius={[0, 4, 4, 0]}
                  maxBarSize={18}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

// ─── Table skeleton ────────────────────────────────────────────────────────────

function TableRowsSkeleton({ cols = 6, rows = 8 }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated">
      <div className="divide-y divide-line-subtle">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-5 py-3">
            {Array.from({ length: cols }).map((__, j) => (
              <Skeleton key={j} className={cn('h-4 rounded-sm', j === 0 ? 'w-24' : 'flex-1')} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Pagination ────────────────────────────────────────────────────────────────

function Pagination({ page, totalPages, total, onPage }) {
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center justify-between gap-2">
      <p className="text-xs text-ink-tertiary">
        Page{' '}
        <span className="nums font-medium text-ink-secondary">{page}</span>{' '}
        of{' '}
        <span className="nums font-medium text-ink-secondary">{totalPages}</span>
        {' — '}
        <span className="nums font-medium text-ink-secondary">
          {total.toLocaleString()}
        </span>{' '}
        total
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page === 1}
          aria-label="Previous page"
          className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:border-line-strong hover:bg-fill focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
        >
          <ChevronLeft className="size-4" />
        </button>
        <button
          type="button"
          onClick={() => onPage(Math.min(totalPages, page + 1))}
          disabled={page === totalPages}
          aria-label="Next page"
          className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:border-line-strong hover:bg-fill focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>
    </div>
  );
}

// ─── Tab: Request Logs ─────────────────────────────────────────────────────────

function RequestLogsTab({ period }) {
  const [filterMethod, setFilterMethod] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterQ, setFilterQ] = useState('');
  const [page, setPage] = useState(1);

  useEffect(() => setPage(1), [filterMethod, filterStatus, filterQ]);

  const { data, isLoading, isError } = useObservabilityRequests({
    period,
    method: filterMethod || undefined,
    status: filterStatus ? Number(filterStatus) : undefined,
    q: filterQ || undefined,
    page,
    page_size: PAGE_SIZE,
  });

  const items = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[130px]">
          <Select
            label="Method"
            value={filterMethod}
            onChange={(e) => setFilterMethod(e.target.value)}
          >
            <option value="">All methods</option>
            {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </Select>
        </div>
        <div className="min-w-[120px]">
          <label className="mb-1.5 block text-sm font-medium text-ink-secondary">
            Status
          </label>
          <input
            type="number"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            placeholder="e.g. 500"
            className={cn(
              'h-11 w-full rounded-sm border border-line-subtle bg-bg-sunken px-3.5 text-sm text-ink-primary',
              'transition-[border-color,box-shadow] duration-200',
              'hover:border-line-strong',
              'focus-visible:border-accent focus-visible:outline-none',
              'focus-visible:[box-shadow:var(--accent-glow)]',
            )}
          />
        </div>
        <div className="min-w-[200px] flex-1">
          <label className="mb-1.5 block text-sm font-medium text-ink-secondary">
            Route search
          </label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-tertiary" />
            <input
              type="text"
              value={filterQ}
              onChange={(e) => setFilterQ(e.target.value)}
              placeholder="Filter by route..."
              className={cn(
                'h-11 w-full rounded-sm border border-line-subtle bg-bg-sunken pl-9 pr-3.5 text-sm text-ink-primary',
                'transition-[border-color,box-shadow] duration-200',
                'hover:border-line-strong',
                'focus-visible:border-accent focus-visible:outline-none',
                'focus-visible:[box-shadow:var(--accent-glow)]',
              )}
            />
          </div>
        </div>
      </div>

      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          size="sm"
          bordered={false}
          title="Couldn't load request logs"
          description="Something went wrong fetching logs."
        />
      ) : isLoading ? (
        <TableRowsSkeleton cols={7} rows={8} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Activity}
          size="sm"
          bordered={false}
          title="No data yet — generate some traffic"
          description="Request logs will appear here once the backend starts receiving requests."
        />
      ) : (
        <>
          <motion.div
            className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-sm"
            variants={listStagger(0.02)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[820px] text-sm">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Time</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Method</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Status</th>
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Route</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Total</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">DB</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Queries</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-subtle">
                {items.map((row) => (
                  <motion.tr
                    key={row.id}
                    variants={fadeUp}
                    className="transition-colors hover:bg-fill/60"
                  >
                    <td className="px-4 py-3">
                      <span className="nums text-xs text-ink-tertiary">{formatDateTime(row.ts)}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded-sm bg-fill px-1.5 py-0.5 font-mono text-xs font-medium text-ink-secondary">
                        {row.method}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'rounded-sm px-1.5 py-0.5 font-mono text-xs font-semibold',
                          statusTone(row.status),
                        )}
                      >
                        {row.status}
                      </span>
                    </td>
                    <td className="max-w-[260px] px-4 py-3">
                      <span className="block truncate font-mono text-xs text-ink-primary" title={row.route}>
                        {row.route}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="nums text-xs text-ink-primary">{formatMs(row.total_ms)}</span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="nums text-xs text-ink-secondary">{formatMs(row.db_ms)}</span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="nums text-xs text-ink-secondary">{row.query_count ?? '—'}</span>
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </motion.div>
          <Pagination page={page} totalPages={totalPages} total={total} onPage={setPage} />
        </>
      )}
    </div>
  );
}

// ─── Tab: Aggregated Logs ──────────────────────────────────────────────────────

function AggregatedLogsTab({ period }) {
  const [sort, setSort] = useState('avg');

  const { data, isLoading, isError } = useObservabilityRoutes({ period, sort });

  const items = Array.isArray(data) ? data : [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-ink-tertiary">Grouped by route. Showing all routes for the selected period.</p>
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-secondary">Sort by</span>
          <SegmentedControl
            options={[
              { value: 'avg', label: 'Avg' },
              { value: 'count', label: 'Count' },
              { value: 'max', label: 'Max' },
            ]}
            value={sort}
            onChange={setSort}
          />
        </div>
      </div>

      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          size="sm"
          bordered={false}
          title="Couldn't load aggregated route data"
          description="Something went wrong fetching route aggregations."
        />
      ) : isLoading ? (
        <TableRowsSkeleton cols={6} rows={8} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Activity}
          size="sm"
          bordered={false}
          title="No data yet — generate some traffic"
          description="Aggregated route stats will appear here once requests are logged."
        />
      ) : (
        <motion.div
          className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-sm"
          variants={listStagger(0.02)}
          initial="hidden"
          animate="show"
        >
          <table className="w-full min-w-[700px] text-sm">
            <thead>
              <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Route</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Count</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Avg</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Min</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Max</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Avg DB</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-subtle">
              {items.map((row) => (
                <motion.tr
                  key={row.route}
                  variants={fadeUp}
                  className="transition-colors hover:bg-fill/60"
                >
                  <td className="max-w-[320px] px-4 py-3">
                    <span className="block truncate font-mono text-xs text-ink-primary" title={row.route}>
                      {row.route}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="nums text-xs text-ink-secondary">{Number(row.count).toLocaleString()}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="nums text-xs text-ink-primary">{formatMs(row.avg_ms)}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="nums text-xs text-success">{formatMs(row.min_ms)}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className={cn('nums text-xs', row.max_ms > 1000 ? 'text-warning' : 'text-ink-secondary')}>
                      {formatMs(row.max_ms)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="nums text-xs text-ink-secondary">{formatMs(row.avg_db_ms)}</span>
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </motion.div>
      )}
    </div>
  );
}

// ─── Tab: Slow Queries ─────────────────────────────────────────────────────────

function SlowQueriesTab({ period }) {
  const [view, setView] = useState('queries');
  const [page, setPage] = useState(1);

  useEffect(() => setPage(1), [view]);

  const { data, isLoading, isError } = useObservabilitySlowQueries({
    period,
    view,
    page,
    page_size: PAGE_SIZE,
  });

  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const fingerprints = data?.fingerprints || [];
  const tables = data?.tables || [];
  const recent = data?.recent || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <SegmentedControl
          options={[
            { value: 'queries', label: 'Queries' },
            { value: 'table', label: 'By table' },
            { value: 'recent', label: 'Recent' },
          ]}
          value={view}
          onChange={setView}
        />
      </div>

      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          size="sm"
          bordered={false}
          title="Couldn't load slow query data"
          description="Something went wrong fetching slow queries."
        />
      ) : isLoading ? (
        <TableRowsSkeleton cols={5} rows={7} />
      ) : view === 'queries' ? (
        fingerprints.length === 0 ? (
          <EmptyState
            icon={Database}
            size="sm"
            bordered={false}
            title="No data yet — generate some traffic"
            description="Slow query fingerprints will appear here as requests are processed."
          />
        ) : (
          <>
            <motion.div
              className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-sm"
              variants={listStagger(0.02)}
              initial="hidden"
              animate="show"
            >
              <table className="w-full min-w-[860px] text-sm">
                <thead>
                  <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">SQL (normalized)</th>
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Table</th>
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Op</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Count</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Avg</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Max</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line-subtle">
                  {fingerprints.map((row) => (
                    <motion.tr
                      key={row.fingerprint_hash}
                      variants={fadeUp}
                      className="transition-colors hover:bg-fill/60"
                    >
                      <td className="max-w-[340px] px-4 py-3">
                        <span
                          className="block truncate font-mono text-xs text-ink-primary"
                          title={row.sql_normalized}
                        >
                          {row.sql_normalized}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs text-ink-secondary">{row.table_name || '—'}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="rounded-sm bg-fill px-1.5 py-0.5 font-mono text-xs text-ink-secondary">
                          {row.operation || '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="nums text-xs text-ink-secondary">{Number(row.count).toLocaleString()}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="nums text-xs text-warning">{formatMs(row.avg_ms)}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="nums text-xs text-danger">{formatMs(row.max_ms)}</span>
                      </td>
                    </motion.tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
            <Pagination page={page} totalPages={totalPages} total={total} onPage={setPage} />
          </>
        )
      ) : view === 'table' ? (
        tables.length === 0 ? (
          <EmptyState
            icon={Database}
            size="sm"
            bordered={false}
            title="No data yet — generate some traffic"
            description="Table-level query stats will appear here once slow queries are captured."
          />
        ) : (
          <motion.div
            className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-sm"
            variants={listStagger(0.02)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Table</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Count</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Avg</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Max</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-subtle">
                {tables.map((row) => (
                  <motion.tr
                    key={row.table_name}
                    variants={fadeUp}
                    className="transition-colors hover:bg-fill/60"
                  >
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs font-medium text-ink-primary">{row.table_name}</span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="nums text-xs text-ink-secondary">{Number(row.count).toLocaleString()}</span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="nums text-xs text-warning">{formatMs(row.avg_ms)}</span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="nums text-xs text-danger">{formatMs(row.max_ms)}</span>
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </motion.div>
        )
      ) : (
        /* view === 'recent' */
        recent.length === 0 ? (
          <EmptyState
            icon={Database}
            size="sm"
            bordered={false}
            title="No data yet — generate some traffic"
            description="Recent slow queries will appear here as they are captured."
          />
        ) : (
          <>
            <motion.div
              className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-sm"
              variants={listStagger(0.02)}
              initial="hidden"
              animate="show"
            >
              <table className="w-full min-w-[860px] text-sm">
                <thead>
                  <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Time</th>
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Route</th>
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Table</th>
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Op</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Duration</th>
                    <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">SQL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line-subtle">
                  {recent.map((row) => (
                    <motion.tr
                      key={row.id}
                      variants={fadeUp}
                      className="transition-colors hover:bg-fill/60"
                    >
                      <td className="px-4 py-3">
                        <span className="nums text-xs text-ink-tertiary">{formatDateTime(row.ts)}</span>
                      </td>
                      <td className="max-w-[180px] px-4 py-3">
                        <span className="block truncate font-mono text-xs text-ink-secondary" title={row.route}>
                          {row.route}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs text-ink-secondary">{row.table_name || '—'}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="rounded-sm bg-fill px-1.5 py-0.5 font-mono text-xs text-ink-secondary">
                          {row.operation || '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="nums text-xs text-danger">{formatMs(row.duration_ms)}</span>
                      </td>
                      <td className="max-w-[280px] px-4 py-3">
                        <span
                          className="block truncate font-mono text-xs text-ink-primary"
                          title={row.sql_normalized}
                        >
                          {row.sql_normalized}
                        </span>
                      </td>
                    </motion.tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
            <Pagination page={page} totalPages={totalPages} total={total} onPage={setPage} />
          </>
        )
      )}
    </div>
  );
}

// ─── Page ──────────────────────────────────────────────────────────────────────

const TABS = [
  { key: 'requests', label: 'Request Logs' },
  { key: 'aggregated', label: 'Aggregated Logs' },
  { key: 'slow', label: 'Slow Queries' },
];

export default function AdminObservabilityPage() {
  const [period, setPeriod] = useState('24h');
  const [activeTab, setActiveTab] = useState('requests');

  const { data: overview, isLoading: overviewLoading, isError: overviewError, refetch } =
    useObservabilityOverview({ period });

  const fmtInt = (v) => Number(v || 0).toLocaleString();
  const fmtPct = (v) => (v != null ? `${Number(v).toFixed(2)}%` : '—');
  const fmtMs = (v) => (v != null ? formatMs(v) : '—');
  const fmtMaxMs = (v) => {
    if (v == null) return '—';
    const n = Number(v);
    if (n >= 1000) return `${(n / 1000).toFixed(1)}s`;
    return `${Math.round(n)}ms`;
  };

  return (
    <AdminPage
      title="Observability"
      description="Backend request timing (client-traced) and database query timing (server-side)."
      action={
        <SegmentedControl
          options={PERIODS}
          value={period}
          onChange={setPeriod}
        />
      }
    >
      {/* Error banner */}
      {overviewError && (
        <motion.div variants={fadeUp}>
          <Card className="border-danger/30 bg-danger/8 p-4 text-sm text-danger">
            Couldn&apos;t load observability data.{' '}
            <button
              type="button"
              onClick={() => refetch()}
              className="ml-1 underline underline-offset-2 hover:text-ink-primary focus-visible:focus-ring"
            >
              Try again
            </button>
          </Card>
        </motion.div>
      )}

      {/* KPI row — 5 cards */}
      <motion.div
        variants={fadeUp}
        className="grid grid-cols-2 gap-4 lg:grid-cols-5"
      >
        <KPICard
          label="Requests"
          icon={Activity}
          value={overview?.requests}
          previousValue={undefined}
          deltaPct={null}
          format={fmtInt}
          tone="accent"
          loading={overviewLoading}
        />
        <KPICard
          label="Error rate (%)"
          icon={AlertTriangle}
          value={overview?.error_rate}
          previousValue={undefined}
          deltaPct={null}
          format={fmtPct}
          tone={
            overview?.error_rate == null
              ? 'accent'
              : overview.error_rate > 5
                ? 'warning'
                : 'success'
          }
          loading={overviewLoading}
        />
        <KPICard
          label="Avg latency"
          icon={Clock}
          value={overview?.avg_latency_ms}
          previousValue={undefined}
          deltaPct={null}
          format={fmtMs}
          tone="info"
          loading={overviewLoading}
        />
        <KPICard
          label="Avg DB I/O"
          icon={Database}
          value={overview?.avg_db_ms}
          previousValue={undefined}
          deltaPct={null}
          format={fmtMs}
          tone="accent"
          loading={overviewLoading}
        />
        <KPICard
          label="Max latency"
          icon={Zap}
          value={overview?.max_latency_ms}
          previousValue={undefined}
          deltaPct={null}
          format={fmtMaxMs}
          tone={
            overview?.max_latency_ms == null
              ? 'accent'
              : overview.max_latency_ms > 5000
                ? 'warning'
                : 'success'
          }
          loading={overviewLoading}
        />
      </motion.div>

      {/* Charts row */}
      <motion.div
        variants={fadeUp}
        className="grid gap-4 lg:grid-cols-2"
      >
        <LatencySeriesChart
          series={overview?.latency_series || []}
          loading={overviewLoading}
        />
        <SlowestRoutesChart
          routes={overview?.slowest_routes || []}
          loading={overviewLoading}
        />
      </motion.div>

      {/* Tab bar + tab content */}
      <motion.div variants={fadeUp} className="space-y-5">
        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-line-subtle">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                'relative px-4 py-2.5 text-sm font-medium transition-colors focus-visible:focus-ring',
                activeTab === tab.key
                  ? 'text-accent'
                  : 'text-ink-secondary hover:text-ink-primary',
              )}
            >
              {tab.label}
              {activeTab === tab.key && (
                <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-t-full bg-accent" />
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div>
          {activeTab === 'requests' && <RequestLogsTab period={period} />}
          {activeTab === 'aggregated' && <AggregatedLogsTab period={period} />}
          {activeTab === 'slow' && <SlowQueriesTab period={period} />}
        </div>
      </motion.div>
    </AdminPage>
  );
}
