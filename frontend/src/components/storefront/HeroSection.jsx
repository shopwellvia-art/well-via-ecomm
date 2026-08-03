import { Link } from 'react-router-dom';
import WImage from '@/components/storefront/WImage';
import { FlaskConical, Sprout, CandyOff, ShieldCheck } from 'lucide-react';
import { useHeroSlides } from '@/features/hero-slides/hooks.js';

const DEFAULT_HEADING = 'Where daily wellness\nmeets daily cravings!';
const DEFAULT_BODY =
  'Clinically-backed nutrition and daily rituals designed to feel gentle, effective and beautifully simple.';

const TRUST_CHIPS = [
  { label: 'Clinically\nReviewed', Icon: FlaskConical },
  { label: 'Vegan\n& Clean', Icon: Sprout },
  { label: 'No Added\nSugar', Icon: CandyOff },
  { label: 'FSSAI\nCompliant', Icon: ShieldCheck },
];

function scrollToWhy() {
  document
    .getElementById('why-we-exist')
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export default function HeroSection() {
  const { data: slides = [] } = useHeroSlides();

  const slide = slides[0] ?? null;

  const heading = slide?.heading || DEFAULT_HEADING;
  const body = slide?.subtext || DEFAULT_BODY;
  const primaryLabel = slide?.cta_label || 'Shop Rituals';
  const primaryTo = slide?.cta_href || '/products';
  const headingLines = heading.split('\n');

  const slideImage = slide?.image_url ?? null;

  return (
    <section className="relative overflow-hidden grid grid-cols-[1.1fr_0.9fr] lg:grid-cols-[1.05fr_1fr] gap-1 sm:gap-4 lg:gap-6 items-center px-3 sm:px-8 lg:px-16 pt-5 sm:pt-6 lg:pt-[64px] pb-6 sm:pb-8 lg:pb-16 animate-rise">
      
      <div className="text-left order-1 flex flex-col justify-center z-10">
        <img
          src="/gummy-green.png"
          alt=""
          aria-hidden="true"
          className="w-8 sm:w-12 lg:w-16 h-auto -ml-4 -mt-10 lg:-ml-16 lg:-mt-36 mb-1.5 lg:mb-3 pointer-events-none"
        />
        <h1 className="font-cormorant font-normal text-[22px] sm:text-[34px] lg:text-[66px] max-w-[700px] leading-[1.15] -tracking-[0.01em] m-0 mb-2.5 lg:mb-5 text-[#08112C]">
          {headingLines.map((line, i) => (
            <span key={i} className="block whitespace-nowrap">
              {line}
            </span>
          ))}
        </h1>

        <p className="font-cormorant text-[11px] sm:text-[16px] lg:text-[21px] leading-[1.35] lg:leading-[1.55] text-wink/80 max-w-[195px] sm:max-w-[460px] m-0 mb-4 lg:mb-8">
          {body}
        </p>

      {/* CTAs */}
<div className="flex flex-row justify-start gap-1 sm:gap-2 lg:gap-4 mb-5 sm:mb-6 lg:mb-10">
  <Link
    to={primaryTo}
    className="bg-[#08112C] text-white no-underline rounded-[8px] lg:rounded-[10px] px-4 sm:px-10 lg:px-16 py-[4px] sm:py-[5px] lg:py-[8px] font-wserif text-[10px] sm:text-[14px] lg:text-[18px] tracking-wide shadow-[0_12px_28px_-12px_rgba(24,58,46,0.7)] hover:bg-[#010E37]/85 transition-colors whitespace-nowrap"
  >
    {primaryLabel}
  </Link>
  <button
    type="button"
    onClick={scrollToWhy}
    className="bg-transparent text-[#08112C] cursor-pointer border border-[#08112C]/50 rounded-[8px] lg:rounded-[10px] px-4 sm:px-10 lg:px-16 py-[4px] sm:py-[5px] lg:py-[8px] font-wserif text-[10px] sm:text-[14px] lg:text-[18px] tracking-wide hover:border-[#010E37] hover:bg-[#010E37]/5 transition-colors whitespace-nowrap"
  >
    Why Wellvia
  </button>
</div>

        {/* Trust chips */}
        <div className="grid grid-cols-4 gap-1 sm:gap-4 justify-items-center lg:flex lg:flex-wrap lg:justify-start lg:gap-x-10 lg:gap-y-5">
          {TRUST_CHIPS.map(({ label, Icon }) => (
            <div key={label} className="flex flex-col items-center gap-1 lg:gap-2 text-center">
              <span className="w-7 h-7 sm:w-9 sm:h-9 lg:w-11 lg:h-11 rounded-full border border-[#010E37]/30 bg-[#010E37]/5 flex items-center justify-center text-[#010E37]">
                <Icon size={13} strokeWidth={1.6} className="sm:w-[16px] sm:h-[16px] lg:w-[19px] lg:h-[19px]" />
              </span>
              <span className="font-wserif text-[8px] sm:text-[11px] lg:text-[14.5px] leading-[1.15] lg:leading-[1.25] text-wink whitespace-pre-line">
                {label}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="relative flex items-center justify-end lg:justify-start order-2 h-full min-h-[200px] sm:min-h-[280px] lg:min-h-0">

  {/* 1. Grey background glow */}
  <img
    src="/grey-bg.png"
    alt=""
    aria-hidden="true"
    className="absolute -right-4 top-1/2 -translate-y-1/2 w-[130%] max-w-none h-[120%] object-contain pointer-events-none z-0 lg:-left-16 lg:-translate-x-4 lg:right-auto lg:top-[55%] lg:w-[115%] lg:h-[110%] lg:object-cover"
  />
  
  {/* Extra image placed directly ABOVE gummy1 — Moved more towards right and higher up on mobile */}
  <img
    src="/gummy-purple.png"
    alt="Decoration Top"
    className="absolute right-0 -top-16 translate-x-2 w-[50px] sm:w-[85px] lg:-mr-28 lg:-mt-24 lg:w-[110px] lg:translate-x-0 z-20 pointer-events-none"
  />

  {/* 2. Small gummy decoration image — Shifted slightly more left on mobile */}
  <img
    src="/gummy1.png"
    alt="Decoration"
    className="absolute -left-8 sm:-left-16 lg:-left-28 top-[58%] sm:top-[52%] lg:top-[62%] -translate-y-1/2 w-[65px] sm:w-[110px] lg:w-[220px] z-20 pointer-events-none"
  />

  {/* 3. Main hero product image — Shifted slightly towards the right on mobile (-translate-x-1) */}
  <div className="relative z-10 flex items-center justify-end lg:justify-start h-full w-full">
    {slideImage ? (
      <WImage
        src={slideImage}
        alt={slide?.alt || "Wellvia hero"}
        shape="rounded"
        className="w-auto h-full max-h-[195px] sm:max-h-[300px] lg:max-h-none lg:w-full lg:h-auto object-contain max-w-[190px] sm:max-w-[360px] lg:max-w-[560px] -translate-x-1 lg:translate-x-0"
      />
    ) : (
      <img
        src="/homepage1.png"
        alt="Wellvia Beauty Boost and Multivitamin gummies"
        className="w-auto h-full max-h-[195px] sm:max-h-[300px] lg:max-h-none lg:w-full lg:h-auto object-contain max-w-[190px] sm:max-w-[360px] lg:max-w-[560px] -translate-x-1 lg:translate-x-0"
        fetchPriority="high"
      />
    )}
  </div>

</div>
    </section>
  );
}