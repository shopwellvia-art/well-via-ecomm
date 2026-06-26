import { Link } from 'react-router-dom';

// Original WELLVIA leaf wordmark (not copied from any reference brand).
export function LeafMark({ size = 40, dot = true, color = '#B49A63' }) {
  return (
    <svg width={size} height={size * 0.75} viewBox="0 0 48 36" fill="none" aria-hidden>
      <path
        d="M24 33C14 22 6 18 4 6c12 2 18 10 20 20 2-10 8-18 20-20-2 12-10 16-20 27Z"
        fill={color}
      />
      {dot && <circle cx="40.5" cy="5" r="2.6" fill={color} />}
    </svg>
  );
}

export default function Logo({ size = 'lg', stacked = true, to = '/' }) {
  const fontSize = size === 'lg' ? 'text-[25px]' : size === 'md' ? 'text-[18px]' : 'text-[13px]';
  const tracking = size === 'lg' ? 'tracking-[0.26em]' : 'tracking-[0.2em]';
  return (
    <Link
      to={to}
      className={`flex ${stacked ? 'flex-col' : 'flex-row'} items-center gap-1.5 no-underline`}
    >
      <LeafMark size={size === 'lg' ? 40 : 24} />
      <span
        className={`font-display ${fontSize} ${tracking} font-medium text-green`}
        style={{ paddingLeft: size === 'lg' ? '0.26em' : '0.2em' }}
      >
        WELLVIA
      </span>
    </Link>
  );
}
