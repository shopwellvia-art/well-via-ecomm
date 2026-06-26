import { motion } from 'framer-motion';
import { heroContainer, fadeUp } from '@/lib/motion.js';
import { cn } from '@/lib/utils.js';

/**
 * Presentational wellness wrapper shared by the six Wellvia company/content
 * pages (About, Contact, Careers, Stories, Press, Corporate).
 *
 * Renders:
 *  1. A wellness header band — paper-textured, eyebrow + serif title + subtitle.
 *  2. A bg-wcanvas content area — max-w-content, py-12.
 *
 * The page components own all data fetching; they compute props and pass
 * CMS-driven values here. This component has no data wiring of its own.
 *
 * Props
 * ─────
 *  eyebrow       Small uppercase gold label above the title.
 *  title         Serif h1. Skipped when isLoading is true.
 *  subtitle      Optional muted paragraph below title.
 *  extra         Optional ReactNode appended after subtitle (e.g. intro text).
 *  children      Page body rendered in the content area.
 *  isLoading     Show skeleton header instead of title/subtitle.
 *  disabled      Render an "unavailable" state instead of the full page.
 *  disabledTitle Heading shown in the disabled state.
 */
export function ContentPage({
  eyebrow,
  title,
  subtitle,
  extra,
  children,
  isLoading = false,
  disabled = false,
  disabledTitle = 'Page unavailable',
}) {
  // ── Disabled (admin toggled off) ──────────────────────────────────────────
  if (disabled) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center bg-wcanvas px-6 py-20">
        <div className="text-center">
          <p className="font-wserif text-4xl leading-tight text-wink">
            {disabledTitle}
          </p>
          <p className="mt-4 text-base text-wmuted">
            This page isn&apos;t available right now. Please check back soon.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* ── Wellness page header band ──────────────────────────────────── */}
      <header className="paper border-b border-wline">
        <div className="mx-auto w-full max-w-content px-6 py-14 md:py-20">
          {isLoading ? (
            <div className="max-w-2xl animate-pulse space-y-3">
              <div className="h-3 w-24 rounded-full bg-wgold/20" />
              <div className="h-10 w-80 rounded-lg bg-wline/60" />
              <div className="h-4 w-96 rounded bg-wline/40" />
            </div>
          ) : (
            <motion.div
              variants={heroContainer}
              initial="hidden"
              animate="show"
              className="max-w-3xl"
            >
              {eyebrow && (
                <motion.p
                  variants={fadeUp}
                  className="font-display text-[11px] font-semibold uppercase tracking-[0.22em] text-wgold"
                >
                  {eyebrow}
                </motion.p>
              )}
              <motion.h1
                variants={fadeUp}
                className="mt-3 font-wserif text-4xl leading-tight text-wink sm:text-5xl"
              >
                {title}
              </motion.h1>
              {subtitle && (
                <motion.p
                  variants={fadeUp}
                  className="mt-4 max-w-2xl text-base leading-relaxed text-wmuted"
                >
                  {subtitle}
                </motion.p>
              )}
              {extra && (
                <motion.div variants={fadeUp} className="mt-4">
                  {extra}
                </motion.div>
              )}
            </motion.div>
          )}
        </div>
      </header>

      {/* ── Content area ──────────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1], delay: 0.06 }}
        className="bg-wcanvas"
      >
        <div className="mx-auto w-full max-w-content px-6 py-12">{children}</div>
      </motion.div>
    </>
  );
}

// ── Shared sub-components ────────────────────────────────────────────────────

/**
 * Renders a body string into wellness-styled paragraphs.
 * Splits on blank lines (same logic as the original `Prose` component but
 * uses wellness tokens instead of Flipkart tokens).
 */
export function WProse({ text, className }) {
  if (!text) return null;
  const paragraphs = String(text)
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <div className={cn('space-y-4 text-base leading-relaxed text-wmuted', className)}>
      {paragraphs.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
    </div>
  );
}

/** Section spacing wrapper — default mt-10, override via className. */
export function WSection({ children, className }) {
  return <section className={cn('mt-10', className)}>{children}</section>;
}

/** Serif section heading for content pages. */
export function WSectionLabel({ children, className }) {
  return (
    <h2 className={cn('font-wserif text-xl text-wink', className)}>{children}</h2>
  );
}
