import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  ShoppingBag,
  CircleDollarSign,
  Users,
  Receipt,
  TrendingUp,
  TriangleAlert,
  Package,
  Clock,
  CheckCircle2,
  Truck,
  XCircle,
  RotateCcw,
  Sparkles,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { KPICard } from '@/components/admin/KPICard.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useDashboardOverview } from '@/features/dashboard/hooks.js';
import { fadeUp, listStagger } from '@/lib/motion.js';

const PERIODS = [
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
];

const STATUS_META = {
  pending:   { label: 'Pending',   color: '#9ca3af', icon: Clock },
  paid:      { label: 'Paid',      color: '#a8a29e', icon: CheckCircle2 },
  shipped:   { label: 'Shipped',   color: '#60a5fa', icon: Truck },
  delivered: { label: 'Delivered', color: '#34d399', icon: CheckCircle2 },
  cancelled: { label: 'Cancelled', color: '#fbbf24', icon: XCircle },
  refunded:  { label: 'Refunded',  color: '#f87171', icon: RotateCcw },
};

// ─── Revenue chart ──────────────────────────────────────────────────────────

function RevenueChart({ series, currency, loading }) {
  if (loading) {
    return <Skeleton className="h-72 w-full rounded-lg" />;
  }
  const hasData = series.some((p) => p.revenue > 0);
  if (!hasData) {
    return (
      <Card>
        <div className="p-6">
          <EmptyState
            icon={TrendingUp}
            size="sm"
            bordered={false}
            title="No revenue in this period"
            description="Once an order moves to PAID it'll show up on this chart."
          />
        </div>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader title="Revenue over time" />
      <div className="p-5 pt-4">
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="revFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--success, #22c55e)" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="var(--success, #22c55e)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: 'currentColor' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(d) =>
                  new Date(d).toLocaleDateString(undefined, {
                    month: 'short',
                    day: 'numeric',
                  })
                }
                className="text-ink-tertiary"
                minTickGap={36}
              />
              <YAxis
                tick={{ fontSize: 11, fill: 'currentColor' }}
                axisLine={false}
                tickLine={false}
                className="text-ink-tertiary"
                tickFormatter={(v) =>
                  v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
                }
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--line-subtle)',
                  borderRadius: 8,
                  fontSize: 12,
                  color: 'var(--ink-primary)',
                  boxShadow: 'var(--shadow-md)',
                }}
                labelStyle={{ color: 'var(--ink-tertiary)', marginBottom: 4 }}
                labelFormatter={(d) =>
                  new Date(d).toLocaleDateString(undefined, {
                    weekday: 'short',
                    month: 'short',
                    day: 'numeric',
                  })
                }
                formatter={(value, name) => {
                  if (name === 'revenue') return [formatPrice(value, currency), 'Revenue'];
                  return [value, name];
                }}
              />
              <Area
                type="monotone"
                dataKey="revenue"
                stroke="#22c55e"
                strokeWidth={2}
                fill="url(#revFill)"
                dot={false}
                activeDot={{ r: 4, fill: '#22c55e', strokeWidth: 0 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </Card>
  );
}

// ─── Order status donut ──────────────────────────────────────────────────────

