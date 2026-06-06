import { useState } from 'react';
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
} from 'recharts';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { KPICard } from '@/components/admin/KPICard.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useSalesAnalytics } from '@/features/analytics/hooks.js';

// ---- Constants ----

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

// Category bar colours — cycle through a small palette.
const CAT_COLORS = [
  '#22c55e',
  '#60a5fa',
  '#f59e0b',
  '#a78bfa',
  '#f87171',
  '#34d399',
  '#fb923c',
];

// ---- Segmented control ----

function SegmentedControl({ options, value, onChange, size = 'sm' }) {
  return (
    <div className="inline-flex rounded-sm border border-line-subtle bg-bg-elevated">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            'px-3 py-1.5 text-xs font-medium transition-colors first:rounded-l-sm last:rounded-r-sm focus-visible:focus-ring',
            value === opt.value
              ? 'bg-accent text-ink-inverse'
              : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ---- Revenue over time chart ----

function RevenueSeriesChart({ series, granularity, onGranularityChange, loading }) {
  if (loading) {
    return <Skeleton className="h-80 rounded-lg" />;
  }

  const hasData = Array.isArray(series) && series.some((p) => p.revenue > 0);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium text-ink-primary">Revenue over time</p>
        <SegmentedControl
          options={GRANULARITIES}
          value={granularity}
          onChange={onGranularityChange}
        />
      </div>
      {!hasData ? (
        <div className="mt-4">
          <EmptyState
            icon={TrendingUp}
            title="No revenue in this period"
            description="Once an order moves to PAID it will appear on this chart."
          />
        </div>
      ) : (
        <div className="mt-4 h-72">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series} margin={{ top: 10, right: 12, left: -12, bottom: 0 }}>
              <defs>
                <linearGradient id="salesRevFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22c55e" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: 'currentColor' }}
                stroke="currentColor"
                tickFormatter={(d) =>
                  new Date(d).toLocaleDateString(undefined, {
                    month: 'short',
                    day: 'numeric',
                  })
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
              />
              <Area
                type="monotone"
                dataKey="orders"
                stroke="#60a5fa"
                strokeWidth={1.5}
                fill="none"
                strokeDasharray="4 2"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

// ---- Revenue by category (horizontal bar chart) ----

function CategoryChart({ data, loading }) {
  if (loading) return <Skeleton className="h-72 rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card className="p-5">
      <p className="text-sm font-medium text-ink-primary">Revenue by category</p>
      {isEmpty ? (
        <div className="mt-4">
          <EmptyState
            icon={Tag}
            title="No category data"
            description="Category breakdown appears once orders are fulfilled."
          />
        </div>
      ) : (
        <div className="mt-4 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={data}
              layout="vertical"
              margin={{ top: 0, right: 12, left: 8, bottom: 0 }}
            >
              <XAxis
                type="number"
                tick={{ fontSize: 10, fill: 'currentColor' }}
                stroke="currentColor"
                className="text-ink-tertiary"
                tickFormatter={(v) =>
                  v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)
                }
              />
              <YAxis
                type="category"
                dataKey="category"
                width={90}
                tick={{ fontSize: 10, fill: 'currentColor' }}
                stroke="currentColor"
                className="text-ink-tertiary"
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--line-subtle)',
                  borderRadius: 4,
                  fontSize: 12,
                }}
                formatter={(value, name, props) => {
                  const pct = props.payload?.pct;
                  return [
                    `${formatPrice(value, 'INR')}${pct != null ? ` (${pct.toFixed(1)}%)` : ''}`,
                    'Revenue',
                  ];
                }}
              />
              <Bar dataKey="revenue" radius={[0, 3, 3, 0]}>
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
    </Card>
  );
}

// ---- Sales by day of week (vertical bar chart) ----

function DayOfWeekChart({ data, loading }) {
  if (loading) return <Skeleton className="h-72 rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card className="p-5">
      <p className="text-sm font-medium text-ink-primary">Sales by day of week</p>
      {isEmpty ? (
        <div className="mt-4">
          <EmptyState
            icon={TrendingUp}
            title="No data yet"
            description="Day-of-week patterns appear after orders are recorded."
          />
        </div>
      ) : (
        <div className="mt-4 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={data}
              margin={{ top: 10, right: 12, left: -12, bottom: 0 }}
            >
              <XAxis
                dataKey="dow"
                tick={{ fontSize: 11, fill: 'currentColor' }}
                stroke="currentColor"
                className="text-ink-tertiary"
              />
              <YAxis
                tick={{ fontSize: 11, fill: 'currentColor' }}
                stroke="currentColor"
                className="text-ink-tertiary"
                tickFormatter={(v) =>
                  v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)
                }
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--line-subtle)',
                  borderRadius: 4,
                  fontSize: 12,
                }}
                formatter={(value, name) => {
                  if (name === 'revenue') return [formatPrice(value, 'INR'), 'Revenue'];
                  if (name === 'orders') return [value, 'Orders'];
                  return [value, name];
                }}
              />
              <Bar dataKey="revenue" fill="#22c55e" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

// ---- Page ----

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
      {isError && (
        <Card className="mb-4 border-danger/30 bg-danger/10 p-4 text-sm text-danger">
          Couldn&apos;t load analytics data.{' '}
          <button
            type="button"
            onClick={() => refetch()}
            className="ml-1 underline hover:text-ink-primary"
          >
            Try again
          </button>
        </Card>
      )}

      {/* KPI cards */}
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
      </div>

      {/* Revenue over time (full-width) */}
      <div className="mt-6">
        <RevenueSeriesChart
          series={data?.series || []}
          granularity={granularity}
          onGranularityChange={setGranularity}
          loading={isLoading}
        />
      </div>

      {/* Category + day-of-week side by side */}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <CategoryChart data={data?.by_category || []} loading={isLoading} />
        <DayOfWeekChart data={data?.by_day_of_week || []} loading={isLoading} />
      </div>
    </AdminPage>
  );
}
