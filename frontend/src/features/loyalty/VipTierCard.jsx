import { Award, TrendingUp, Sparkles, ChevronRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { Card } from '@/components/ui/Card.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp } from '@/lib/motion.js';

/**
 * Storefront VIP card. Reads `tier_progress` from /loyalty/me. Renders nothing
 * if the store has no VIP tiers configured yet.
 *
 *   { current: {name, color, earn_multiplier, benefits}, next: ..., points_to_next, progress_pct }
 */
export function VipTierCard({ progress }) {
  if (!progress || (!progress.current && !progress.next)) return null;
  const { current, next, points_to_next, progress_pct } = progress;

  // Accent color drives the badge tint. Falls back to the theme accent when
  // the admin didn't set a hex.
  const badgeColor = current?.color || 'var(--accent)';
  const nextColor = next?.color || 'var(--accent)';
  const progressPct = Math.max(2, Math.min(100, progress_pct || 0));
  const isTopTier = !next;

  return (
    <Card className="overflow-hidden p-0">
      {/* Gradient band — uses tier color as a CSS variable so it survives tokens */}
      <div
        className="h-1.5 w-full"
        style={{
          background: `linear-gradient(90deg, ${badgeColor}cc 0%, ${badgeColor} 50%, ${badgeColor}99 100%)`,
        }}
        aria-hidden="true"
      />

      <motion.div
        variants={fadeUp}
        initial="hidden"
        animate="show"
        className="grid gap-6 p-6 md:grid-cols-[1fr_280px]"
      >
        {/* Left: current tier identity */}
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            <Award className="size-3.5 shrink-0" aria-hidden="true" />
            Your VIP tier
          </p>

          <div className="mt-3 flex items-center gap-3.5">
            <span
              className="grid size-12 shrink-0 place-items-center rounded-xl text-sm font-semibold text-white shadow-md"
              style={{ background: badgeColor }}
              aria-hidden="true"
            >
              <Award className="size-6" />
            </span>
            <div className="min-w-0">
              <p className="text-h2 font-semibold text-ink-primary leading-tight">
                {current?.name || 'Explorer'}
              </p>
              {current && Number(current.earn_multiplier) > 1 && (
                <p className="mt-1 inline-flex items-center gap-1 rounded-full bg-success/12 px-2 py-0.5 text-[11px] font-semibold text-success">
                  <TrendingUp className="size-3 shrink-0" aria-hidden="true" />
                  {Number(current.earn_multiplier).toFixed(2)}
                  <span className="nums">×</span> points multiplier
                </p>
              )}
            </div>
          </div>

          {current?.benefits && (
            <p className="mt-3.5 text-sm leading-relaxed text-ink-secondary">
              {current.benefits}
            </p>
          )}

          {isTopTier && (
            <p className="mt-3.5 inline-flex items-center gap-1.5 text-sm font-medium text-accent">
              <Sparkles className="size-4 shrink-0" aria-hidden="true" />
              You&apos;ve reached the highest tier
            </p>
          )}
        </div>

        {/* Right: progress to next tier */}
        <div className="min-w-0">
          {next ? (
            <div>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
                  Progress to {next.name}
                </p>
                <span
                  className="rounded-full px-2 py-0.5 text-[10px] font-semibold text-white"
                  style={{ background: nextColor }}
                  aria-hidden="true"
                >
                  {next.name}
                </span>
              </div>

              <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-fill">
                <div
                  className={cn('h-full rounded-full transition-[width] duration-700')}
                  style={{
                    width: `${progressPct}%`,
                    background: `linear-gradient(90deg, ${nextColor}bb 0%, ${nextColor} 100%)`,
                  }}
                  role="progressbar"
                  aria-valuenow={progressPct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Progress to ${next.name}: ${progressPct}%`}
                />
              </div>

              <div className="mt-2.5 flex items-center justify-between gap-2">
                <p className="text-xs text-ink-tertiary">
                  <span className="nums font-semibold text-ink-primary">
                    {progressPct}%
                  </span>{' '}
                  of the way there
                </p>
                {points_to_next != null && points_to_next > 0 && (
                  <p className="flex items-center gap-0.5 text-xs text-ink-secondary">
                    <span className="nums font-semibold text-ink-primary">
                      {points_to_next.toLocaleString()}
                    </span>{' '}
                    pts needed
                  </p>
                )}
              </div>

              <div className="mt-4 rounded-lg border border-line-subtle bg-bg-sunken p-3.5">
                {points_to_next != null && points_to_next > 0 ? (
                  <p className="text-sm text-ink-secondary leading-snug">
                    Earn{' '}
                    <strong className="text-ink-primary nums">
                      {points_to_next.toLocaleString()} more pts
                    </strong>{' '}
                    to unlock{' '}
                    <span className="font-semibold" style={{ color: nextColor }}>
                      {next.name}
                    </span>{' '}
                    with{' '}
                    <span className="font-semibold text-ink-primary nums">
                      {Number(next.earn_multiplier).toFixed(2)}×
                    </span>{' '}
                    multiplier.
                  </p>
                ) : (
                  <p className="flex items-center gap-1.5 text-sm font-medium text-success">
                    <Sparkles className="size-4 shrink-0" aria-hidden="true" />
                    You&apos;ve reached {next.name}!
                  </p>
                )}

                {next.benefits && (
                  <p className="mt-2 flex items-start gap-1.5 text-xs text-ink-tertiary">
                    <ChevronRight className="mt-px size-3.5 shrink-0 text-accent" aria-hidden="true" />
                    {next.benefits}
                  </p>
                )}
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center rounded-xl border border-line-subtle bg-bg-sunken px-5 py-6 text-center">
              <div>
                <span
                  className="mx-auto grid size-10 place-items-center rounded-full text-white shadow-md"
                  style={{ background: badgeColor }}
                  aria-hidden="true"
                >
                  <Sparkles className="size-5" />
                </span>
                <p className="mt-3 text-sm font-semibold text-ink-primary">
                  Top tier achieved
                </p>
                <p className="mt-1 text-xs text-ink-tertiary">
                  You&apos;re already at the highest level.
                </p>
              </div>
            </div>
          )}
        </div>
      </motion.div>
    </Card>
  );
}
