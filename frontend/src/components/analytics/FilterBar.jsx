import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, X } from 'lucide-react';
import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import {
  activeFilterCount,
  GRANULARITIES,
  MAX_HOURLY_RANGE_DAYS,
  normaliseFilters,
  parseFilters,
  supportedFilterKeys,
  toSearchParams,
  UNMAPPED_VIEW_FILTERS,
} from '@/features/analytics/filters.js';
import { ComparisonSelector } from './ComparisonSelector.jsx';
import { DateRangePicker } from './DateRangePicker.jsx';
import { SegmentedControl } from './SegmentedControl.jsx';
import { cn } from '@/lib/utils.js';

/**
 * The controls that decide what the numbers cover.
 *
 * Two rules, both inherited from `filters.js` rather than reimplemented here:
 *
 *  - **Only what the view declares is rendered.** Carrying a courier filter
 *    onto a view that ignores couriers would leave a control that looks applied
 *    and is not, which is the most direct way to hand someone a wrong number.
 *  - **State lives in the URL.** Refresh, the back button and a link pasted
 *    into Slack all reproduce the same screen, because the URL is the only copy
 *    of the filter state — there is no second copy in component state to drift
 *    from it.
 *
 * Text fields commit on blur or Enter, not per keystroke: the URL is history,
 * and eight entries for "b-l-u-e-d-a-r-t" makes the back button useless.
 */

/**
 * Read and write the analytics filter state through the address bar.
 *
 * `filters.js` is pure by design, so the React half lives here. History is
 * REPLACED rather than pushed: a view is the navigable unit, filters are
 * refinements of it, and pushing each one would bury the previous view under a
 * dozen entries of the current one.
 */
