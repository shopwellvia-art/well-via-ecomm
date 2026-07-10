import { Link } from 'react-router-dom';
import WImage from '@/components/storefront/WImage';
import {
  CheckCircle,
  CloseIcon,
  LeafIcon,
  ShieldIcon,
} from '@/components/storefront/Icons';
import { useHeroSlides } from '@/features/hero-slides/hooks.js';

/**
 * HeroSection — Wellvia home hero ("Where daily wellness meets daily cravings!").
 *
 * Wiring:
 *   - useHeroSlides(): when the admin has published slides, the first slide's
 *     heading / subtext / CTA / image override the static mockup copy.
 *     With no slides (dev default) the static hero below renders as designed.
 *   - Right-hand imagery: slide image wins; else the static brand shot
 *     extracted from the design file (/public/home/hero-products.jpg).
 *   - Primary CTA → /products. Secondary CTA smooth-scrolls to #why-we-exist.
 */

const DEFAULT_HEADING = 'Where daily wellness\nmeets daily cravings!';
const DEFAULT_BODY =
  'Clinically-backed nutrition and daily rituals designed to feel gentle, effective and beautifully simple.';

const TRUST_CHIPS = [
  { label: 'Clinically\nReviewed', Icon: CheckCircle },
  { label: 'Vegan\n& Clean', Icon: LeafIcon },
  { label: 'No Added\nSugar', Icon: CloseIcon },
  { label: 'FSSAI\nCompliant', Icon: ShieldIcon },
];

function scrollToWhy() {
  document
    .getElementById('why-we-exist')
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export default function HeroSection() {
  const { data: slides = [] } = useHeroSlides();

  // First published slide (if any) overrides the static mockup copy.
  const slide = slides[0] ?? null;

  const heading = slide?.heading || DEFAULT_HEADING;
  const body = slide?.subtext || DEFAULT_BODY;
  const primaryLabel = slide?.cta_label || 'Shop Rituals';
  const primaryTo = slide?.cta_href || '/products';
  const headingLines = heading.split('\n');

  // Right-hand imagery: slide image wins; else the static brand shot.
  const slideImage = slide?.image_url ?? null;
  return (
    <section className="grid md:grid-cols-[1.05fr_1fr] gap-8 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 pt-9 lg:pt-[64px] pb-10 lg:pb-16 animate-rise">
      {/* ── Left: headline + copy + CTAs + trust chips ─────────────────── */}
      <div>
        <h1 className="font-wserif font-semibold text-[clamp(40px,5.2vw,66px)] leading-[1.06] -tracking-[0.01em] m-0 mb-[20px] text-wgreen">
          {headingLines.map((line, i) => (
            <span key={i}>
              {line}
              {i < headingLines.length - 1 && <br />}
            </span>
          ))}
        </h1>

        <p className="font-wserif text-[clamp(17px,1.5vw,21px)] leading-[1.55] text-wink/80 max-w-[460px] m-0 mb-8">
          {body}
        </p>

        {/* CTAs */}
        <div className="flex flex-wrap gap-4 mb-10">
          <Link
            to={primaryTo}
            className="bg-wgreen text-white no-underline rounded-[10px] px-9 py-[13px] font-wserif text-[18px] tracking-wide shadow-[0_12px_28px_-12px_rgba(24,58,46,0.7)] hover:bg-wgreen-dark transition-colors"
          >
            {primaryLabel}
          </Link>
          <button
            type="button"
            onClick={scrollToWhy}
            className="bg-transparent text-wgreen cursor-pointer border border-wgreen/50 rounded-[10px] px-9 py-[13px] font-wserif text-[18px] tracking-wide hover:border-wgreen hover:bg-wgreen/5 transition-colors"
          >
            Why Wellvia
          </button>
        </div>

        {/* Trust chips — icon above a two-line label, per the mockup */}
        <div className="flex flex-wrap gap-x-10 gap-y-5">
          {TRUST_CHIPS.map(({ label, Icon }) => (
            <div key={label} className="flex flex-col items-center gap-2 text-center">
              <span className="w-11 h-11 rounded-full border border-wink/50 flex items-center justify-center text-wink">
                <Icon size={19} strokeWidth={1.4} />
              </span>
              <span className="font-wserif text-[14.5px] leading-[1.25] text-wink whitespace-pre-line">
                {label}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Right: brand composition (extracted from the design file) ───── */}
      <div className="relative flex items-center justify-center">
        {slideImage ? (
          <WImage
            src={slideImage}
            alt={slide?.alt || 'Wellvia hero'}
            shape="rounded"
            className="w-full h-[clamp(320px,40vw,480px)] relative z-[1]"
          />
        ) : (
          /* Static brand shot — two pouches with hand-drawn annotations,
             cropped from zx10R/home&navbar mockup into /public/home/. */
          <img
            src="/home/hero-products.jpg"
            alt="Wellvia Beauty Boost and Multivitamin gummies"
            className="w-full max-w-[560px] h-auto rounded-xl2"
            fetchPriority="high"
          />
        )}
      </div>
    </section>
  );
}
