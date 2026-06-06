import { useState } from 'react';
import { Link } from 'react-router-dom';
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
import { Card } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useDashboardOverview } from '@/features/dashboard/hooks.js';

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

// ---- Revenue chart ----

function RevenueChart({ series, currency, loading }) {
  if (loading) {
    return <Skeleton className="h-72 rounded-lg" />;
  }
  const hasData = series.some((p) => p.revenue > 0);
  if (!hasData) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={TrendingUp}
          title="No revenue in this period"
          description="Once an order moves to PAID it'll show up on this chart."
        />
      </Card>
    );
  }
  return (
    <Card className="p-5">
      <p className="text-sm font-medium text-ink-primary">Revenue over time</p>
      <div className="mt-2 h-72">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 10, right: 12, left: -12, bottom: 0 }}>
            <defs>
              <linearGradient id="revFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#22c55e" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: 'currentColor' }}
              stroke="currentColor"
              tickFormatter={(d) =>
                new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
              }
              className="text-ink-tertiary"
              minTickGap={32}
            />
            <YAxis
              tick={{ fontSize: 11, fill: 'currentColor' }}
              stroke="currentColor"
              className="text-ink-tertiary"
              tickFormatter={(v) =>
                v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
              }
            />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--line-subtle)',
                borderRadius: 4,
                fontSize: 12,
              }}
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
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

// ---- Order status donut ----

