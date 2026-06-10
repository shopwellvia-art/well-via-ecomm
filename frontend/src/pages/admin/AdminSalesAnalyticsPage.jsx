import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  CircleDollarSign,
  ShoppingBag,
  Receipt,
  Tag,
  TrendingUp,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { KPICard } from '@/components/admin/KPICard.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useSalesAnalytics } from '@/features/analytics/hooks.js';
import { fadeUp, listStagger } from '@/lib/motion.js';

// ─── Constants ───────────────────────────────────────────────────────────────

const PERIODS = [
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '90d', label: '90d' },
];

const GRANULARITIES = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
];

// Token-aligned palette — accent gradient midpoints + status tones
const CAT_COLORS = [
  '#22c55e', // success
  '#6366f1', // accent
  '#f59e0b', // warning
  '#818cf8', // accent-mid
  '#f87171', // danger-ish
  '#34d399', // success lighter
  '#fb923c', // orange
];

// Shared recharts tooltip style using CSS variable tokens
const TOOLTIP_STYLE = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--line-subtle)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--ink-primary)',
  boxShadow: 'var(--shadow-md)',
};

const AXIS_TICK = { fontSize: 11, fill: 'currentColor' };

// ─── Segmented control ────────────────────────────────────────────────────────

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

// ─── Revenue over time chart ──────────────────────────────────────────────────

function RevenueSeriesChart({ series, granularity, onGranularityChange, loading }) {
  if (loading) {
    return <Skeleton className="h-80 w-full rounded-lg" />;
  }

  const hasData = Array.isArray(series) && series.some((p) => p.revenue > 0);

  return (
    <Card>
      <CardHeader
        title="Revenue over time"
        action={
          <SegmentedControl
            options={GRANULARITIES}
            value={granularity}
            onChange={onGranularityChange}
          />
        }
      />
      <div className="p-5 pt-4">
        {!hasData ? (
          <EmptyState
            icon={TrendingUp}
            size="sm"
            bordered={false}
            title="No revenue in this period"
            description="Once an order moves to PAID it will appear on this chart."
          />
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="salesRevFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#22c55e" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="salesOrdFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#6366f1" stopOpacity={0.14} />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--grid-line)"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tick={AXIS_TICK}
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
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                  tickFormatter={(v) =>
                    v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
                  }
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: 'var(--ink-tertiary)', marginBottom: 4 }}
                  labelFormatter={(d) =>
                    new Date(d).toLocaleDateString(undefined, {
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                    })
                  }
                  formatter={(value, name) => {
                    if (name === 'revenue') return [formatPrice(value, 'INR'), 'Revenue'];
                    if (name === 'orders') return [value, 'Orders'];
                    return [value, name];
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="revenue"
                  stroke="#22c55e"
                  strokeWidth={2}
                  fill="url(#salesRevFill)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#22c55e', strokeWidth: 0 }}
                />
                <Area
                  type="monotone"
                  dataKey="orders"
                  stroke="#6366f1"
                  strokeWidth={1.5}
                  fill="url(#salesOrdFill)"
                  strokeDasharray="4 3"
                  dot={false}
                  activeDot={{ r: 3, fill: '#6366f1', strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

// ─── Revenue by category (horizontal bar chart) ────────────────────────────

function CategoryChart({ data, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card>
      <CardHeader title="Revenue by category" />
      <div className="p-5 pt-4">
        {isEmpty ? (
          <EmptyState
            icon={Tag}
            size="sm"
            bordered={false}
            title="No category data"
            description="Category breakdown appears once orders are fulfilled."
          />
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
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
                  tickFormatter={(v) =>
                    v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)
                  }
                />
                <YAxis
                  type="category"
                  dataKey="category"
                  width={88}
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: 'var(--ink-tertiary)', marginBottom: 4 }}
                  formatter={(value, name, props) => {
                    const pct = props.payload?.pct;
                    return [
                      `${formatPrice(value, 'INR')}${pct != null ? ` (${pct.toFixed(1)}%)` : ''}`,
                      'Revenue',
                    ];
                  }}
                />
                <Bar dataKey="revenue" radius={[0, 4, 4, 0]} maxBarSize={18}>
                  {data.map((entry, index) => (
                    <Cell
                      key={entry.category}
                      fill={CAT_COLORS[index % CAT_COLORS.length]}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

// ─── Sales by day of week (vertical bar chart) ────────────────────────────

function DayOfWeekChart({ data, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card>
      <CardHeader title="Sales by day of week" />
      <div className="p-5 pt-4">
        {isEmpty ? (
          <EmptyState
            icon={TrendingUp}
            size="sm"
            bordered={false}
            title="No data yet"
            description="Day-of-week patterns appear after orders are recorded."
          />
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                margin={{ top: 8, right: 8, left: -20, bottom: 0 }}
                barCategoryGap="30%"
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--grid-line)"
                  vertical={false}
                />
                <XAxis
                  dataKey="dow"
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                />
                <YAxis
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  className="text-ink-tertiary"
                  tickFormatter={(v) =>
                    v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)
                  }
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: 'var(--ink-tertiary)', marginBottom: 4 }}
                  formatter={(value, name) => {
                    if (name === 'revenue') return [formatPrice(value, 'INR'), 'Revenue'];
                    if (name === 'orders') return [value, 'Orders'];
                    return [value, name];
                  }}
                />
                <Bar
                  dataKey="revenue"
                  fill="#6366f1"
                  radius={[4, 4, 0, 0]}
                  maxBarSize={32}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function AdminSalesAnalyticsPage() {
  const [period, setPeriod] = useState('30d');
  const [granularity, setGranularity] = useState('day');

  const { data, isLoading, isError, refetch } = useSalesAnalytics({ period, granularity });

  const summary = data?.summary;
  const fmtMoney = (v) => formatPrice(v, 'INR');
  const fmtInt = (v) => Number(v || 0).toLocaleString();

  return (
    <AdminPage
      title="Sales & Revenue"
      description="Track revenue, orders, and trends across your store."
      action={
        <SegmentedControl
          options={PERIODS}
          value={period}
          onChange={setPeriod}
        />
      }
    >
      {/* Error banner */}
      {isError && (
        <motion.div variants={fadeUp}>
          <Card className="border-danger/30 bg-danger/8 p-4 text-sm text-danger">
            Couldn&apos;t load analytics data.{' '}
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
          label="Avg. Order Value"
          icon={Receipt}
          value={summary?.aov?.current}
          previousValue={summary?.aov?.previous}
          deltaPct={summary?.aov?.delta_pct}
          format={fmtMoney}
          tone="accent"
          loading={isLoading}
        />
        <KPICard
          label="Discounts"
          icon={Tag}
          value={summary?.discounts?.current}
          previousValue={summary?.discounts?.previous}
          deltaPct={summary?.discounts?.delta_pct}
          format={fmtMoney}
          tone="warning"
          loading={isLoading}
        />
      </motion.div>

      {/* Revenue over time (full-width) */}
      <motion.div variants={fadeUp}>
        <RevenueSeriesChart
          series={data?.series || []}
          granularity={granularity}
          onGranularityChange={setGranularity}
          loading={isLoading}
        />
      </motion.div>

      {/* Category + day-of-week side by side */}
      <motion.div
        variants={fadeUp}
        className="grid gap-4 lg:grid-cols-2"
      >
        <CategoryChart data={data?.by_category || []} loading={isLoading} />
        <DayOfWeekChart data={data?.by_day_of_week || []} loading={isLoading} />
      </motion.div>
    </AdminPage>
  );
}
