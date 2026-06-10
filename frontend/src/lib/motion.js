/**
 * Motion tokens — mirror design-system.md §4.
 *
 * All presets are Framer Motion variant objects or whileHover/whileTap values.
 * Import what you need; nothing here auto-runs.
 *
 * Reduced-motion: Framer Motion respects useReducedMotion() automatically when
 * you pass variants to `motion.*` elements. For whileHover/whileTap objects,
 * wrap the consuming component in a useReducedMotion() guard yourself.
 */

// ─── Easing curves ────────────────────────────────────────────────────────────

export const ease = {
  /** General-purpose: fast-out, slow-in. Good for most UI motion. */
  standard: [0.22, 1, 0.36, 1],
  /** Entrance: elements arriving on screen. Slight overshoot feel. */
  entrance: [0.16, 1, 0.3, 1],
  /** Exit: elements leaving. Quick ease-in. */
  exit: [0.4, 0, 1, 1],
  /** Spring-like: for scale/lift interactions. */
  spring: [0.34, 1.56, 0.64, 1],
};

// ─── Duration scale ───────────────────────────────────────────────────────────

export const duration = {
  instant: 0.12,
  fast: 0.2,
  base: 0.32,
  slow: 0.5,
};

// ─── Page / section enter ─────────────────────────────────────────────────────

/** Fade + rise — the default reveal for sections, list items. */
export const fadeUp = {
  hidden: { opacity: 0, y: 16 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: duration.base, ease: ease.entrance },
  },
};

/** Fade in only — no translation. Use when vertical motion feels wrong. */
export const fadeIn = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { duration: duration.base, ease: ease.standard },
  },
};

/** Slide in from the right — for drawers, sidebars, slide-over panels. */
export const slideInRight = {
  hidden: { opacity: 0, x: 24 },
  show: {
    opacity: 1,
    x: 0,
    transition: { duration: duration.base, ease: ease.entrance },
  },
};

/** Scale in from center — for modals, popovers, dropdown menus. */
export const scaleIn = {
  hidden: { opacity: 0, scale: 0.96 },
  show: {
    opacity: 1,
    scale: 1,
    transition: { duration: duration.fast, ease: ease.spring },
  },
};

/** Page entrance — used by the Page wrapper. */
export const pageEnter = {
  initial: { opacity: 0, y: 8 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: duration.base, ease: ease.standard },
  },
};

// ─── Stagger containers ───────────────────────────────────────────────────────

/**
 * Stagger container — children reveal in sequence, capped so nothing drags.
 * Pass a custom stagger (seconds) for denser or sparser grids.
 */
export const staggerContainer = (stagger = 0.07) => ({
  hidden: {},
  show: { transition: { staggerChildren: stagger, delayChildren: 0.05 } },
});

/**
 * List stagger — tighter timing for inline lists and table rows.
 * Children should use `fadeUp` or `fadeIn` as their variant.
 */
export const listStagger = (stagger = 0.04) => ({
  hidden: {},
  show: { transition: { staggerChildren: stagger, delayChildren: 0 } },
});

// ─── Interactive / micro-interaction presets ──────────────────────────────────

/**
 * Hover lift — apply to whileHover on motion.div card wrappers.
 * Pairs with the CSS `.hover-lift` class; use one or the other, not both.
 * Prefer this JS version when you need to coordinate with other motion values.
 *
 * @example
 * <motion.div whileHover={hoverLift} whileTap={tapPress}>
 */
export const hoverLift = {
  y: -4,
  scale: 1.01,
  transition: { duration: duration.fast, ease: ease.standard },
};

/**
 * Tap press — subtle scale-down on pointer-down. Use alongside hoverLift.
 */
export const tapPress = {
  scale: 0.98,
  y: -1,
  transition: { duration: duration.instant, ease: ease.exit },
};

/**
 * Button press — tighter press for inline buttons and icon buttons.
 * Distinct from tapPress: no y-offset, smaller scale delta.
 */
export const buttonPress = {
  scale: 0.96,
  transition: { duration: duration.instant, ease: ease.exit },
};

/**
 * Icon spin — for loading spinners or "refresh" affordances.
 * Use with animate prop; combine with a loop transition.
 *
 * @example
 * <motion.span animate={iconSpin} transition={{ repeat: Infinity, duration: 0.8, ease: 'linear' }}>
 */
export const iconSpin = { rotate: 360 };

/**
 * Attention pulse — brief scale-up to draw the eye to a status change.
 * Use once (no loop) to signal a count increment or success state.
 */
export const attentionPulse = {
  scale: [1, 1.12, 1],
  transition: { duration: 0.4, ease: ease.spring, times: [0, 0.5, 1] },
};

// ─── Hero / cinematic presets ─────────────────────────────────────────────────

/**
 * Hero content reveal — staggered entrance for hero heading + subtext + CTA.
 * Parent uses this as variants; children use `fadeUp`.
 *
 * @example
 * <motion.div variants={heroContainer} initial="hidden" animate="show">
 *   <motion.h1 variants={fadeUp}>…</motion.h1>
 *   <motion.p variants={fadeUp}>…</motion.p>
 * </motion.div>
 */
export const heroContainer = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.1, delayChildren: 0.1 },
  },
};

/**
 * Parallax base — pass as `style` prop with a MotionValue from useTransform.
 * This is just the documented convention; actual transform is caller-provided.
 *
 * @example
 * const y = useTransform(scrollY, [0, 500], [0, -80]);
 * <motion.div style={{ y }} className="parallax-layer">
 */
export const parallaxStyle = {}; // placeholder for docs; value is caller-provided
