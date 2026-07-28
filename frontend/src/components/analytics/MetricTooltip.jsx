import { useEffect, useId, useRef, useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * The definition behind a number, one keystroke away.
 *
 * Two people reading "revenue" mean different things roughly half the time —
 * one includes tax and shipping, the other does not; one counts cancelled
 * orders, the other does not. The KPI catalogue settles that argument, and this
 * is where the settlement is shown: the exact formula, which order statuses are
 * in, how tax, shipping and refunds are treated, and the caveats the metric
 * ships with.
 *
 * Opens on hover AND on focus, and stays open on click so a touch user (who has
 * no hover) can read it. Escape closes. The trigger is a real button with
 * `aria-describedby`, so the definition is announced rather than being a visual
 * flourish that assistive tech never reaches.
 *
 * Props:
 *   kpi    — a KPI catalogue entry from registry.contract.json
 *   align  — 'start' | 'end' (which edge the panel hangs from)
 *   label  — override the accessible name of the trigger
 */

const TREATMENT_LABEL = {
  tax_treatment: 'Tax',
  shipping_treatment: 'Shipping',
  refund_treatment: 'Refunds',
};

export function MetricTooltip({ kpi, align = 'start', label, className }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onPointerDown = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointerDown);
    };
  }, [open]);

  if (!kpi) return null;

  const treatments = Object.entries(TREATMENT_LABEL)
    .map(([key, text]) => (kpi[key] ? `${text}: ${kpi[key]}` : null))
    .filter(Boolean);
  const caveats = Array.isArray(kpi.caveats) ? kpi.caveats : [];
  const included = Array.isArray(kpi.included_statuses) ? kpi.included_statuses : [];
  const excluded = Array.isArray(kpi.excluded_statuses) ? kpi.excluded_statuses : [];

  return (
    <span
      ref={wrapRef}
      className={cn('relative inline-flex align-middle', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-describedby={open ? panelId : undefined}
        aria-label={label || `What "${kpi.label}" means`}
        onClick={() => setOpen((v) => !v)}
        onFocus={() => setOpen(true)}
        className={cn(
          'inline-grid size-4 place-items-center rounded-full text-ink-tertiary',
          'transition-colors hover:text-ink-primary focus-visible:focus-ring',
        )}
      >
        <HelpCircle className="size-3.5" aria-hidden="true" />
      </button>

      {open && (
        <span
          id={panelId}
          role="tooltip"
          className={cn(
            'absolute top-full z-30 mt-2 block w-[min(22rem,80vw)] cursor-default',
            'rounded-lg border border-line-subtle bg-bg-elevated p-4 text-left shadow-lg',
            'animate-scaleIn',
            align === 'end' ? 'right-0 origin-top-right' : 'left-0 origin-top-left',
          )}
        >
          <span className="block text-sm font-semibold text-ink-primary">
            {kpi.label}
          </span>

          {(kpi.tooltip || kpi.description) && (
            <span className="mt-1.5 block text-xs leading-relaxed text-ink-secondary">
              {kpi.tooltip || kpi.description}
            </span>
          )}

          {kpi.formula && (
            <span className="mt-3 block">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                Formula
              </span>
              <code className="mt-1 block break-words rounded-sm bg-bg-sunken px-2 py-1.5 font-mono text-[11px] leading-relaxed text-ink-primary">
                {kpi.formula}
              </code>
            </span>
          )}

          {(kpi.numerator || kpi.denominator) && (
            <span className="mt-2 block text-[11px] leading-relaxed text-ink-tertiary">
              {kpi.numerator && (
                <>
                  <span className="text-ink-secondary">Numerator:</span> {kpi.numerator}
                  <br />
                </>
              )}
              {kpi.denominator && (
                <>
                  <span className="text-ink-secondary">Denominator:</span> {kpi.denominator}
                </>
              )}
            </span>
          )}

          {(treatments.length > 0 || included.length > 0) && (
            <span className="mt-3 block border-t border-line-subtle pt-2.5 text-[11px] leading-relaxed text-ink-tertiary">
              {treatments.length > 0 && <span className="block">{treatments.join(' · ')}</span>}
              {included.length > 0 && (
                <span className="block">
                  <span className="text-ink-secondary">Statuses counted:</span>{' '}
                  {included.join(', ')}
                </span>
              )}
              {excluded.length > 0 && (
                <span className="block">
                  <span className="text-ink-secondary">Excluded:</span> {excluded.join(', ')}
                </span>
              )}
              {kpi.basis && (
                <span className="block">
                  <span className="text-ink-secondary">Basis:</span> {kpi.basis}-level
                </span>
              )}
            </span>
          )}

          {caveats.length > 0 && (
            <span className="mt-3 block border-t border-line-subtle pt-2.5">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-warning">
                Read this before quoting it
              </span>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-ink-secondary">
                {caveats.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </span>
          )}
        </span>
      )}
    </span>
  );
}
