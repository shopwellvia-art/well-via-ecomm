import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowLeft, ArrowRight, Flame, Pause, Play } from 'lucide-react';
import { buttonVariants } from '@/components/ui/Button.jsx';
import { cn } from '@/lib/utils.js';
import { duration, ease } from '@/lib/motion.js';
import { useHeroSlides } from '@/features/hero-slides/hooks.js';
import { useBestsellers } from '@/features/products/hooks.js';
import { DEFAULT_PERKS, resolvePerkIcon } from '@/features/hero-slides/perks.js';
import { SaleCountdown, useSaleTarget } from './SaleCountdown.jsx';

const INTERVAL_MS = 5000;

// --- Slide renderers ---

function SaleSlide({ slide, fallbackImage }) {
  const reduce = useReducedMotion();
  const saleTarget = useSaleTarget();

  const heading = slide.heading ?? 'Shopping, refined to a feeling.';
  const subtext =
    slide.subtext ??
    'A premium store built for speed and delight. Curated essentials, fair prices, and a checkout that just works.';
  const ctaLabel = slide.cta_label ?? 'Shop the collection';
  const ctaHref = slide.cta_href ?? '/products';

  const eyebrow = slide.eyebrow == null ? 'Mega season sale is live' : slide.eyebrow;

  const cta2Label = slide.cta2_label == null ? 'Browse new arrivals' : slide.cta2_label;
  const cta2Href = slide.cta2_href || '/products?sort=newest';

  const perks = slide.perks == null ? DEFAULT_PERKS : slide.perks;

  const countdownLabel =
    slide.countdown_label == null
      ? slide._isFallback
        ? 'Mega season sale ends in'
        : 'Sale ends in'
      : slide.countdown_label;

  const image = slide.image_url || fallbackImage;
  const [imgFailed, setImgFailed] = useState(false);
  const showImage = image && !imgFailed;

  const countdownTarget = (() => {
    if (!slide.countdown_end) return null;
    const ms = Date.parse(slide.countdown_end);
    if (!Number.isFinite(ms) || ms <= Date.now()) return null;
    return ms;
  })();
  const effectiveCountdown = countdownTarget ?? (slide._isFallback ? saleTarget : null);

  return (
    <div className="grid grid-cols-1 items-center gap-0 min-h-[240px] lg:grid-cols-2 lg:min-h-[340px]">
      {/* Left — copy */}
      <div className="flex flex-col items-start px-6 py-8 sm:px-8 sm:py-10">
        {eyebrow && (
          <span className="inline-flex items-center gap-1.5 rounded-xs bg-accent/10 px-3 py-1 text-xs font-semibold text-accent">
            <Flame className="size-3.5" aria-hidden="true" />
            {eyebrow}
          </span>
        )}

        <h2 className="mt-4 max-w-sm text-2xl font-bold leading-tight text-ink-primary sm:text-3xl">
          {heading}
        </h2>

        <p className="mt-2 max-w-xs text-sm leading-relaxed text-ink-secondary">
          {subtext}
        </p>

        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:items-center">
          <Link
            to={ctaHref}
            className={cn(buttonVariants({ variant: 'primary', size: 'md' }))}
          >
            {ctaLabel}
            <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
          {cta2Label && (
            <Link
              to={cta2Href}
              className={cn(buttonVariants({ variant: 'outline', size: 'md' }))}
            >
              {cta2Label}
            </Link>
          )}
        </div>

        {perks.length > 0 && (
          <ul className="mt-5 flex flex-wrap gap-x-5 gap-y-1.5">
            {perks.map(({ icon, label }, i) => {
              const Icon = resolvePerkIcon(icon);
              return (
                <li
                  key={`${label}-${i}`}
                  className="inline-flex items-center gap-1.5 text-xs text-ink-secondary"
                >
                  <Icon className="size-3.5 text-accent" aria-hidden="true" />
                  {label}
                </li>
              );
            })}
          </ul>
        )}

        {effectiveCountdown && (
          <div className="mt-5 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
            {countdownLabel && (
              <span className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                {countdownLabel}:
              </span>
            )}
            <SaleCountdown target={effectiveCountdown} />
          </div>
        )}
      </div>

      {/* Right — product image */}
      <div className="relative flex items-center justify-center bg-gradient-to-br from-accent/5 to-bg-elevated min-h-[180px] lg:min-h-0 lg:h-full">
        {/* Sale badge */}
        {slide.badge_text && (
          <div
            className="absolute right-4 top-4 z-10 grid size-20 place-items-center rounded-full text-center text-white shadow-md bg-accent"
          >
            <div>
              <p className="px-1 text-[10px] font-extrabold uppercase leading-tight tracking-wide">
                {slide.badge_text}
              </p>
            </div>
          </div>
        )}

        {showImage ? (
          <div style={{ contain: 'layout paint' }}>
            <motion.img
              src={image}
              alt={slide.alt || ''}
              onError={() => setImgFailed(true)}
              className="h-56 w-auto max-w-xs object-contain drop-shadow-lg will-change-transform sm:h-64 lg:h-72"
              animate={reduce ? undefined : { y: [0, -8, 0] }}
              transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
            />
          </div>
        ) : (
          <div className="grid size-40 place-items-center rounded-full bg-accent/12 text-5xl font-bold text-accent/40">
            ✦
          </div>
        )}
      </div>
    </div>
  );
}

