import { ArrowDownRight, ArrowUpRight } from 'lucide-react';
import { Card } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';

/**
 * KPI summary card used in admin analytics pages.
 *
 * Props:
 *  label        — metric display name
 *  icon         — lucide icon component
 *  value        — current period value
 *  previousValue — prior period value (shown in delta tooltip)
 *  deltaPct     — percentage change (null → "—", negative → down arrow)
 *  format       — (v) => string  — fmtMoney or fmtInt
 *  tone         — 'accent' | 'success' | 'info' | 'warning'
 *  loading      — boolean skeleton state
 */
export function KPICard({ label, icon: Icon, value, previousValue, deltaPct, format, tone = 'accent', loading }) {
  const positiveIsGood = true;
  const hasDelta = deltaPct != null && Number.isFinite(deltaPct);
  const isUp = hasDelta && deltaPct >= 0;
  const isGood = hasDelta && (positiveIsGood ? isUp : !isUp);

  const TONE = {
    accent: 'bg-accent/15 text-accent',
    success: 'bg-success/15 text-success',
    info: 'bg-blue-500/15 text-blue-400',
    warning: 'bg-warning/15 text-warning',
  }[tone] || 'bg-accent/15 text-accent';

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <span className={cn('grid size-10 shrink-0 place-items-center rounded-sm', TONE)}>
          <Icon className="size-5" aria-hidden="true" />
        </span>
        {hasDelta && (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums',
              isGood ? 'bg-success/15 text-success' : 'bg-danger/15 text-danger',
            )}
            title={`Previous: ${format(previousValue)}`}
          >
            {isUp ? (
              <ArrowUpRight className="size-3" />
            ) : (
              <ArrowDownRight className="size-3" />
            )}
            {Math.abs(deltaPct).toFixed(1)}%
          </span>
        )}
      </div>
      <p className="mt-4 text-h2 text-ink-primary tabular-nums">
        {loading ? <Skeleton className="inline-block h-8 w-24" /> : format(value)}
      </p>
      <p className="mt-0.5 text-sm text-ink-secondary">{label}</p>
      {!loading && !hasDelta && Number(previousValue) === 0 && (
        <p className="mt-1 text-[11px] text-ink-tertiary">No prior period data</p>
      )}
    </Card>
  );
}
