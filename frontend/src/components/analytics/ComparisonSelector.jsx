import { Select } from '@/components/ui/Select.jsx';
import { COMPARISONS } from '@/features/analytics/filters.js';
import { SegmentedControl } from './SegmentedControl.jsx';
import { cn } from '@/lib/utils.js';

/**
 * What "vs" means on every delta on the page.
 *
 * A percentage change is meaningless without its baseline, and the two
 * baselines answer different questions: `previous_period` measures momentum,
 * `previous_year` measures it with the festival calendar held constant — on an
 * Indian store those disagree violently around Diwali, and the disagreement is
 * the useful signal rather than a bug.
 *
 * When the server has resolved the window it is echoed underneath, so the
 * baseline is a stated fact rather than something inferred from the label.
 *
 * Props:
 *   value     — 'none' | 'previous_period' | 'previous_year'
 *   onChange  — (patch) => void
 *   resolved  — envelope.filters, for the "vs <dates>" line
 */

const COMPARISON_LABEL = {
  none: 'None',
  previous_period: 'Prev. period',
  previous_year: 'Prev. year',
};

const COMPARISON_TITLE = {
  none: 'Show current-window figures only, with no deltas.',
  previous_period: 'Compare against the window immediately before this one.',
  previous_year: 'Compare against the same window one year earlier.',
};

export function ComparisonSelector({ value, onChange, resolved = null, className }) {
  const current = value ?? 'previous_period';
  const options = COMPARISONS.map((c) => ({
    value: c,
    label: COMPARISON_LABEL[c] ?? c,
    title: COMPARISON_TITLE[c],
  }));

  const hasWindow = Boolean(resolved?.compare_from && resolved?.compare_to);

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <span className="text-sm font-medium text-ink-secondary">Compare to</span>

      <SegmentedControl
        className="hidden md:inline-flex"
        label="Comparison baseline"
        options={options}
        value={current}
        onChange={(next) => onChange?.({ comparison: next })}
      />
      <div className="md:hidden">
        <Select
          aria-label="Comparison baseline"
          value={current}
          onChange={(e) => onChange?.({ comparison: e.target.value })}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </div>

      <p className="nums min-h-[1.25rem] text-xs text-ink-tertiary">
        {current === 'none'
          ? 'No deltas shown.'
          : hasWindow
            ? `vs ${resolved.compare_from} → ${resolved.compare_to}`
            : ''}
      </p>
    </div>
  );
}