function PhotoSlide({ slide }) {
  const isLight = (slide.text_theme ?? 'light') === 'light';
  const hasOverlay = !!(slide.heading || slide.cta_label);
  const [imgFailed, setImgFailed] = useState(false);
  const showImage = slide.image_url && !imgFailed;

  return (
    <div className="relative w-full aspect-[16/7] sm:aspect-[16/6] overflow-hidden">
      {showImage ? (
        <img
          src={slide.image_url}
          alt={slide.alt || (hasOverlay ? '' : (slide.heading ?? 'Promotional banner'))}
          onError={() => setImgFailed(true)}
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <div className="absolute inset-0 grid place-items-center bg-gradient-to-br from-accent/10 via-bg-elevated to-accent/5">
          <span className="text-sm text-ink-tertiary">Image unavailable</span>
        </div>
      )}

      {hasOverlay && (
        <>
          <div
            aria-hidden="true"
            className={cn(
              'absolute inset-0',
              isLight
                ? 'bg-gradient-to-t from-black/60 via-black/20 to-transparent'
                : 'bg-gradient-to-t from-white/70 via-white/30 to-transparent',
            )}
          />
          <div className="absolute bottom-0 left-0 flex flex-col items-start gap-4 px-6 pb-8 sm:px-8">
            {slide.heading && (
              <h2
                className={cn(
                  'max-w-md text-2xl font-bold leading-tight sm:text-3xl',
                  isLight ? 'text-white drop-shadow-sm' : 'text-ink-primary',
                )}
              >
                {slide.heading}
              </h2>
            )}
            {slide.cta_label && slide.cta_href && (
              <Link
                to={slide.cta_href}
                className={cn(
                  buttonVariants({ variant: isLight ? 'cta' : 'primary', size: 'md' }),
                )}
              >
                {slide.cta_label}
                <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// --- Carousel controls ---

function Dots({ count, active, onGo }) {
  return (
    <div className="flex items-center justify-center gap-1.5 py-2" role="tablist" aria-label="Slide indicators">
      {Array.from({ length: count }, (_, i) => (
        <button
          key={i}
          role="tab"
          aria-selected={i === active}
          aria-label={`Go to slide ${i + 1}`}
          onClick={() => onGo(i)}
          className={cn(
            'h-1.5 rounded-full transition-all duration-300 focus-visible:focus-ring',
            i === active
              ? 'w-5 bg-accent'
              : 'w-1.5 bg-ink-tertiary/40 hover:bg-ink-tertiary/70',
          )}
        />
      ))}
    </div>
  );
}

function NavArrow({ direction, onClick, disabled }) {
  const Icon = direction === 'prev' ? ArrowLeft : ArrowRight;
  const label = direction === 'prev' ? 'Previous slide' : 'Next slide';
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'absolute top-1/2 z-10 -translate-y-1/2',
        direction === 'prev' ? 'left-2' : 'right-2',
        'grid size-11 place-items-center rounded-full',
        'bg-white/90 text-ink-primary shadow-sm',
        'border border-line-subtle',
        'transition-opacity duration-150 hover:opacity-100 hover:shadow-md',
        'focus-visible:focus-ring',
        'disabled:pointer-events-none disabled:opacity-30',
        'opacity-60',
      )}
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}

// --- Main Hero ---

export default function Hero() {
  const reduce = useReducedMotion();
  const { data: slides = [], isLoading } = useHeroSlides();
  const { data: bestsellers = [] } = useBestsellers(4);
  const saleTarget = useSaleTarget();

  const effectiveSlides = slides.length > 0
    ? slides
    : [
        {
          id: '__fallback__',
          kind: 'sale',
          _isFallback: true,
          image_url: bestsellers[0]?.image_url ?? null,
          alt: '',
          heading: null,
          subtext: null,
          badge_text: null,
          cta_label: null,
          cta_href: null,
          countdown_end: null,
          text_theme: 'light',
        },
      ];

  const total = effectiveSlides.length;
  const [current, setCurrent] = useState(0);
  const [paused, setPaused] = useState(false);
  const [manuallyPaused, setManuallyPaused] = useState(false);
  const timerRef = useRef(null);

  const goTo = useCallback(
    (index) => {
      const next = (index + total) % total;
      setCurrent(next);
    },
    [total],
  );

  const goPrev = useCallback(() => goTo(current - 1), [goTo, current]);
  const goNext = useCallback(() => goTo(current + 1), [goTo, current]);

  useEffect(() => {
    if (reduce || paused || manuallyPaused || total <= 1) return;
    timerRef.current = setInterval(() => {
      setCurrent((c) => (c + 1) % total);
    }, INTERVAL_MS);
    return () => clearInterval(timerRef.current);
  }, [reduce, paused, manuallyPaused, total]);

  const [restartKey, setRestartKey] = useState(0);

  useEffect(() => {
    if (reduce || paused || manuallyPaused || total <= 1) return;
    clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setCurrent((c) => (c + 1) % total);
    }, INTERVAL_MS);
    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reduce, paused, manuallyPaused, total, restartKey]);

  function handleGoTo(i) {
    goTo(i);
    setRestartKey((k) => k + 1);
  }

  function handlePrev() {
    goPrev();
    setRestartKey((k) => k + 1);
  }

  function handleNext() {
    goNext();
    setRestartKey((k) => k + 1);
  }

  const currentSlide = effectiveSlides[current] ?? effectiveSlides[0];
  const fallbackImage = bestsellers[0]?.image_url ?? null;

  if (isLoading) {
    return (
      <section className="mx-auto mt-3 max-w-content px-4 sm:px-6" aria-label="Featured promotions">
        <div className="h-[340px] animate-pulse rounded-sm bg-bg-sunken" />
      </section>
    );
  }

  return (
    <section
      className="mx-auto mt-3 max-w-content px-4 sm:px-6"
      aria-label="Featured promotions"
      aria-roledescription="carousel"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="relative isolate overflow-hidden rounded-sm border border-line-subtle bg-gradient-to-r from-accent/4 to-bg-elevated shadow-sm">
        {/* Slides — crossfade via AnimatePresence */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={currentSlide.id}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: duration.base, ease: ease.standard }}
            aria-live={paused || manuallyPaused ? 'polite' : 'off'}
            aria-atomic="true"
          >
            <div
              role="group"
              aria-roledescription="slide"
              aria-label={`Slide ${current + 1} of ${total}`}
            >
              {(currentSlide.kind === 'sale' || currentSlide._isFallback) ? (
                <SaleSlide slide={currentSlide} fallbackImage={fallbackImage} />
              ) : (
                <PhotoSlide slide={currentSlide} />
              )}
            </div>
          </motion.div>
        </AnimatePresence>

        {/* Prev / Next arrows */}
        {total > 1 && (
          <>
            <NavArrow direction="prev" onClick={handlePrev} />
            <NavArrow direction="next" onClick={handleNext} />
          </>
        )}
      </div>

      {/* Dot indicators + pause/play toggle */}
      {total > 1 && (
        <div className="flex items-center justify-center gap-3 py-2">
          <Dots count={total} active={current} onGo={handleGoTo} />
          <button
            type="button"
            aria-label={manuallyPaused ? 'Play slideshow' : 'Pause slideshow'}
            onClick={() => setManuallyPaused((mp) => !mp)}
            className="grid size-6 place-items-center rounded-full text-ink-tertiary transition-colors hover:text-ink-primary focus-visible:focus-ring"
          >
            {manuallyPaused ? (
              <Play className="size-3.5" aria-hidden="true" />
            ) : (
              <Pause className="size-3.5" aria-hidden="true" />
            )}
          </button>
        </div>
      )}
    </section>
  );
}
