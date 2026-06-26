import { motion } from 'framer-motion';
import { cn } from '@/lib/utils.js';

/**
 * 5-bar rating histogram. Rows ordered 5★ on top down to 1★.
 * The fill bars animate in when the section scrolls into view.
 *
 * Props:
 *   distribution — { "1": n, "2": n, "3": n, "4": n, "5": n }
 *   total        — total review count (used to compute percentages)
 *   onRowClick   — optional. Called with the star number when a row is clicked
 *                  (useful for filtering reviews by star).
 */
export function RatingHistogram({ distribution, total, onRowClick, className }) {
  const safeTotal = Number(total) || 0;
  const dist = distribution || {};

  return (
    <ul className={cn('flex flex-col gap-2', className)}>
      {[5, 4, 3, 2, 1].map((star) => {
        const count = Number(dist[String(star)] || 0);
        const pct = safeTotal > 0 ? Math.round((count / safeTotal) * 100) : 0;
        const Row = onRowClick ? 'button' : 'div';
        return (
          <li key={star}>
            <Row
              {...(onRowClick && {
                type: 'button',
                onClick: () => onRowClick(star),
              })}
              className={cn(
                'flex w-full items-center gap-3 text-sm text-wmuted',
                onRowClick &&
                  'rounded-sm hover:text-wink text-left transition-colors',
              )}
            >
              <span className="nums w-14 shrink-0 text-xs text-wmuted">
                {star} star{star === 1 ? '' : 's'}
              </span>
              <span className="relative block h-2 flex-1 overflow-hidden rounded-full bg-wcanvas">
                <motion.span
                  className="absolute inset-y-0 left-0 rounded-full bg-wgold"
                  initial={{ width: 0 }}
                  whileInView={{ width: `${pct}%` }}
                  transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1], delay: (5 - star) * 0.06 }}
                  viewport={{ once: true }}
                />
              </span>
              <span className="nums w-8 shrink-0 text-right text-xs text-wmuted">
                {pct}%
              </span>
            </Row>
          </li>
        );
      })}
    </ul>
  );
}