function StatusDonut({ data, loading }) {
  if (loading) return <Skeleton className="h-72 rounded-lg" />;
  const rows = Object.entries(data || {}).map(([status, count]) => ({
    name: STATUS_META[status]?.label || status,
    value: count,
    color: STATUS_META[status]?.color || '#9ca3af',
    raw: status,
  }));
  const total = rows.reduce((a, r) => a + r.value, 0);
  if (total === 0) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={ShoppingBag}
          title="No orders yet"
          description="Status breakdown will appear here once customers buy."
        />
      </Card>
    );
  }
  return (
    <Card className="p-5">
      <p className="text-sm font-medium text-ink-primary">Orders by status</p>
      <div className="mt-2 grid items-center gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
        <div className="h-44">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={rows}
                dataKey="value"
                nameKey="name"
                innerRadius={42}
                outerRadius={68}
                strokeWidth={2}
                stroke="var(--bg-elevated)"
              >
                {rows.map((r) => (
                  <Cell key={r.name} fill={r.color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--line-subtle)',
                  borderRadius: 4,
                  fontSize: 12,
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
                  className="inline-block size-3 shrink-0 rounded-sm"
                  style={{ backgroundColor: r.color }}
                  aria-hidden="true"
                />
                <span className="flex-1 capitalize text-ink-secondary">{r.name}</span>
                <span className="tabular-nums text-ink-primary">
                  {r.value}
                  <span className="ml-1 text-ink-tertiary">({pct}%)</span>
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}

// ---- Lists ----

function RecentOrdersList({ rows, loading }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
        <p className="text-sm font-medium text-ink-primary">Recent orders</p>
        <Link
          to="/admin/orders"
          className="text-xs text-accent hover:underline focus-visible:focus-ring"
        >
          View all
        </Link>
      </div>
      {loading ? (
        <div className="flex flex-col gap-1 p-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-ink-tertiary">
          No orders yet.
        </p>
      ) : (
        <ul className="divide-y divide-line-subtle">
          {rows.map((o) => {
            const meta = STATUS_META[o.status] || { color: '#9ca3af', label: o.status };
            return (
              <li key={o.id}>
                <Link
                  to={`/admin/orders/${o.id}`}
                  className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-fill focus-visible:focus-ring"
                >
                  <span className="font-mono text-sm font-medium text-accent">
                    #{o.id}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-secondary">
                    {o.customer_email}
                  </span>
                  <span
                    className="rounded-full px-2 py-0.5 text-[10px] font-medium capitalize"
                    style={{ backgroundColor: `${meta.color}30`, color: meta.color }}
                  >
                    {meta.label}
                  </span>
                  <span className="ml-2 w-24 text-right text-sm font-semibold tabular-nums text-ink-primary">
                    {formatPrice(o.total_amount, o.currency)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function LowStockList({ rows, loading }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
        <p className="text-sm font-medium text-ink-primary">Low stock</p>
        <Link
          to="/admin/products"
          className="text-xs text-accent hover:underline focus-visible:focus-ring"
        >
          All products
        </Link>
      </div>
      {loading ? (
        <div className="flex flex-col gap-1 p-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-ink-tertiary">
          Everything is well stocked.
        </p>
      ) : (
        <ul className="divide-y divide-line-subtle">
          {rows.map((p) => (
            <li key={p.id}>
              <Link
                to={`/admin/products/${p.id}/edit`}
                className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-fill focus-visible:focus-ring"
              >
                <span
                  className={cn(
                    'grid size-8 shrink-0 place-items-center rounded-sm',
                    p.stock === 0
                      ? 'bg-danger/15 text-danger'
                      : 'bg-warning/15 text-warning',
                  )}
                >
                  {p.stock === 0 ? (
                    <TriangleAlert className="size-4" />
                  ) : (
                    <Package className="size-4" />
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-ink-primary">
                  {p.name}
                  <span className="ml-1 text-[10px] text-ink-tertiary">{p.sku}</span>
                </span>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums',
                    p.stock === 0
                      ? 'bg-danger/15 text-danger'
                      : 'bg-warning/15 text-warning',
                  )}
                >
                  {p.stock === 0 ? 'Out of stock' : `${p.stock} left`}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function TopProductsList({ rows, currency, loading }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
        <p className="text-sm font-medium text-ink-primary">Top selling products</p>
      </div>
      {loading ? (
        <div className="flex flex-col gap-1 p-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-ink-tertiary">
          No sales yet in this period.
        </p>
      ) : (
        <ul className="divide-y divide-line-subtle">
          {rows.map((p, idx) => (
            <li key={p.product_id}>
              <Link
                to={`/admin/products/${p.product_id}/edit`}
                className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-fill focus-visible:focus-ring"
              >
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
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
                  <span className="block text-sm font-semibold tabular-nums text-ink-primary">
                    {formatPrice(p.revenue, currency)}
                  </span>
                  <span className="block text-[10px] tabular-nums text-ink-tertiary">
                    {p.units} unit{p.units === 1 ? '' : 's'}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---- Page ----

export default function AdminDashboardPage() {
  const [period, setPeriod] = useState('30d');
  const { data, isLoading, isError, refetch } = useDashboardOverview(period);
  // KPI cards format money — pick a currency once. Real orders' currency comes
  // from the recent_orders list; falls back to INR on a fresh store.
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
          className="h-9 rounded-sm border border-line-subtle bg-bg-elevated px-3 text-sm text-ink-primary focus-visible:focus-ring"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      }
    >
      {isError && (
        <Card className="mb-4 border-danger/30 bg-danger/10 p-4 text-sm text-danger">
          Couldn&apos;t load dashboard data.{' '}
          <button
            type="button"
            onClick={() => refetch()}
            className="ml-1 underline hover:text-ink-primary"
          >
            Try again
          </button>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
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
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <RevenueChart
          series={data?.revenue_series || []}
          currency={currency}
          loading={isLoading}
        />
        <StatusDonut data={data?.orders_by_status} loading={isLoading} />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <RecentOrdersList rows={data?.recent_orders || []} loading={isLoading} />
        <TopProductsList
          rows={data?.top_products || []}
          currency={currency}
          loading={isLoading}
        />
        <LowStockList rows={data?.low_stock || []} loading={isLoading} />
      </div>

      <p className="mt-6 flex items-center gap-1.5 text-xs text-ink-tertiary">
        <Sparkles className="size-3" />
        Revenue counts paid, shipped, and delivered orders. Refunded and cancelled
        orders are excluded.
      </p>
    </AdminPage>
  );
}
