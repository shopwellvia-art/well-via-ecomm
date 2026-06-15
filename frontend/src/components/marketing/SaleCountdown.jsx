import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils.js';

function remaining(target) {
  const diff = Math.max(0, target - Date.now());
  return {
    d: Math.floor(diff / 86400000),
    h: Math.floor((diff % 86400000) / 3600000),
    m: Math.floor((diff % 3600000) / 60000),
    s: Math.floor((diff % 60000) / 1000),
  };
}

/**
 * Live sale countdown — ticks every second toward `target` (a ms timestamp).
 * `tone="light"` renders white-on-dark cells for use over dark banners.
 * `compact` renders HH:MM:SS chip style (no labels, smaller cells) for the
 *   Deals-of-the-Day orange header bar.
 */
export function SaleCountdown({ target, className, tone = 'dark', compact = false }) {
  const [t, setT] = useState(() => remaining(target));

  useEffect(() => {
    const id = setInterval(() => setT(remaining(target)), 1000);
    return () => clearInterval(id);
  }, [target]);

  if (compact) {
    // HH:MM:SS chips — Flipkart Deals-of-the-Day style
    const hh = String(t.h + t.d * 24).padStart(2, '0');
    const mm = String(t.m).padStart(2, '0');
    const ss = String(t.s).padStart(2, '0');
    const chips = [hh, mm, ss];
    return (
      <div className={cn('flex items-center gap-1 font-bold nums', className)}>
        {chips.map((v, i) => (
          <span key={i} className="flex items-center gap-1">
            <span
              className={cn(
                'grid h-7 min-w-[1.75rem] place-items-center rounded-xs px-1 text-sm tabular-nums',
                tone === 'light' ? 'bg-white/20 text-white' : 'bg-bg-sunken text-ink-primary',
              )}
            >
              {v}
            </span>
            {i < chips.length - 1 && (
              <span className={tone === 'light' ? 'text-white/80' : 'text-ink-tertiary'}>:</span>
            )}
          </span>
        ))}
      </div>
    );
  }

  const cells = [
    ['Days', t.d],
    ['Hrs', t.h],
    ['Min', t.m],
    ['Sec', t.s],
  ];

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {cells.map(([label, val], i) => (
        <div key={label} className="flex items-center gap-2">
          <div
            className={cn(
              'flex min-w-[3rem] flex-col items-center rounded-sm px-2.5 py-1.5',
              tone === 'light'
                ? 'bg-white/15 text-white'
                : 'bg-bg-sunken text-ink-primary shadow-sm',
            )}
          >
            <span className="text-xl font-bold tabular-nums leading-none">
              {String(val).padStart(2, '0')}
            </span>
            <span
              className={cn(
                'mt-1 text-[10px] font-medium uppercase tracking-wide',
                tone === 'light' ? 'text-white/70' : 'text-ink-tertiary',
              )}
            >
              {label}
            </span>
          </div>
          {i < cells.length - 1 && (
            <span
              className={cn(
                'text-lg font-bold',
                tone === 'light' ? 'text-white/50' : 'text-ink-tertiary',
              )}
            >
              :
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/** A rolling ~2-day sale window so the countdown is always live and positive. */
export function useSaleTarget() {
  const [target] = useState(() => {
    const end = new Date();
    end.setHours(23, 59, 59, 999);
    end.setDate(end.getDate() + 2);
    return end.getTime();
  });
  return target;
}