function StatusDonut({ data, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;
  const rows = Object.entries(data || {}).map(([status, count]) => ({
    name: STATUS_META[status]?.label || status,
    value: count,
    color: STATUS_META[status]?.color || '#9ca3af',
    raw: status,
  }));
  const total = rows.reduce((a, r) => a + r.value, 0);
  if (total === 0) {
    return (
      <Card>
        <div className="p-6">
          <EmptyState
            icon={ShoppingBag}
            size="sm"
            bordered={false}
            title="No orders yet"
            description="Status breakdown will appear here once customers buy."
          />
        </div>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader title="Orders by status" />
      <div className="p-5 pt-3">
        <div className="grid items-center gap-4 sm:grid-cols-[168px_minmax(0,1fr)]">
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={rows}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={40}
                  outerRadius={65}
                  strokeWidth={2}
                  stroke="var(--bg-elevated)"
                  paddingAngle={2}
                >
                  {rows.map((r) => (
                    <Cell key={r.name} fill={r.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--line-subtle)',
                    borderRadius: 8,
                    fontSize: 12,
                    color: 'var(--ink-primary)',
                    boxShadow: 'var(--shadow-md)',
                  }}
                  formatter={(value, name) => [value, name]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="flex flex-col gap-1.5">
            {rows.map((r) => {
              const pct = total > 0 ? Math.round((r.value / total) * 100) : 0;
              return (
                <li key={r.name} className="flex items-center gap-2 text-xs">
                  <span
                    className="inline-block size-2.5 shrink-0 rounded-sm"
                    style={{ backgroundColor: r.color }}
                    aria-hidden="true"
                  />
                  <span className="flex-1 capitalize text-ink-secondary">
                    {r.name}
                  </span>
                  <span className="nums text-ink-primary">
                    {r.value}
                    <span className="ml-1 text-ink-tertiary">({pct}%)</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </Card>
  );
}

// ─── Recent orders list ──────────────────────────────────────────────────────

function RecentOrdersList({ rows, loading }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader
        title="Recent orders"
        action={
          <Link
            to="/admin/orders"
            className="text-xs text-accent transition-opacity hover:opacity-70 focus-visible:focus-ring"
          >
            View all
          </Link>
        }
      />
      {loading ? (
        <div className="flex flex-col gap-1 p-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="px-5 py-8">
          <EmptyState
            icon={ShoppingBag}
            size="sm"
            bordered={false}
            title="No orders yet"
          />
        </div>
      ) : (
        <motion.ul
          variants={listStagger(0.04)}
          initial="hidden"
          animate="show"
          className="divide-y divide-line-subtle"
        >
          {rows.map((o) => {
            const meta = STATUS_META[o.status] || { color: '#9ca3af', label: o.status };
            return (
              <motion.li key={o.id} variants={fadeUp}>
                <Link
                  to={`/admin/orders/${o.id}`}
                  className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-fill focus-visible:focus-ring"
                >
                  <span className="font-mono text-sm font-medium text-accent nums">
                    #{o.id}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-secondary">
                    {o.customer_email}
                  </span>
                  <span
                    className="rounded-full px-2 py-0.5 text-[10px] font-medium capitalize"
                    style={{
                      backgroundColor: `${meta.color}28`,
                      color: meta.color,
                    }}
                  >
                    {meta.label}
                  </span>
                  <span className="ml-2 w-24 text-right text-sm font-semibold nums text-ink-primary">
                    {formatPrice(o.total_amount, o.currency)}
                  </span>
                </Link>
              </motion.li>
            );
          })}
        </motion.ul>
      )}
    </Card>
  );
}

// ─── Low stock list ──────────────────────────────────────────────────────────

function LowStockList({ rows, loading }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader
        title="Low stock"
        action={
          <Link
            to="/admin/products"
            className="text-xs text-accent transition-opacity hover:opacity-70 focus-visible:focus-ring"
          >
            All products
          </Link>
        }
      />
      {loading ? (
        <div className="flex flex-col gap-1 p-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="px-5 py-8">
          <EmptyState
            icon={Package}
            size="sm"
            bordered={false}
            title="All well stocked"
          />
        </div>
      ) : (
        <motion.ul
          variants={listStagger(0.04)}
          initial="hidden"
          animate="show"
          className="divide-y divide-line-subtle"
        >
          {rows.map((p) => (
            <motion.li key={p.id} variants={fadeUp}>
              <Link
                to={`/admin/products/${p.id}/edit`}
                className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-fill focus-visible:focus-ring"
              >
                <span
                  className={cn(
                    'grid size-8 shrink-0 place-items-center rounded-sm',
                    p.stock === 0
                      ? 'bg-danger/12 text-danger'
                      : 'bg-warning/12 text-warning',
                  )}
                >
                  {p.stock === 0 ? (
                    <TriangleAlert className="size-4" aria-hidden="true" />
                  ) : (
                    <Package className="size-4" aria-hidden="true" />
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-ink-primary">
                  {p.name}
                  <span className="ml-1.5 text-[10px] text-ink-tertiary">
                    {p.sku}
                  </span>
                </span>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-[11px] font-semibold nums',
                    p.stock === 0
                      ? 'bg-danger/12 text-danger'
                      : 'bg-warning/12 text-warning',
                  )}
                >
                  {p.stock === 0 ? 'Out of stock' : `${p.stock} left`}
                </span>
              </Link>
            </motion.li>
          ))}
        </motion.ul>
      )}
    </Card>
  );
}

// ─── Top products list ───────────────────────────────────────────────────────

function TopProductsList({ rows, currency, loading }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Top selling products" />
      {loading ? (
        <div className="flex flex-col gap-1 p-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="px-5 py-8">
          <EmptyState
            icon={TrendingUp}
            size="sm"
            bordered={false}
            title="No sales yet"
            description="Top products appear after orders are placed."
          />
        </div>
      ) : (
        <motion.ul
          variants={listStagger(0.04)}
          initial="hidden"
          animate="show"
          className="divide-y divide-line-subtle"
        >
          {rows.map((p, idx) => (
            <motion.li key={p.product_id} variants={fadeUp}>
              <Link
                to={`/admin/products/${p.product_id}/edit`}
                className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-fill focus-visible:focus-ring"
              >
                {/* Rank badge */}
                <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent/12 text-[11px] font-semibold text-accent nums">
                  {idx + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink-primary">
                    {p.name}
                  </span>
                  <span className="block font-mono text-[10px] text-ink-tertiary">
                    {p.sku}
                  </span>
                </span>
                <span className="text-right">
                  <span className="block text-sm font-semibold nums text-ink-primary">
                    {formatPrice(p.revenue, currency)}
                  </span>
                  <span className="block text-[10px] nums text-ink-tertiary">
                    {p.units} unit{p.units === 1 ? '' : 's'}
                  </span>
                </span>
              </Link>
            </motion.li>
          ))}
        </motion.ul>
      )}
    </Card>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function AdminDashboardPage() {
  const [period, setPeriod] = useState('30d');
  const { data, isLoading, isError, refetch } = useDashboardOverview(period);

  const currency = data?.recent_orders?.[0]?.currency || 'INR';
  const summary = data?.summary;
  const fmtMoney = (v) => formatPrice(v, currency);
  const fmtInt = (v) => Number(v || 0).toLocaleString();

  return (
    <AdminPage
      title="Dashboard"
      description="Live view of your store. Auto-refreshes every minute."
      action={
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          aria-label="Select period"
          className="h-9 rounded-sm border border-line-subtle bg-bg-elevated px-3 text-sm text-ink-primary transition-colors hover:border-line-strong focus-visible:focus-ring"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      }
    >
      {/* Error banner */}
      {isError && (
        <motion.div variants={fadeUp}>
          <Card className="border-danger/30 bg-danger/8 p-4 text-sm text-danger">
            Couldn&apos;t load dashboard data.{' '}
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

      {/* KPI row */}
      <motion.div
        variants={fadeUp}
        className="grid grid-cols-2 gap-4 lg:grid-cols-4"
      >
        <KPICard
          label="Revenue"
          icon={CircleDollarSign}
          value={summary?.revenue?.current}
          previousValue={summary?.revenue?.previous}
          deltaPct={summary?.revenue?.delta_pct}
          format={fmtMoney}
          tone="success"
          loading={isLoading}
        />
        <KPICard
          label="Orders"
          icon={ShoppingBag}
          value={summary?.orders?.current}
          previousValue={summary?.orders?.previous}
          deltaPct={summary?.orders?.delta_pct}
          format={fmtInt}
          tone="info"
          loading={isLoading}
        />
        <KPICard
          label="New customers"
          icon={Users}
          value={summary?.new_customers?.current}
          previousValue={summary?.new_customers?.previous}
          deltaPct={summary?.new_customers?.delta_pct}
          format={fmtInt}
          tone="accent"
          loading={isLoading}
        />
        <KPICard
          label="Average order"
          icon={Receipt}
          value={summary?.aov?.current}
          previousValue={summary?.aov?.previous}
          deltaPct={summary?.aov?.delta_pct}
          format={fmtMoney}
          tone="warning"
          loading={isLoading}
        />
      </motion.div>

      {/* Charts row */}
      <motion.div
        variants={fadeUp}
        className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]"
      >
        <RevenueChart
          series={data?.revenue_series || []}
          currency={currency}
          loading={isLoading}
        />
        <StatusDonut data={data?.orders_by_status} loading={isLoading} />
      </motion.div>

      {/* Lists row */}
      <motion.div
        variants={fadeUp}
        className="grid gap-4 lg:grid-cols-3"
      >
        <RecentOrdersList rows={data?.recent_orders || []} loading={isLoading} />
        <TopProductsList
          rows={data?.top_products || []}
          currency={currency}
          loading={isLoading}
        />
        <LowStockList rows={data?.low_stock || []} loading={isLoading} />
      </motion.div>

      {/* Footnote */}
      <motion.p
        variants={fadeUp}
        className="flex items-center gap-1.5 text-xs text-ink-tertiary"
      >
        <Sparkles className="size-3" aria-hidden="true" />
        Revenue counts paid, shipped, and delivered orders. Refunded and
        cancelled orders are excluded.
      </motion.p>
    </AdminPage>
  );
}
