import { motion, useReducedMotion } from 'framer-motion';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { fadeUp, hoverLift, tapPress, duration, ease } from '@/lib/motion.js';

const TONES = {
  accent:  'bg-accent/12 text-accent',
  success: 'bg-success/12 text-success',
  warning: 'bg-warning/12 text-warning',
  danger:  'bg-danger/12 text-danger',
  info:    'bg-info/12 text-info',
};

/**
 * Dashboard metric tile — icon, value, label, optional trend.
 *
 * Props (unchanged from before — additive additions only):
 *   icon     — lucide icon component
 *   label    — metric name
 *   value    — formatted display value (string)
 *   tone     — 'accent' | 'success' | 'warning' | 'danger' | 'info'
 *   loading  — boolean skeleton state
 *   trend    — optional number (percent change); positive = up arrow
 *   onClick  — if provided, card becomes interactive (hover-lift + cursor-pointer)
 */
export function StatCard({
  icon: Icon,
  label,
  value,
  tone = 'accent',
  loading = false,
  trend,
  onClick,
}) {
  const reduce = useReducedMotion();
  const isClickable = typeof onClick === 'function';

  const hasTrend = trend != null && Number.isFinite(trend);
  const isUp = hasTrend && trend >= 0;

  const card = (
    <div
      onClick={onClick}
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={isClickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') onClick(); } : undefined}
      className={cn(
        'rounded-lg border border-line-subtle bg-bg-elevated p-5 shadow-md',
        'transition-colors duration-150',
        isClickable && 'cursor-pointer focus-visible:focus-ring',
      )}
    >
      {/* Icon + trend badge row */}
      <div className="flex items-start justify-between gap-2">
        <span
          className={cn(
            'grid size-10 place-items-center rounded-sm',
            TONES[tone] || TONES.accent,
          )}
        >
          <Icon className="size-5" aria-hidden="true" />
        </span>

        {hasTrend && (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[11px] font-medium',
              'nums',
              isUp
                ? 'bg-success/12 text-success'
                : 'bg-danger/12 text-danger',
            )}
            aria-label={`${isUp ? 'Up' : 'Down'} ${Math.abs(trend).toFixed(1)}%`}
          >
            {isUp ? (
              <ArrowUpRight className="size-3" aria-hidden="true" />
            ) : (
              <ArrowDownRight className="size-3" aria-hidden="true" />
            )}
            {Math.abs(trend).toFixed(1)}%
          </span>
        )}
      </div>

      {/* Value */}
      <p className="mt-4 text-h2 leading-none text-ink-primary nums">
        {loading ? <Skeleton className="inline-block h-8 w-24" /> : (value ?? '—')}
      </p>

      {/* Label */}
      <p className="mt-1.5 text-sm text-ink-secondary">{label}</p>
    </div>
  );

  // Wrap in motion.div for lift/press when interactive and motion not reduced.
  if (isClickable && !reduce) {
    return (
      <motion.div
        variants={fadeUp}
        whileHover={hoverLift}
        whileTap={tapPress}
      >
        {card}
      </motion.div>
    );
  }

  return (
    <motion.div variants={fadeUp}>
      {card}
    </motion.div>
  );
}
