import { motion, useReducedMotion } from 'framer-motion';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { cn } from '@/lib/utils.js';
import { duration, ease } from '@/lib/motion.js';

/**
 * Shared presentational building blocks for the storefront company pages
 * (About, Contact, Careers, Stories, Press, Corporate). Keeping them here means
 * every page shares one consistent header, prose and section rhythm.
 */

/**
 * Page header band: white bar with breadcrumb, eyebrow, title, subtitle.
 * Flipkart/Amazon style — flat white on grey, blue accent for eyebrow/links,
 * no glow or gradient backgrounds.
 */
export function CompanyHero({ hero = {}, current, children }) {
  const reduce = useReducedMotion();
  return (
    <header className="border-b border-line-subtle bg-bg-elevated">
      <div className="mx-auto w-full max-w-content px-6 py-8">
        <Breadcrumbs current={current} />
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: duration.base, ease: ease.entrance }}
          className="mt-4 max-w-3xl"
        >
          {hero.eyebrow && (
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">
              {hero.eyebrow}
            </p>
          )}
          <h1 className="mt-2 text-h1 font-semibold text-ink-primary">{hero.title}</h1>
          {hero.subtitle && (
            <p className="mt-3 text-base leading-relaxed text-ink-secondary">{hero.subtitle}</p>
          )}
          {children}
        </motion.div>
      </div>
    </header>
  );
}

/** Renders a body string into paragraphs, splitting on blank lines. */
export function Prose({ text, className }) {
  if (!text) return null;
  const paragraphs = String(text)
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <div className={cn('space-y-4 text-base leading-relaxed text-ink-secondary', className)}>
      {paragraphs.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
    </div>
  );
}

/** Small uppercase section label. */
export function SectionLabel({ children, className }) {
  return (
    <h2 className={cn('text-lg font-semibold text-ink-primary', className)}>{children}</h2>
  );
}

/** A light section wrapper that adds top spacing between major blocks. */
export function Section({ children, className }) {
  return <section className={cn('mt-8', className)}>{children}</section>;
}

/** Shown when an admin has toggled a page off. */
export function PageDisabled({ title = 'Page unavailable' }) {
  return (
    <div className="mx-auto flex min-h-[40vh] max-w-md flex-col items-center justify-center text-center">
      <h1 className="text-h2 text-ink-primary">{title}</h1>
      <p className="mt-2 text-sm text-ink-secondary">
        This page isn&apos;t available right now. Please check back soon.
      </p>
    </div>
  );
}
