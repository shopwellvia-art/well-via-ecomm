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

/* Slide background presets — used when no backend override is present */
const SLIDE_PRESETS = [
  {
    gradient: 'linear-gradient(105deg,#0d3a8a 0%,#2874F0 58%,#5C97F5 100%)',
    eyebrowBg: 'bg-white/15',
    eyebrowText: 'text-white',
    headingHighlight: 'text-[#FFE11B]',
    ctaVariant: 'white-on-blue', // white bg, accent text
    cta2Variant: 'ghost-white',
  },
  {
    gradient: 'linear-gradient(105deg,#101826 0%,#1f2d45 60%,#324a73 100%)',
    eyebrowBg: 'bg-[#FFE11B]/20',
    eyebrowText: 'text-[#FFE11B]',
    headingHighlight: 'text-[#FFE11B]',
    ctaVariant: 'cta-solid', // orange bg
    cta2Variant: null,
  },
  {
    gradient: 'linear-gradient(105deg,#7a3b00 0%,#c0560f 55%,#FB641E 100%)',
    eyebrowBg: 'bg-white/15',
    eyebrowText: 'text-white',
    headingHighlight: 'text-white',
    ctaVariant: 'white-on-cta', // white bg, cta text
    cta2Variant: null,
  },
];

function SaleSlide({ slide, fallbackImage, slideIndex = 0 }) {
  const reduce = useReducedMotion();
  const saleTarget = useSaleTarget();

  const preset = SLIDE_PRESETS[slideIndex % SLIDE_PRESETS.length];

  const heading = slide.heading ?? 'Up to 60% off on audio, wearables & more';
  const subtext =
    slide.subtext ??
    'Free delivery, 7-day returns and no-cost EMI on top brands — for a limited time.';
  const ctaLabel = slide.cta_label ?? 'Shop the sale';
  const ctaHref = slide.cta_href ?? '/products';

  const eyebrow = slide.eyebrow == null ? 'Mega Season Sale · Live now' : slide.eyebrow;

  const cta2Label = slide.cta2_label == null ? 'New arrivals' : slide.cta2_label;
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

  // CTA button styles per preset variant
  function ctaClass(variant) {
    if (variant === 'white-on-blue')
      return 'inline-flex h-11 items-center gap-2 rounded-sm bg-white px-7 text-sm font-bold uppercase tracking-wide text-accent shadow-md transition-transform hover:-translate-y-0.5';
    if (variant === 'cta-solid')
      return 'inline-flex h-11 items-center gap-2 rounded-sm bg-cta px-7 text-sm font-bold uppercase tracking-wide text-white shadow-md transition-transform hover:-translate-y-0.5';
    if (variant === 'white-on-cta')
      return 'inline-flex h-11 items-center gap-2 rounded-sm bg-white px-7 text-sm font-bold uppercase tracking-wide text-cta shadow-md transition-transform hover:-translate-y-0.5';
    return 'inline-flex h-11 items-center gap-2 rounded-sm bg-white px-7 text-sm font-bold uppercase tracking-wide text-accent shadow-md transition-transform hover:-translate-y-0.5';
  }

  const slideBg = slide.gradient ?? preset.gradient;

  return (
    <div
      className="relative flex items-center overflow-hidden min-h-[224px] sm:min-h-[300px]"
      style={{ background: slideBg }}
    >
      {/* Left — copy */}
      <div className="relative z-10 flex-1 px-6 py-8 sm:px-12 sm:py-12">
        {eyebrow && (
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-xs px-3 py-1 text-[11px] font-semibold uppercase tracking-wide',
              preset.eyebrowBg,
              preset.eyebrowText,
            )}
          >
            <Flame className="size-3 opacity-80" aria-hidden="true" />
            {eyebrow}
          </span>
        )}

        <h2 className="mt-4 max-w-md text-3xl font-bold leading-tight text-white sm:text-[2.6rem]">
          {heading}
        </h2>

        <p className="mt-2 max-w-sm text-sm text-white/85">
          {subtext}
        </p>

        <div className="mt-6 flex flex-wrap gap-3">
          <Link to={ctaHref} className={ctaClass(preset.ctaVariant)}>
            {ctaLabel}
            <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
          {preset.cta2Variant && cta2Label && (
            <Link
              to={cta2Href}
              className="inline-flex h-11 items-center rounded-sm border border-white/45 px-6 text-sm font-semibold text-white transition-colors hover:bg-white/10"
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
                  className="inline-flex items-center gap-1.5 text-xs text-white/80"
                >
                  <Icon className="size-3.5 text-white/70" aria-hidden="true" />
                  {label}
                </li>
              );
            })}
          </ul>
        )}

        {effectiveCountdown && (
          <div className="mt-5 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
            {countdownLabel && (
              <span className="text-[11px] font-semibold uppercase tracking-wide text-white/70">
                {countdownLabel}:
              </span>
            )}
            <SaleCountdown target={effectiveCountdown} tone="light" />
          </div>
        )}
      </div>

      {/* Right — product image (hidden on mobile) */}
      <div className="relative hidden w-[42%] items-center justify-center self-stretch sm:flex">
        {/* Sale badge */}
        {slide.badge_text && (
          <div className="absolute right-8 top-8 z-10 grid size-[88px] place-items-center rounded-full bg-cta text-center text-[11px] font-extrabold uppercase leading-tight text-white shadow-lg">
            <p className="px-1">{slide.badge_text}</p>
          </div>
        )}

        {showImage ? (
          <div style={{ contain: 'layout paint' }}>
            <motion.img
              src={image}
              alt={slide.alt || ''}
              onError={() => setImgFailed(true)}
              className="h-56 w-auto max-w-xs object-contain drop-shadow-xl will-change-transform sm:h-64 lg:h-72"
              animate={reduce ? undefined : { y: [0, -8, 0] }}
              transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
            />
          </div>
        ) : (
          <div className="grid size-40 place-items-center rounded-full bg-white/10 text-5xl font-bold text-white/30">
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
    <div
      className="absolute inset-x-0 bottom-3 z-10 flex items-center justify-center gap-1.5"
      role="tablist"
      aria-label="Slide indicators"
    >
      {Array.from({ length: count }, (_, i) => (
        <button
          key={i}
          role="tab"
          aria-selected={i === active}
          aria-label={`Go to slide ${i + 1}`}
          onClick={() => onGo(i)}
          className={cn(
            'h-1.5 rounded-full transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white',
            i === active
              ? 'w-6 bg-white'
              : 'w-1.5 bg-white/40 hover:bg-white/70',
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
        direction === 'prev' ? 'left-3' : 'right-3',
        'grid size-10 place-items-center rounded-full',
        'bg-white/90 text-ink-primary shadow-sm',
        'border border-line-subtle',
        'transition hover:bg-white hover:shadow-md',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white',
        'disabled:pointer-events-none disabled:opacity-30',
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
        <div className="h-[224px] animate-pulse rounded-sm bg-bg-sunken sm:h-[300px]" />
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
      {/* Carousel container — dots and arrows live inside here */}
      <div className="relative isolate overflow-hidden rounded-sm shadow-sm">
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
                <SaleSlide slide={currentSlide} fallbackImage={fallbackImage} slideIndex={current} />
              ) : (
                <PhotoSlide slide={currentSlide} />
              )}
            </div>
          </motion.div>
        </AnimatePresence>

        {/* Prev / Next arrows — white circular, always visible */}
        {total > 1 && (
          <>
            <NavArrow direction="prev" onClick={handlePrev} />
            <NavArrow direction="next" onClick={handleNext} />
          </>
        )}

        {/* Dot indicators — overlaid at bottom of slide */}
        {total > 1 && (
          <Dots count={total} active={current} onGo={handleGoTo} />
        )}

        {/* Pause / play toggle — small button top-right */}
        {total > 1 && (
          <button
            type="button"
            aria-label={manuallyPaused ? 'Play slideshow' : 'Pause slideshow'}
            onClick={() => setManuallyPaused((mp) => !mp)}
            className="absolute right-14 top-3 z-20 grid size-6 place-items-center rounded-full bg-black/25 text-white transition-colors hover:bg-black/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          >
            {manuallyPaused ? (
              <Play className="size-3" aria-hidden="true" />
            ) : (
              <Pause className="size-3" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
    </section>
  );
}
