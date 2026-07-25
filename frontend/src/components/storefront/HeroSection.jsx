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
    <section className="grid grid-cols-[1fr_0.85fr] lg:grid-cols-[1.05fr_1fr] gap-3 lg:gap-14 items-stretch lg:items-center px-4 sm:px-8 lg:px-16 pt-6 lg:pt-[64px] pb-8 lg:pb-16 animate-rise">
      {/* ── Left: headline + copy + CTAs + trust chips ─────────────────── */}
      <div className="text-left order-1 flex flex-col justify-center">
        <h1 className="font-cormorant font-normal text-[26px] sm:text-[34px] lg:text-[66px] max-w-[700px] 
        leading-[1.1] -tracking-[0.01em] m-0 mb-3 lg:mb-5 text-[#08112C]">
          {headingLines.map((line, i) => (
            <span key={i}  className="block whitespace-nowrap">
              {line}
              {i < headingLines.length - 1 && <br />}
            </span>
          ))}
        </h1>

        <p className="font-cormorant text-[13px] sm:text-[16px] lg:text-[21px] leading-[1.4] lg:leading-[1.55] text-wink/80 max-w-[460px] m-0 mb-5 lg:mb-8">
          {body}
        </p>

        {/* CTAs */}
        <div className="flex flex-row justify-start gap-2 lg:gap-4 mb-6 lg:mb-10">
          <Link
  to={primaryTo}
  className="bg-[#08112C] text-white no-underline rounded-[8px] lg:rounded-[10px] px-4 sm:px-6 lg:px-12 py-[8px] lg:py-[13px] font-wserif text-[12px] sm:text-[14px] lg:text-[18px] tracking-wide shadow-[0_12px_28px_-12px_rgba(24,58,46,0.7)] hover:bg-[#010E37]/85 transition-colors whitespace-nowrap"
>
            {primaryLabel}
          </Link>
          <button
  type="button"
  onClick={scrollToWhy}
  className="bg-transparent text-[#08112C] cursor-pointer border border-[#08112C]/50 rounded-[8px] lg:rounded-[10px] px-4 sm:px-6 lg:px-12 py-[8px] lg:py-[13px] font-wserif text-[12px] sm:text-[14px] lg:text-[18px] tracking-wide hover:border-[#010E37] hover:bg-[#010E37]/5 transition-colors whitespace-nowrap"
>
  Why Wellvia
</button>
        </div>

        {/* Trust chips — single row on mobile too, per the mockup */}
        <div className="grid grid-cols-4 gap-2 sm:gap-4 justify-items-center lg:flex lg:flex-wrap lg:justify-start lg:gap-x-10 lg:gap-y-5">
         {TRUST_CHIPS.map(({ label, Icon }) => (
  <div key={label} className="flex flex-col items-center gap-1 lg:gap-2 text-center">
    <span className="w-8 h-8 sm:w-9 sm:h-9 lg:w-11 lg:h-11 rounded-full border border-[#010E37]/30 bg-[#010E37]/5 flex items-center justify-center text-[#010E37]">
      <Icon size={14} strokeWidth={1.6} className="sm:w-[16px] sm:h-[16px] lg:w-[19px] lg:h-[19px]" />
    </span>
    <span className="font-wserif text-[9px] sm:text-[11px] lg:text-[14.5px] leading-[1.2] lg:leading-[1.25] text-wink whitespace-pre-line">
      {label}
    </span>
  </div>
))}
        </div>
      </div>

      {/* ── Right: brand composition ───── */}
       <div className="relative flex items-center justify-center order-2 h-full max-h-[220px] sm:max-h-[300px] lg:max-h-none">
        {slideImage ? (
          <WImage
            src={slideImage}
            alt={slide?.alt || 'Wellvia hero'}
            shape="rounded"
             className="w-auto h-full max-h-[220px] sm:max-h-[300px] lg:max-h-none lg:w-full lg:h-auto object-contain max-w-[340px] sm:max-w-[420px] lg:max-w-[560px] rounded-xl2"
          />
        ) : (
          <img
            src="/homepage1.png"
            alt="Wellvia Beauty Boost and Multivitamin gummies"
             className="w-auto h-full max-h-[220px] sm:max-h-[300px] lg:max-h-none lg:w-full lg:h-auto object-contain max-w-[180px] sm:max-w-[260px] lg:max-w-[560px] rounded-xl2"
            fetchPriority="high"
          />
        )}
      </div>
    </section>
  );
}