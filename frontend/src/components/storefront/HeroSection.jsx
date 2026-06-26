import { Link } from 'react-router-dom';
import WImage from '@/components/storefront/WImage';
import TrustBadges from '@/components/storefront/TrustBadges';

/**
 * HeroSection — split text/image hero used by the Home page.
 *
 * All copy is editable via props with sensible wellness-brand defaults.
 * CTAs link to /products (real route) and /about.
 * The hero image is rendered via WImage which handles null/missing gracefully.
 *
 * Props:
 *   eyebrow        — small uppercase gold label above the headline
 *   heading        — main h1; can include <br /> via JSX or split on \n
 *   body           — sub-headline paragraph
 *   primaryLabel   — primary CTA button text
 *   primaryTo      — primary CTA link target
 *   secondaryLabel — secondary CTA button text
 *   secondaryTo    — secondary CTA link target
 *   heroImage      — src string for the hero photo (null = gradient fallback)
 *   heroAlt        — alt text for the hero image
 *   ratingScore    — displayed score in the floating review card
 *   reviewCount    — displayed count string in the floating review card
 */
export default function HeroSection({
  eyebrow = 'Clinically-backed wellness',
  heading = 'Wellness, Refined\nfor Everyday Living',
  body = 'Clinically backed nutrition and daily wellness rituals designed to feel gentle, effective, and beautifully simple.',
  primaryLabel = 'Shop Rituals',
  primaryTo = '/products',
  secondaryLabel = 'Why Wellvia?',
  secondaryTo = '/about',
  heroImage = null,
  heroAlt = 'Wellvia wellness product lifestyle photo',
  ratingScore = '4.8',
  reviewCount = '12,400+',
}) {
  const headingLines = heading.split('\n');

  return (
    <section className="grid md:grid-cols-[1.05fr_1fr] gap-6 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 pt-9 lg:pt-[72px] pb-10 lg:pb-16 animate-rise">
      {/* Left: copy + CTAs */}
      <div>
        {/* Eyebrow */}
        <div className="inline-flex items-center gap-2 text-[11px] tracking-[0.22em] uppercase text-wgold mb-[22px]">
          <span className="w-6 h-px bg-wgold" />
          {eyebrow}
        </div>

        {/* Headline */}
        <h1 className="font-wserif font-medium text-[clamp(42px,5.6vw,72px)] leading-[1.02] -tracking-[0.01em] m-0 mb-[22px] text-wink">
          {headingLines.map((line, i) => (
            <span key={i}>
              {line}
              {i < headingLines.length - 1 && <br />}
            </span>
          ))}
        </h1>

        {/* Body */}
        <p className="text-[clamp(15px,1.3vw,17.5px)] leading-[1.7] text-wmuted max-w-[430px] m-0 mb-8 font-light">
          {body}
        </p>

        {/* CTAs */}
        <div className="flex flex-wrap gap-3.5 mb-9">
          <Link
            to={primaryTo}
            className="bg-wgreen text-white no-underline rounded-full px-[34px] py-4 text-[14px] tracking-wide shadow-[0_12px_28px_-12px_rgba(24,58,46,0.7)] hover:bg-wgreen-dark transition-colors"
          >
            {primaryLabel}
          </Link>
          <Link
            to={secondaryTo}
            className="bg-transparent text-wink no-underline border border-wline rounded-full px-8 py-4 text-[14px] tracking-wide hover:border-wgreen transition-colors"
          >
            {secondaryLabel}
          </Link>
        </div>

        {/* Trust badges row */}
        <TrustBadges />
      </div>

      {/* Right: hero image + floating rating card */}
      <div className="relative">
        {/* Soft gold radial glow behind the image */}
        <div
          className="absolute -right-[6%] -bottom-[8%] w-[62%] h-[78%] z-0 rounded-full"
          style={{
            background:
              'radial-gradient(circle at 50% 40%, rgba(180,154,99,0.16), transparent 70%)',
          }}
        />

        <WImage
          src={heroImage}
          alt={heroAlt}
          shape="rounded"
          className="w-full h-[clamp(360px,42vw,520px)] relative z-[1]"
        />

        {/* Floating review card */}
        <div className="absolute -left-3.5 bottom-6 z-[2] bg-wcard border border-wline rounded-2xl px-[17px] py-[13px] shadow-[0_18px_40px_-20px_rgba(40,30,10,0.4)] flex items-center gap-3">
          <span className="font-wserif text-[30px] text-wgreen leading-none">
            {ratingScore}
          </span>
          <div className="text-[11px] leading-[1.5] text-wmuted">
            <span className="text-wgold tracking-[1px]">★★★★★</span>
            <br />
            {reviewCount} reviews
          </div>
        </div>
      </div>
    </section>
  );
}
