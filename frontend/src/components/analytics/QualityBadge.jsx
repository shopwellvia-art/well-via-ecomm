import { Badge } from '@/components/ui/Badge.jsx';
import { QUALITY_RANK } from '@/features/analytics/viewState.js';
import { cn } from '@/lib/utils.js';

/**
 * What a number is actually worth, said out loud.
 *
 * The backend labels every KPI with a `MetricQuality`. Rendering the number
 * without the label is the whole failure mode this system exists to prevent —
 * an ESTIMATED margin and an AUTHORITATIVE one look identical on a card, and
 * only one of them should change a pricing decision.
 *
 * Tones are deliberately not a five-step rainbow. AUTHORITATIVE and ACTUAL are
 * both "you can act on this" and share a tone; the eye should only be pulled by
 * ESTIMATED and INCOMPLETE, which are the two that change what you'd do next.
 *
 * Props:
 *   quality      — 'AUTHORITATIVE' | 'ACTUAL' | 'ALLOCATED' | 'ESTIMATED' | 'INCOMPLETE'
 *   coveragePct  — optional 0..100; appended as "· 82%" when below 100
 *   size         — Badge size ('sm' | 'md')
 *   showLabel    — false renders the dot + coverage only (for dense rows)
 */

/** Tone, display label, and the one-line "so what" for each quality grade. */
export const QUALITY_META = {
  AUTHORITATIVE: {
    tone: 'success',
    label: 'Authoritative',
    hint: 'Read straight from the transactional record. This is the number of record.',
  },
  ACTUAL: {
    tone: 'success',
    label: 'Actual',
    hint: 'An observed real value from a trusted source, not a derived one.',
  },
  ALLOCATED: {
    tone: 'info',
    label: 'Allocated',
    hint: 'Split from an order-level total by a documented, reconciled rule.',
  },
  ESTIMATED: {
    tone: 'warning',
    label: 'Estimated',
    hint: 'Derived from a cost rule or assumption rather than an observation.',
  },
  INCOMPLETE: {
    tone: 'danger',
    label: 'Incomplete',
    hint: 'Some inputs are missing. Coverage says how much of it is known.',
  },
};

/** Unknown grades are treated as the worst one — never as the best. */
export function qualityMeta(quality) {
  if (quality && QUALITY_RANK.includes(quality)) return QUALITY_META[quality];
  return QUALITY_META.INCOMPLETE;
}

/**
 * Coverage as a number, or `null` when it was not measured.
 *
 * `Number(null)` is `0`, and `coverage_pct` is nullable in the envelope — so
 * the obvious one-liner turns "we did not measure coverage" into a confident
 * "0% of rows were usable". That is the same class of lie as charting a null as
 * zero, on the one field whose entire job is to say how much is unknown.
 */
export function toCoverage(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function QualityBadge({
  quality,
  coveragePct = null,
  size = 'sm',
  showLabel = true,
  className,
}) {
  const meta = qualityMeta(quality);
  const coverage = toCoverage(coveragePct);
  const showCoverage = coverage !== null && coverage < 100;
  const title = showCoverage
    ? `${meta.label} — ${meta.hint} Coverage: ${coverage.toFixed(0)}% of the underlying rows.`
    : `${meta.label} — ${meta.hint}`;

  return (
    <Badge
      tone={meta.tone}
      size={size}
      dot
      title={title}
      className={cn('whitespace-nowrap', className)}
    >
      {showLabel && meta.label}
      {showCoverage && (
        <span className="nums opacity-80">
          {showLabel && '· '}
          {coverage.toFixed(0)}%
        </span>
      )}
    </Badge>
  );
}
