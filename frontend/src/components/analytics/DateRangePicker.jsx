import { useEffect, useState } from 'react';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { MAX_RANGE_DAYS, PERIODS } from '@/features/analytics/filters.js';
import { SegmentedControl } from './SegmentedControl.jsx';
import { cn } from '@/lib/utils.js';

/**
 * The window every number on the page is measured over.
 *
 * Presets on wide screens, a native select on narrow ones, and two native
 * `<input type="date">` for a custom range. Native on purpose: a date-picker
 * dependency would be ~40kB to reproduce a control the platform ships, already
 * localises, already exposes to assistive tech and already renders as a real
 * picker on touch.
 *
 * A custom range is held as a draft until BOTH ends are set and valid. Without
 * that, typing the first date would emit a half-range, `filters.js` would
 * normalise it away as "not a window", and the field would appear to clear
 * itself under the operator's cursor.
 *
 * Props:
 *   value     — { period, date_from, date_to }
 *   onChange  — (patch) => void, merged by the caller
 *   disabled  — lock the control while a required filter is missing
 */

const PERIOD_LABEL = {
  '7d': '7d',
  '30d': '30d',
  '90d': '90d',
  mtd: 'MTD',
  qtd: 'QTD',
  ytd: 'YTD',
  custom: 'Custom',
};

const PERIOD_TITLE = {
  mtd: 'Month to date',
  qtd: 'Quarter to date',
  ytd: 'Year to date',
  custom: 'Pick exact start and end dates',
};

const DAY_MS = 86_400_000;

function spanDays(from, to) {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS);
}

/** Returns an error string, or '' when the pair is a window the backend accepts. */
function validateRange(from, to) {
  if (!from || !to) return '';
  const span = spanDays(from, to);
  if (Number.isNaN(span)) return 'Enter both dates.';
  // The window is half-open [from, to) — equal ends select nothing.
  if (span <= 0) return 'The end date must be after the start date.';
  if (span > MAX_RANGE_DAYS) return `Windows are capped at ${MAX_RANGE_DAYS} days.`;
  return '';
}

export function DateRangePicker({ value, onChange, disabled = false, className }) {
  const period = value?.period ?? '30d';
  const [draft, setDraft] = useState({
    from: value?.date_from ?? '',
    to: value?.date_to ?? '',
  });

  // Re-seed when the URL changes underneath us — a pasted link or the back
  // button must move the visible dates, not just the numbers.
  useEffect(() => {
    setDraft({ from: value?.date_from ?? '', to: value?.date_to ?? '' });
  }, [value?.date_from, value?.date_to]);

  const options = PERIODS.map((p) => ({
    value: p,
    label: PERIOD_LABEL[p] ?? p,
    title: PERIOD_TITLE[p],
  }));

  function selectPeriod(next) {
    if (next === 'custom') {
      // Only commit `custom` once there is a real window behind it; otherwise
      // just reveal the inputs and leave the applied period alone.
      const error = validateRange(draft.from, draft.to);
      if (draft.from && draft.to && !error) {
        onChange?.({ period: 'custom', date_from: draft.from, date_to: draft.to });
      } else {
        onChange?.({ period: 'custom', date_from: null, date_to: null });
      }
      return;
    }
    onChange?.({ period: next, date_from: null, date_to: null });
  }

  function editDraft(edge, raw) {
    const next = { ...draft, [edge]: raw };
    setDraft(next);
    if (next.from && next.to && !validateRange(next.from, next.to)) {
      onChange?.({ period: 'custom', date_from: next.from, date_to: next.to });
    }
  }

  const error = validateRange(draft.from, draft.to);
  const isCustom = period === 'custom';

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-ink-secondary">Period</span>
        <SegmentedControl
          className="hidden md:inline-flex"
          label="Reporting period"
          options={options}
          value={period}
          onChange={selectPeriod}
        />
        <div className="md:hidden">
          <Select
            aria-label="Reporting period"
            value={period}
            disabled={disabled}
            onChange={(e) => selectPeriod(e.target.value)}
          >
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {isCustom && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <Input
            type="date"
            label="From"
            value={draft.from}
            max={draft.to || undefined}
            disabled={disabled}
            onChange={(e) => editDraft('from', e.target.value)}
            className="sm:w-44"
          />
          <Input
            type="date"
            label="To"
            value={draft.to}
            min={draft.from || undefined}
            disabled={disabled}
            error={error || undefined}
            helper={!error && draft.from && draft.to ? 'End date is exclusive.' : undefined}
            onChange={(e) => editDraft('to', e.target.value)}
            className="sm:w-44"
          />
        </div>
      )}
    </div>
  );
}
