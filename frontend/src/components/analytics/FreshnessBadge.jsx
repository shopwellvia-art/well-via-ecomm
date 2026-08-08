import { Clock } from 'lucide-react';
import { Badge } from '@/components/ui/Badge.jsx';
import { cn } from '@/lib/utils.js';

/**
 * How old the answer is, and whether that is older than it should be.
 *
 * `last_updated_at` is nullable in the envelope and null means *nothing has
 * aggregated yet*. That must never render as "just now" — a fresh install with
 * an empty rollup would then claim to be current while showing no data, which
 * is the most expensive possible misread on a dashboard.
 *
 * Staleness is judged against the view's own cadence, not a fixed clock: two
 * hours old is fine for a daily rollup and alarming for a realtime counter.
 *
 * Props:
 *   freshness      — 'realtime' | 'hourly' | 'daily' (the view's declared cadence)
 *   lastUpdatedAt  — ISO instant of the newest source watermark, or null
 *   computedAt     — ISO instant this response was computed (tooltip only)
 *   size           — Badge size
 *   showCadence    — append the cadence, e.g. "· hourly"
 */

/** How long past the cadence before "old" becomes "stale", in milliseconds. */
const STALE_AFTER_MS = {
  realtime: 5 * 60 * 1000,
  hourly: 3 * 60 * 60 * 1000,
  daily: 36 * 60 * 60 * 1000,
};

const CADENCE_LABEL = {
  realtime: 'realtime',
  hourly: 'hourly',
  daily: 'daily',
};

/**
 * Coarse relative time. Deliberately coarse: "2h ago" is the honest resolution
 * of an hourly rollup, and "1h 47m ago" implies a precision the bucket does not
 * have. Future instants read as "just now" rather than negative durations —
 * a clock skew between browser and server should not look like a data problem.
 */
export function relativeTime(instant) {
  const then = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(then.getTime())) return null;
  const deltaMs = Date.now() - then.getTime();
  if (deltaMs < 60_000) return 'just now';
  const minutes = Math.floor(deltaMs / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** Absolute rendering for the tooltip — the relative form alone is unauditable. */
function absoluteTime(instant) {
  const then = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(then.getTime())) return null;
  return then.toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function FreshnessBadge({
  freshness = 'daily',
  lastUpdatedAt = null,
  computedAt = null,
  size = 'sm',
  showCadence = false,
  className,
}) {
  const cadence = CADENCE_LABEL[freshness] ?? 'daily';

  // Null watermark is a statement, not a gap. Say it plainly.
  if (lastUpdatedAt == null) {
    return (
      <Badge
        tone="warning"
        size={size}
        title={
          'No source has been aggregated yet, so there is no "as of" time. ' +
          'This is not the same as an up-to-date result showing zero.'
        }
        className={cn('whitespace-nowrap', className)}
      >
        <Clock className="size-3 shrink-0" aria-hidden="true" />
        Not yet computed
      </Badge>
    );
  }

  const then = new Date(lastUpdatedAt);
  const ageMs = Number.isNaN(then.getTime()) ? null : Date.now() - then.getTime();
  const isStale = ageMs != null && ageMs > (STALE_AFTER_MS[cadence] ?? STALE_AFTER_MS.daily);
  const relative = relativeTime(lastUpdatedAt) ?? 'unknown';
  const absolute = absoluteTime(lastUpdatedAt);

  const title = [
    absolute ? `Data through ${absolute}.` : null,
    `Expected cadence: ${cadence}.`,
    isStale ? 'This is older than the cadence promises.' : null,
    computedAt ? `Response computed ${absoluteTime(computedAt)}.` : null,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <Badge
      tone={isStale ? 'warning' : 'neutral'}
      size={size}
      title={title}
      className={cn('whitespace-nowrap', className)}
    >
      <Clock className="size-3 shrink-0" aria-hidden="true" />
      <span className="nums">{relative}</span>
      {showCadence && <span className="opacity-70">· {cadence}</span>}
    </Badge>
  );
}
