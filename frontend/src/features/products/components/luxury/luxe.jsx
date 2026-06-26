import { motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { ease, duration } from '@/lib/motion.js';

/**
 * Shared primitives for the luxury product page.
 *
 * Token-driven — no hardcoded colours or glass/glow effects.
 */

/**
 * Scroll reveal — fades + rises its children the first time they enter the
 * viewport. Collapses to a plain wrapper when the user prefers reduced motion.
 */
export function Reveal({ children, className, delay = 0, y = 24, as = 'div' }) {
  const reduce = useReducedMotion();
  const MotionTag = motion[as] || motion.div;

  if (reduce) {
    const Tag = as;
    return <Tag className={className}>{children}</Tag>;
  }

  return (
    <MotionTag
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-80px' }}
      transition={{ duration: duration.slow, ease: ease.entrance, delay }}
    >
      {children}
    </MotionTag>
  );
}

/** Section heading with a small gold eyebrow — used between page sections. */
export function SectionHeading({ eyebrow, title, className }) {
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {eyebrow && (
        <span className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-wgold">
          <span className="h-px w-6 bg-wgold/50" aria-hidden="true" />
          {eyebrow}
        </span>
      )}
      <h2 className="text-h2 font-wserif text-wink text-balance">{title}</h2>
    </div>
  );
}
