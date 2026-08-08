import { Info, PlugZap, TriangleAlert } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { cn } from '@/lib/utils.js';
import { notConfigured } from './bespokeHelpers.js';

/**
 * Shared furniture for the ten bespoke views.
 *
 * Kept here rather than in each view so the ten look like one product, and so
 * the two notices that carry real meaning — "this could not run" and "read this
 * before you read the chart" — have exactly one implementation between them.
 */

/** A titled section inside a bespoke view. */
export function Panel({ title, description, action, children, className, bodyClassName }) {
  return (
    <Card className={cn('overflow-hidden', className)}>
      {(title || action) && <CardHeader title={title} action={action} />}
      <div className={cn('p-5', bodyClassName)}>
        {description && (
          <p className="mb-4 text-xs leading-relaxed text-ink-secondary">{description}</p>
        )}
        {children}
      </div>
    </Card>
  );
}

const PILL_TONES = {
  neutral: 'bg-fill text-ink-secondary border-line-subtle',
  success: 'bg-success-soft text-success border-success/30',
  warning: 'bg-warning-soft text-warning border-warning/30',
  danger: 'bg-danger-soft text-danger border-danger/30',
  info: 'bg-info-soft text-info border-info/30',
  /** Deliberately dashed: "we did not check", not "we checked and it passed". */
  unchecked: 'bg-transparent text-ink-tertiary border-dashed border-line-strong',
};

export function StatusPill({ tone = 'neutral', children, className, title }) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-xs border px-2 py-0.5',
        'text-[11px] font-medium leading-tight whitespace-nowrap',
        PILL_TONES[tone] ?? PILL_TONES.neutral,
        className,
      )}
    >
      {children}
    </span>
  );
}

const BANNER_TONES = {
  info: 'border-info/30 bg-info-soft text-ink-primary',
  warning: 'border-warning/30 bg-warning-soft text-ink-primary',
  error: 'border-danger/30 bg-danger-soft text-ink-primary',
};

const BANNER_ICONS = { info: Info, warning: TriangleAlert, error: TriangleAlert };

/**
 * A caveat that has to be read before the numbers are.
 *
 * Rendered above the chart, not below it and not in a collapsed "details"
 * drawer. The funnel's `FUNNEL_STARTS_AT_CART` is the case this exists for: a
 * reader who misses it divides orders by something and calls the result a site
 * conversion rate.
 *
 * The backend's own `message` is displayed verbatim. Paraphrasing a caveat is
 * how a caveat gets softened.
 */
export function CaveatBanner({ warning, tone, title, className }) {
  if (!warning) return null;
  const level = tone ?? warning.severity ?? 'info';
  const Icon = BANNER_ICONS[level] ?? Info;
  return (
    <div
      role="note"
      className={cn(
        'flex items-start gap-3 rounded-sm border px-4 py-3',
        BANNER_TONES[level] ?? BANNER_TONES.info,
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        {title && <p className="text-sm font-semibold">{title}</p>}
        <p className="text-xs leading-relaxed text-ink-secondary">{warning.message}</p>
      </div>
    </div>
  );
}

/**
 * What a view renders when the resolver returned a reason instead of data.
 *
 * `not_configured()` comes back with the reason, the capabilities that would
 * fix it, and an explicitly empty `sources` list. All three are shown: the
 * empty source list is the difference between "nothing is connected" and
 * "everything is connected and the answer is zero", and an admin who cannot
 * see which one they are looking at will assume the second.
 */
export function NotConfiguredPanel({ envelope, viewDef, title = 'Not available yet' }) {
  const reason = notConfigured(envelope);
  const requires = reason?.requires?.length
    ? reason.requires
    : (viewDef?.requires ?? []);
  const message =
    reason?.message ||
    viewDef?.limitation ||
    'The data this view needs is not recorded anywhere in this deployment.';

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col items-start gap-3 p-6">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-warning-soft text-warning">
          <PlugZap className="size-5" aria-hidden="true" />
        </span>
        <h3 className="text-h3 text-ink-primary">{title}</h3>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-secondary">{message}</p>
        {requires.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-tertiary">Needs:</span>
            {requires.map((item) => (
              <StatusPill key={item} tone="info">
                {item}
              </StatusPill>
            ))}
          </div>
        )}
        {reason?.noSources && (
          <p className="text-xs text-ink-tertiary">
            No source was read for this view. That is why there is no chart here —
            an empty chart would be a measurement, and none was taken.
          </p>
        )}
      </div>
    </Card>
  );
}

/**
 * A compact table for a bespoke board.
 *
 * Not the shared `DataTable`: these boards need per-row status semantics
 * (unchecked rows, never-built rows, severity rows) that a generic sortable
 * table would flatten into "a row with some blank cells".
 */
export function MiniTable({ head, children, caption, className }) {
  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full min-w-[36rem] border-collapse text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-line-subtle text-left">{head}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Th({ children, align = 'left', className }) {
  return (
    <th
      scope="col"
      className={cn(
        'px-3 py-2 text-xs font-medium text-ink-tertiary',
        align === 'right' && 'text-right',
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, align = 'left', className, ...props }) {
  return (
    <td
      className={cn(
        'px-3 py-2 align-top text-ink-primary',
        align === 'right' && 'text-right tabular-nums',
        className,
      )}
      {...props}
    >
      {children}
    </td>
  );
}
