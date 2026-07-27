import { motion, useReducedMotion } from 'framer-motion';
import { duration, ease, staggerContainer } from '@/lib/motion.js';

/**
 * Consistent admin page shell.
 *
 * Props:
 *   title        — page heading (h1)
 *   description  — optional supporting copy beneath the heading
 *   action       — optional element rendered top-right (period picker, CTA, etc.)
 *   children     — page body; receives staggered reveal when motion is enabled
 *   maxWidth     — override container width (default 'max-w-[1200px]')
 */
export function AdminPage({
  title,
  description,
  action,
  children,
  maxWidth = 'max-w-[1200px]',
}) {
  const reduce = useReducedMotion();

  return (
    <div className={`mx-auto w-full ${maxWidth}`}>
      {/* Page header — clear h1 moment + tight tracking */}
      <motion.header
        initial={reduce ? false : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: duration.base, ease: ease.entrance }}
        className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
      >
        <div>
          <h1 className="text-h1 tracking-tight text-ink-primary">{title}</h1>
          {description && (
            <p className="mt-1.5 text-sm leading-relaxed text-ink-secondary">
              {description}
            </p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </motion.header>

      {/* Hairline divider — visual rhythm between header and content */}
      <motion.div
        initial={reduce ? false : { scaleX: 0, opacity: 0 }}
        animate={{ scaleX: 1, opacity: 1 }}
        transition={{ duration: duration.base, ease: ease.standard, delay: 0.1 }}
        style={{ transformOrigin: 'left' }}
        className="mt-5 h-px w-full bg-line-subtle"
      />

      {/* Body — staggered children reveal */}
      <motion.div
        variants={reduce ? undefined : staggerContainer(0.06)}
        initial={reduce ? undefined : 'hidden'}
        animate={reduce ? undefined : 'show'}
        className="mt-7 space-y-6"
      >
        {children}
      </motion.div>
    </div>
  );
}
