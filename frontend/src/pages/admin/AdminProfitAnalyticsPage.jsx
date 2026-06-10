import { useState } from 'react';
import { motion } from 'framer-motion';
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
  CartesianGrid,
  Cell,
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
import { useProfitAnalytics } from '@/features/analytics/hooks.js';
import { fadeUp } from '@/lib/motion.js';

// ─── Constants ───────────────────────────────────────────────────────────────

const PERIODS = [
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '90d', label: '90d' },
];

const CAT_COLORS = [
  '#22c55e',
  '#6366f1',
  '#f59e0b',
  '#818cf8',
  '#f87171',
  '#34d399',
  '#fb923c',
];

const TOOLTIP_STYLE = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--line-subtle)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--ink-primary)',
  boxShadow: 'var(--shadow-md)',
};

const AXIS_TICK = { fontSize: 11, fill: 'currentColor' };

// ─── Segmented control (identical API to Sales page) ────────────────────────

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

// ─── Data-quality banner ──────────────────────────────────────────────────────

function CoverageBanner({ coveragePct }) {
  if (coveragePct == null || coveragePct >= 100) return null;

  return (
    <motion.div variants={fadeUp}>
      <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/8 px-4 py-3 text-sm text-warning">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <span>
          Profit figures use cost data for{' '}
          <strong className="font-semibold">
            {Number(coveragePct).toFixed(1)}%
          </strong>{' '}
          of items sold. Add a cost price to your products for accurate numbers.
        </span>
      </div>
    </motion.div>
  );
}

// ─── Contribution Waterfall ───────────────────────────────────────────────────