export function useUrlFilters(view) {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters = useMemo(() => parseFilters(searchParams, view), [searchParams, view]);

  const setFilters = useCallback(
    (patch) => {
      const next = normaliseFilters({ ...filters, ...patch }, view);
      const params = toSearchParams(next, view);
      // Canonical strings mean an unchanged filter set is a no-op rather than
      // a re-render that re-fetches.
      if (params.toString() === searchParams.toString()) return;
      setSearchParams(params, { replace: true });
    },
    [filters, view, searchParams, setSearchParams],
  );

  const reset = useCallback(() => {
    if (searchParams.toString() === '') return;
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [searchParams, setSearchParams]);

  return {
    filters,
    setFilters,
    reset,
    activeCount: activeFilterCount(filters, view),
  };
}

const GRANULARITY_LABEL = { hour: 'Hour', day: 'Day', week: 'Week', month: 'Month' };

/** Longest window each preset can resolve to — mirrors the table in filters.js. */
const PRESET_MAX_SPAN_DAYS = { '7d': 7, '30d': 30, '90d': 90, mtd: 31, qtd: 92, ytd: 366 };

const DAY_MS = 86_400_000;

/**
 * Would `granularity=hour` be rewritten away for this window?
 *
 * Computed for custom ranges too, not just presets: a hand-picked 60-day window
 * is exactly as many buckets as `90d` and gets the same refusal, and letting the
 * option look available there would make the control appear broken.
 */
function exceedsHourlyCap(filters) {
  if (filters?.period === 'custom') {
    if (!filters.date_from || !filters.date_to) return true;
    const span = Math.round(
      (Date.parse(`${filters.date_to}T00:00:00Z`) -
        Date.parse(`${filters.date_from}T00:00:00Z`)) / DAY_MS,
    );
    return !Number.isFinite(span) || span > MAX_HOURLY_RANGE_DAYS;
  }
  const span = PRESET_MAX_SPAN_DAYS[filters?.period ?? '30d'] ?? Infinity;
  return span > MAX_HOURLY_RANGE_DAYS;
}

/**
 * The free-form keys. `dimension` and `limit` are deliberately absent: they are
 * shape controls the view or the table owns, not questions an operator asks.
 */
const FIELD_META = {
  product_id: { label: 'Product', type: 'number', placeholder: 'Product ID' },
  category_id: { label: 'Category', type: 'number', placeholder: 'Category ID' },
  sku: { label: 'SKU', type: 'text' },
  customer_segment: { label: 'Segment', type: 'text' },
  new_or_returning: {
    label: 'Customer',
    type: 'select',
    options: [
      { value: '', label: 'All' },
      { value: 'new', label: 'New' },
      { value: 'returning', label: 'Returning' },
    ],
  },
  source: { label: 'Source', type: 'text' },
  medium: { label: 'Medium', type: 'text' },
  campaign: { label: 'Campaign', type: 'text' },
  device: { label: 'Device', type: 'text' },
  country: { label: 'Country', type: 'text', placeholder: 'IN', maxLength: 2 },
  state: { label: 'State', type: 'text' },
  city: { label: 'City', type: 'text' },
  payment_method: { label: 'Payment method', type: 'text' },
  payment_gateway: { label: 'Gateway', type: 'text' },
  courier: { label: 'Courier', type: 'text' },
  order_status: { label: 'Order status', type: 'text' },
  coupon: { label: 'Coupon', type: 'text' },
};

/** A text/number field that only writes to the URL once the operator is done. */
function DraftField({ fieldKey, meta, value, options, onCommit }) {
  const [draft, setDraft] = useState(value == null ? '' : String(value));

  useEffect(() => {
    setDraft(value == null ? '' : String(value));
  }, [value]);

  const commit = () => {
    const trimmed = draft.trim();
    const next = trimmed === '' ? null : meta.type === 'number' ? Number(trimmed) : trimmed;
    onCommit({ [fieldKey]: next });
  };

  if (meta.type === 'select' || options) {
    const list = options ?? meta.options ?? [];
    return (
      <Select
        label={meta.label}
        value={value == null ? '' : String(value)}
        onChange={(e) => onCommit({ [fieldKey]: e.target.value === '' ? null : e.target.value })}
      >
        {list.map((o) => (
          <option key={String(o.value)} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    );
  }

  return (
    <Input
      label={meta.label}
      type={meta.type === 'number' ? 'number' : 'text'}
      inputMode={meta.type === 'number' ? 'numeric' : undefined}
      maxLength={meta.maxLength}
      placeholder={meta.placeholder}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          commit();
        }
      }}
    />
  );
}

export function FilterBar({
  view,
  filters,
  onChange,
  onReset,
  activeCount = 0,
  resolved = null,
  optionsByKey = {},
  action = null,
  className,
}) {
  const [expanded, setExpanded] = useState(false);

  const keys = useMemo(() => supportedFilterKeys(view), [view]);
  const declared = Array.isArray(view?.filters) ? view.filters : [];

  const hasDateRange = keys.includes('period');
  const hasComparison = keys.includes('comparison');
  const hasGranularity = keys.includes('granularity');
  const extraKeys = keys.filter((k) => k in FIELD_META);

  // Hourly buckets are refused over a long window. `filters.js` silently
  // rewrites the value; disabling the option instead means the operator sees
  // why rather than watching their choice bounce back.
  const hourlyBlocked = exceedsHourlyCap(filters);

  const granularityOptions = GRANULARITIES.map((g) => ({
    value: g,
    label: GRANULARITY_LABEL[g] ?? g,
    disabled: g === 'hour' && hourlyBlocked,
    title:
      g === 'hour' && hourlyBlocked
        ? `Hourly buckets are limited to ${MAX_HOURLY_RANGE_DAYS} days.`
        : undefined,
  }));

  // Tokens the registry declares that `AnalyticsFilters` has no field for.
  // Said out loud rather than dropped, so the gap is visible to the person it
  // affects instead of only to whoever greps the codec.
  const unsupported = declared.filter((t) => UNMAPPED_VIEW_FILTERS.includes(t));

  if (!hasDateRange && !hasComparison && !hasGranularity && extraKeys.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="Filters"
      className={cn(
        'rounded-lg border border-line-subtle bg-bg-elevated px-4 py-3 shadow-sm',
        className,
      )}
    >
      <div className="flex flex-col gap-x-4 gap-y-2 lg:flex-row lg:flex-wrap lg:items-start">
        {hasDateRange && (
          <DateRangePicker value={filters} onChange={onChange} className="lg:min-w-[18rem]" />
        )}

        {hasComparison && (
          <ComparisonSelector
            value={filters?.comparison}
            onChange={onChange}
            resolved={resolved}
          />
        )}

        {hasGranularity && (
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-secondary">Bucket</span>
            <SegmentedControl
              className="hidden md:inline-flex"
              label="Time bucket"
              options={granularityOptions}
              value={filters?.granularity ?? 'day'}
              onChange={(g) => onChange?.({ granularity: g })}
            />
            <div className="md:hidden">
              <Select
                aria-label="Time bucket"
                value={filters?.granularity ?? 'day'}
                onChange={(e) => onChange?.({ granularity: e.target.value })}
              >
                {granularityOptions.map((o) => (
                  <option key={o.value} value={o.value} disabled={o.disabled}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        )}

        <div className="flex flex-1 flex-wrap items-center justify-end gap-2 self-center">
          {extraKeys.length > 0 && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
            >
              <SlidersHorizontal className="size-4" aria-hidden="true" />
              More filters
              {activeCount > 0 && (
                <Badge tone="accent" className="ml-1">
                  {activeCount}
                </Badge>
              )}
            </Button>
          )}
          {activeCount > 0 && (
            <Button size="sm" variant="ghost" onClick={onReset}>
              <X className="size-4" aria-hidden="true" />
              Clear
            </Button>
          )}
          {action}
        </div>
      </div>

      {expanded && extraKeys.length > 0 && (
        <div className="mt-3 grid grid-cols-1 gap-x-4 border-t border-line-subtle pt-3 sm:grid-cols-2 lg:grid-cols-4">
          {extraKeys.map((key) => (
            <DraftField
              key={key}
              fieldKey={key}
              meta={FIELD_META[key]}
              value={filters?.[key]}
              options={optionsByKey[key]}
              onCommit={onChange}
            />
          ))}
        </div>
      )}

      {unsupported.length > 0 && (
        <p className="mt-2 text-[11px] text-ink-tertiary">
          This view also lists {unsupported.join(', ')} as a dimension, but the API has
          no filter for it yet — those breakdowns are shown in full rather than filtered.
        </p>
      )}
    </section>
  );
}
