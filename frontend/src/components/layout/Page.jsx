import { motion, useReducedMotion } from 'framer-motion';
import { duration, ease } from '@/lib/motion.js';
import { cn } from '@/lib/utils.js';

/**
 * Route-level wrapper. Provides the entrance transition and the standard
 * content max-width. Pass `bleed` for full-bleed pages (e.g. the hero).
 *
 * The entrance uses a slightly longer ease curve so landing on a page
 * feels like a cinematic reveal rather than a plain fade.
 */
export function Page({ children, className, bleed = false }) {
  const reduce = useReducedMotion();
  return (
    <motion.main
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: duration.slow, ease: ease.entrance }}
      className={cn(
        !bleed && 'mx-auto w-full max-w-content px-6 py-12',
        className,
      )}
    >
      {children}
    </motion.main>
  );
}