function WaterfallChart({ waterfall, revenue, loading }) {
  if (loading) return <Skeleton className="h-64 w-full rounded-lg" />;

  const isEmpty = !Array.isArray(waterfall) || waterfall.length === 0;

  return (
    <Card>
      <CardHeader title="Contribution waterfall" />
      <div className="p-5 pt-4">
        {isEmpty ? (
          <EmptyState
            icon={TrendingUp}
            size="sm"
            bordered={false}
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

              const barBg = isCost
                ? 'bg-danger/25'
                : isResult
                  ? step.amount >= 0
                    ? 'bg-success/35'
                    : 'bg-danger/25'
                  : isSubtotal
                    ? 'bg-accent/25'
                    : 'bg-success/18';

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
                      'w-44 shrink-0 text-sm',
                      isSubtotal || isResult
                        ? 'font-semibold text-ink-primary'
                        : 'text-ink-secondary',
                      isStart && 'font-medium text-ink-primary',
                    )}
                  >
                    {step.label}
                  </span>

                  {/* Bar track */}
                  <div className="flex-1">
                    {isSubtotal || isResult || isStart ? (
                      <div className="h-px bg-line-subtle" />
                    ) : (
                      <div className="h-2 overflow-hidden rounded-full bg-fill-strong">
                        <div
                          className={cn('h-full rounded-full transition-all duration-500', barBg)}
                          style={{ width: `${barWidthPct}%` }}
                        />
                      </div>
                    )}
                  </div>

                  {/* Amount */}
                  <span
                    className={cn(
                      'w-28 shrink-0 text-right font-mono text-sm nums',
                      amountColor,
                      (isSubtotal || isResult) && 'text-base font-semibold',
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
      </div>
    </Card>
  );
}

// ─── Margin by product table ──────────────────────────────────────────────────

function MarginByProductTable({ data, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card className="overflow-hidden">
      <CardHeader title="Margin by product" />
      <div className="p-5 pt-4">
        {isEmpty ? (
          <EmptyState
            icon={Tag}
            size="sm"
            bordered={false}
            title="No product data"
            description="Product margin breakdown appears once cost prices are set."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line-subtle text-left">
                  <th className="pb-2.5 text-xs font-medium text-ink-tertiary">Product</th>
                  <th className="pb-2.5 text-right text-xs font-medium text-ink-tertiary">Units</th>
                  <th className="pb-2.5 text-right text-xs font-medium text-ink-tertiary">Revenue</th>
                  <th className="pb-2.5 text-right text-xs font-medium text-ink-tertiary">Cost</th>
                  <th className="pb-2.5 text-right text-xs font-medium text-ink-tertiary">Gross profit</th>
                  <th className="pb-2.5 text-right text-xs font-medium text-ink-tertiary">Margin</th>
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
                          ? 'bg-danger/5 hover:bg-danger/8'
                          : 'hover:bg-fill',
                      )}
                    >
                      <td className="py-2.5 pr-3">
                        <p className="font-medium text-ink-primary">{row.name}</p>
                        <p className="text-[11px] text-ink-tertiary">{row.sku}</p>
                      </td>
                      <td className="py-2.5 text-right nums text-ink-secondary">
                        {row.units}
                      </td>
                      <td className="py-2.5 text-right nums text-ink-secondary">
                        {formatPrice(row.revenue, 'INR')}
                      </td>
                      <td className="py-2.5 text-right nums text-ink-secondary">
                        {formatPrice(row.cost, 'INR')}
                      </td>
                      <td
                        className={cn(
                          'py-2.5 text-right nums font-medium',
                          isNegative ? 'text-danger' : 'text-ink-primary',
                        )}
                      >
                        {formatPrice(row.gross_profit, 'INR')}
                      </td>
                      <td
                        className={cn(
                          'py-2.5 text-right nums font-semibold',
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
      </div>
    </Card>
  );
}

// ─── Margin by category chart ─────────────────────────────────────────────────

function MarginByCategoryChart({ data, loading }) {
  if (loading) return <Skeleton className="h-72 w-full rounded-lg" />;

  const isEmpty = !Array.isArray(data) || data.length === 0;

  return (
    <Card>
      <CardHeader title="Gross profit by category" />
      <div className="p-5 pt-4">
        {isEmpty ? (
          <EmptyState
            icon={Tag}
            size="sm"
            bordered={false}
            title="No category data"
            description="Category margin breakdown appears once orders are fulfilled."
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
                  formatter={(value, _name, props) => {
                    const pct = props.payload?.margin_pct;
                    return [
                      `${formatPrice(value, 'INR')}${pct != null ? ` (${Number(pct).toFixed(1)}%)` : ''}`,
                      'Gross profit',
                    ];
                  }}
                />
                <Bar dataKey="gross_profit" radius={[0, 4, 4, 0]} maxBarSize={18}>
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
      </div>
    </Card>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

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
      {/* Error banner */}
      {isError && (
        <motion.div variants={fadeUp}>
          <Card className="border-danger/30 bg-danger/8 p-4 text-sm text-danger">
            Couldn&apos;t load profitability data.{' '}
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

      {/* Data-quality coverage warning */}
      {!isLoading && (
        <CoverageBanner coveragePct={data?.cost_coverage_pct ?? null} />
      )}

      {/* Empty-period info note */}
      {!isLoading && !isError && data && data.order_count === 0 && (
        <motion.div variants={fadeUp}>
          <div className="flex items-start gap-3 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 text-sm text-ink-secondary">
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>
              No orders in this period — profitability data will appear once
              orders are placed.
            </span>
          </div>
        </motion.div>
      )}

      {/* KPI row */}
      <motion.div
        variants={fadeUp}
        className="grid grid-cols-2 gap-4 lg:grid-cols-4"
      >
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
      </motion.div>

      {/* Contribution waterfall (full-width) */}
      <motion.div variants={fadeUp}>
        <WaterfallChart
          waterfall={data?.waterfall}
          revenue={data?.revenue ?? 1}
          loading={isLoading}
        />
      </motion.div>

      {/* Product table + category chart side by side */}
      <motion.div
        variants={fadeUp}
        className="grid gap-4 lg:grid-cols-2"
      >
        <MarginByProductTable
          data={data?.margin_by_product}
          loading={isLoading}
        />
        <MarginByCategoryChart
          data={data?.margin_by_category}
          loading={isLoading}
        />
      </motion.div>
    </AdminPage>
  );
}
