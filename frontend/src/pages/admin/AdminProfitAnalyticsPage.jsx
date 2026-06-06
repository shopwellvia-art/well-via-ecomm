import { useState } from 'react';
import {
  PiggyBank,
  TrendingUp,
  AlertTriangle,
  Info,
  Tag,
} from 'lucide-react';
import {
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
import { useProfitAnalytics } from '@/features/analytics/hooks.js';

// ---- Constants ----

const PERIODS = [
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '90d', label: '90d' },
];

const CAT_COLORS = [
  '#22c55e',
  '#60a5fa',
  '#f59e0b',
  '#a78bfa',
  '#f87171',
  '#34d399',
  '#fb923c',
];

// ---- Segmented control (mirrors AdminSalesAnalyticsPage) ----

function SegmentedControl({ options, value, onChange }) {
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

// ---- Data-quality banner ----

function CoverageBanner({ coveragePct }) {
  // null = no sales yet — show nothing
  if (coveragePct == null) return null;

  if (coveragePct >= 100) return null;

  return (
    <div className="mb-5 flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>
        Profit figures use cost data for{' '}
        <strong>{Number(coveragePct).toFixed(1)}%</strong> of items sold. Add a
        cost price to your products for accurate numbers.
      </span>
    </div>
  );
}

// ---- Contribution Waterfall ----

function WaterfallChart({ waterfall, revenue, loading }) {
  if (loading) return <Skeleton className="h-64 rounded-lg" />;

  const isEmpty = !Array.isArray(waterfall) || waterfall.length === 0;

  return (
    <Card className="p-5">
      <p className="mb-4 text-sm font-medium text-ink-primary">
        Contribution waterfall
      </p>
      {isEmpty ? (
        <EmptyState
          icon={TrendingUp}
          title="No data yet"
          description="Waterfall appears once orders with cost data are recorded."
        />
      ) : (
        <div className="flex flex-col divide-y divide-line-subtle">
          {waterfall.map((step) => {
            const isCost = step.kind === 'cost';
            const isSubtotal = step.kind === 'subtotal';
            const isResult = step.kind === 'result';
            const isStart = step.kind === 'start';

            const absAmount = Math.abs(step.amount);
            const maxBase = revenue > 0 ? revenue : 1;
            const barWidthPct = Math.min(100, (absAmount / maxBase) * 100);

            const amountColor = isCost
              ? 'text-danger'
              : isResult
                ? step.amount >= 0
                  ? 'text-success'
                  : 'text-danger'
                : isSubtotal
                  ? 'text-accent'
                  : 'text-ink-primary';

            const barColor = isCost
              ? 'bg-danger/30'
              : isResult
                ? step.amount >= 0
                  ? 'bg-success/40'
                  : 'bg-danger/30'
                : isSubtotal
                  ? 'bg-accent/30'
                  : 'bg-success/20';

            return (
              <div
                key={step.label}
                className={cn(
                  'flex items-center gap-3 py-2.5',
                  (isSubtotal || isResult) && 'py-3',
                )}
              >
                {/* Label */}
                <span
                  className={cn(
                    'w-40 shrink-0 text-sm',
                    isSubtotal || isResult
                      ? 'font-semibold text-ink-primary'
                      : 'text-ink-secondary',
                    isStart && 'font-medium text-ink-primary',
                  )}
                >
                  {step.label}
                </span>

                {/* Bar */}
                <div className="flex-1">
                  {(isSubtotal || isResult || isStart) ? (
                    <div className="h-px bg-line-subtle" />
                  ) : (
                    <div className="h-2 overflow-hidden rounded-full bg-fill">
                      <div
                        className={cn('h-full rounded-full', barColor)}
                        style={{ width: `${barWidthPct}%` }}
                      />
                    </div>
                  )}
                </div>

                {/* Amount */}
                <span
                  className={cn(
                    'w-28 shrink-0 text-right font-mono text-sm tabular-nums',
                    amountColor,
                    (isSubtotal || isResult) && 'font-semibold text-base',
                  )}
                >
                  {step.amount < 0
                    ? `−${formatPrice(Math.abs(step.amount), 'INR')}`
                    : formatPrice(step.amount, 'INR')}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

// ---- Margin by product table ----

function MarginByProductTable({ data, loading }) {
  if (loading) return <Skeleton className="h-72 rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card className="p-5">
      <p className="mb-4 text-sm font-medium text-ink-primary">
        Margin by product
      </p>
      {isEmpty ? (
        <EmptyState
          icon={Tag}
          title="No product data"
          description="Product margin breakdown appears once cost prices are set."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line-subtle text-left text-xs text-ink-tertiary">
                <th className="pb-2 font-medium">Product</th>
                <th className="pb-2 text-right font-medium">Units</th>
                <th className="pb-2 text-right font-medium">Revenue</th>
                <th className="pb-2 text-right font-medium">Cost</th>
                <th className="pb-2 text-right font-medium">Gross profit</th>
                <th className="pb-2 text-right font-medium">Margin %</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-subtle">
              {data.map((row) => {
                const isNegative =
                  row.margin_pct != null
                    ? row.margin_pct < 0
                    : row.gross_profit < 0;
                return (
                  <tr
                    key={row.product_id}
                    className={cn(
                      'transition-colors',
                      isNegative
                        ? 'bg-danger/5 hover:bg-danger/10'
                        : 'hover:bg-fill',
                    )}
                  >
                    <td className="py-2.5 pr-3">
                      <p className="font-medium text-ink-primary">{row.name}</p>
                      <p className="text-[11px] text-ink-tertiary">{row.sku}</p>
                    </td>
                    <td className="py-2.5 text-right tabular-nums text-ink-secondary">
                      {row.units}
                    </td>
                    <td className="py-2.5 text-right tabular-nums text-ink-secondary">
                      {formatPrice(row.revenue, 'INR')}
                    </td>
                    <td className="py-2.5 text-right tabular-nums text-ink-secondary">
                      {formatPrice(row.cost, 'INR')}
                    </td>
                    <td
                      className={cn(
                        'py-2.5 text-right tabular-nums font-medium',
                        isNegative ? 'text-danger' : 'text-ink-primary',
                      )}
                    >
                      {formatPrice(row.gross_profit, 'INR')}
                    </td>
                    <td
                      className={cn(
                        'py-2.5 text-right tabular-nums font-medium',
                        isNegative ? 'text-danger' : 'text-success',
                      )}
                    >
                      {row.margin_pct != null
                        ? `${Number(row.margin_pct).toFixed(1)}%`
                        : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ---- Margin by category chart ----

function MarginByCategoryChart({ data, loading }) {
  if (loading) return <Skeleton className="h-72 rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card className="p-5">
      <p className="mb-4 text-sm font-medium text-ink-primary">
        Gross profit by category
      </p>
      {isEmpty ? (
        <EmptyState
          icon={Tag}
          title="No category data"
          description="Category margin breakdown appears once orders are fulfilled."
        />
      ) : (
        <div className="h-64">
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
                formatter={(value, _name, props) => {
                  const pct = props.payload?.margin_pct;
                  return [
                    `${formatPrice(value, 'INR')}${pct != null ? ` (${Number(pct).toFixed(1)}%)` : ''}`,
                    'Gross profit',
                  ];
                }}
              />
              <Bar dataKey="gross_profit" radius={[0, 3, 3, 0]}>
                {data.map((entry, index) => (
                  <Cell
                    key={entry.category}
                    fill={
                      entry.gross_profit < 0
                        ? '#f87171'
                        : CAT_COLORS[index % CAT_COLORS.length]
                    }
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

// ---- Page ----

export default function AdminProfitAnalyticsPage() {
  const [period, setPeriod] = useState('30d');
  const { data, isLoading, isError, refetch } = useProfitAnalytics({ period });

  const fmtMoney = (v) => (v != null ? formatPrice(v, 'INR') : '—');
  const fmtPct = (v) => (v != null ? `${Number(v).toFixed(1)}%` : '—');

  const netMarginTone =
    data?.net_margin_pct == null
      ? 'accent'
      : data.net_margin_pct >= 0
        ? 'success'
        : 'warning';

  return (
    <AdminPage
      title="Profitability"
      description="Contribution margins and net profit across your store."
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
          Couldn&apos;t load profitability data.{' '}
          <button
            type="button"
            onClick={() => refetch()}
            className="ml-1 underline hover:text-ink-primary"
          >
            Try again
          </button>
        </Card>
      )}

      {/* Data-quality warning */}
      {!isLoading && (
        <CoverageBanner coveragePct={data?.cost_coverage_pct ?? null} />
      )}

      {/* Empty state — no sales at all */}
      {!isLoading && !isError && data && data.order_count === 0 && (
        <div className="mb-5 flex items-start gap-3 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 text-sm text-ink-secondary">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            No orders in this period — profitability data will appear once
            orders are placed.
          </span>
        </div>
      )}

      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KPICard
          label="Revenue"
          icon={TrendingUp}
          value={data?.revenue}
          previousValue={undefined}
          deltaPct={null}
          format={fmtMoney}
          tone="success"
          loading={isLoading}
        />
        <KPICard
          label="Contribution Margin 1"
          icon={PiggyBank}
          value={data?.c1}
          previousValue={undefined}
          deltaPct={null}
          format={fmtMoney}
          tone="accent"
          loading={isLoading}
        />
        <KPICard
          label="Net Profit"
          icon={PiggyBank}
          value={data?.net_profit}
          previousValue={undefined}
          deltaPct={null}
          format={fmtMoney}
          tone={
            data?.net_profit == null
              ? 'accent'
              : data.net_profit >= 0
                ? 'success'
                : 'warning'
          }
          loading={isLoading}
        />
        <KPICard
          label="Net Margin %"
          icon={TrendingUp}
          value={data?.net_margin_pct}
          previousValue={undefined}
          deltaPct={null}
          format={fmtPct}
          tone={netMarginTone}
          loading={isLoading}
        />
      </div>

      {/* Contribution waterfall (full-width) */}
      <div className="mt-6">
        <WaterfallChart
          waterfall={data?.waterfall}
          revenue={data?.revenue ?? 1}
          loading={isLoading}
        />
      </div>

      {/* Product table + category chart side by side */}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <MarginByProductTable
          data={data?.margin_by_product}
          loading={isLoading}
        />
        <MarginByCategoryChart
          data={data?.margin_by_category}
          loading={isLoading}
        />
      </div>
    </AdminPage>
  );
}
