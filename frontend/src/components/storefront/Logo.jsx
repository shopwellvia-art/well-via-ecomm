import { Link } from 'react-router-dom';
import { mediaUrl } from '@/lib/utils';
import { useStorefrontConfigWithDefaults } from '@/features/storefront-config/hooks.js';

/**
 * LeafMark — the Wellvia botanical SVG mark.
 * Extracted so Header mobile row can use it without wrapping a <Link>.
 */
export function LeafMark({ size = 40, dot = true, color = '#B49A63' }) {
  return (
    <svg
      width={size}
      height={size * 0.75}
      viewBox="0 0 48 36"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M24 33C14 22 6 18 4 6c12 2 18 10 20 20 2-10 8-18 20-20-2 12-10 16-20 27Z"
        fill={color}
      />
      {dot && <circle cx="40.5" cy="5" r="2.6" fill={color} />}
    </svg>
  );
}

/**
 * Logo — Wellvia wordmark.
 *
 * Brand identity comes from the admin storefront config: an uploaded
 * `logo_url` replaces the mark + wordmark with the image, otherwise the
 * LeafMark renders alongside the configured brand name.
 *
 * Props:
 *   size     'lg' | 'md' | 'sm'  (default 'lg')
 *   stacked  true = leaf above wordmark; false = side-by-side  (default true)
 *   to       Link target  (default '/')
 */
export default function Logo({
  size = 'lg',
  stacked = true,
  to = '/',
  // On the dark-green header the mark + wordmark render in cream/gold.
  markColor,
  textClassName = 'text-wgreen',
}) {
  const { config } = useStorefrontConfigWithDefaults();

  const fontSize =
    size === 'lg' ? 'text-[25px]' : size === 'md' ? 'text-[18px]' : 'text-[13px]';
  const tracking =
    size === 'lg' ? 'tracking-[0.26em]' : 'tracking-[0.2em]';
  const markSize = size === 'lg' ? 40 : 24;
  const paddingLeft = size === 'lg' ? '0.26em' : '0.2em';
  // An uploaded logo is usually a full lockup — mark ABOVE wordmark above
  // tagline — so it needs far more height than a bare wordmark to stay legible.
  // The mark was capped at 36px while the nav beside it ran at 18px, which put
  // the wordmark inside the lockup at roughly 5px and inverted the hierarchy:
  // the menu read louder than the brand.
  //
  // These boxes look large because a lockup export is mostly margin — the
  // current one is 306x244 of artwork on a 768x373 canvas, so only 65% of the
  // height and 40% of the width is ink. A 64px box therefore draws a ~42px
  // logo. The surrounding margin is flat brand navy identical to the header
  // band, so the extra box height is invisible and costs no layout.
  // Replacing the asset with a tightly cropped transparent export would make
  // the same box draw a ~50% larger mark.
  const imgMaxHeight =
    size === 'lg' ? 'max-h-[88px]' : size === 'md' ? 'max-h-16' : 'max-h-11';

  return (
    <Link
      to={to}
      className={`flex ${stacked ? 'flex-col' : 'flex-row'} items-center gap-1.5 no-underline focus:outline-none`}
      aria-label={`${config.brand_name} — go to homepage`}
    >
      {config.logo_url ? (
        <img
          src={mediaUrl(config.logo_url)}
          alt={config.brand_name}
          className={`${imgMaxHeight} w-auto object-contain`}
        />
      ) : (
        <>
          <LeafMark size={markSize} color={markColor} />
          <span
            className={`font-display ${fontSize} ${tracking} font-medium ${textClassName}`}
            style={{ paddingLeft }}
          >
            {config.brand_name}
          </span>
        </>
      )}
    </Link>
  );
}
