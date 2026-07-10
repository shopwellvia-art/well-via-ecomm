/**
 * Lightweight inline SVG icon set used across the Wellvia storefront.
 * All icons are stroke-based and accept { size, stroke, strokeWidth } props.
 * Keeps the storefront dependency-free of a full icon library.
 *
 * Named exports (all used by Phase 1+ components):
 *   SearchIcon, BagIcon, HeartIcon, UserIcon, MenuIcon, CloseIcon,
 *   CheckCircle, Check, LockIcon, TruckIcon, ShieldIcon, SupportIcon,
 *   LeafIcon, MailIcon, Stars
 */

function base(props) {
  return {
    width: props.size || 18,
    height: props.size || 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: props.stroke || 'currentColor',
    strokeWidth: props.strokeWidth || 1.5,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': true,
  };
}

export const SearchIcon = (p) => (
  <svg {...base(p)}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);

export const BagIcon = (p) => (
  <svg {...base(p)}>
    <path d="M6 8h12l-1 12H7L6 8Z" />
    <path d="M9 8a3 3 0 0 1 6 0" />
  </svg>
);

export const HeartIcon = (p) => (
  <svg {...base(p)} fill={p.filled ? 'currentColor' : 'none'}>
    <path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10Z" />
  </svg>
);

export const UserIcon = (p) => (
  <svg {...base(p)}>
    <circle cx="12" cy="8" r="4" />
    <path d="M5 21c0-4 3.5-6 7-6s7 2 7 6" />
  </svg>
);

export const MenuIcon = (p) => (
  <svg {...base(p)}>
    <path d="M3 6h18M3 12h18M3 18h18" />
  </svg>
);

export const CloseIcon = (p) => (
  <svg {...base(p)}>
    <path d="m6 6 12 12M18 6 6 18" />
  </svg>
);

export const CheckCircle = (p) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="9" />
    <path d="m8.5 12 2.3 2.3 4.7-5" />
  </svg>
);

export const Check = (p) => (
  <svg {...base(p)}>
    <path d="m5 13 4 4 10-11" />
  </svg>
);

export const LockIcon = (p) => (
  <svg {...base(p)}>
    <rect x="5" y="11" width="14" height="9" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);

export const TruckIcon = (p) => (
  <svg {...base(p)}>
    <path d="M3 7h12v8H3z" />
    <path d="M15 10h3l3 3v2h-6z" />
    <circle cx="7" cy="17" r="1.6" />
    <circle cx="17.5" cy="17" r="1.6" />
  </svg>
);

export const ShieldIcon = (p) => (
  <svg {...base(p)}>
    <path d="M12 3l7 3v5c0 4-3 7.5-7 9-4-1.5-7-5-7-9V6z" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

export const SupportIcon = (p) => (
  <svg {...base(p)}>
    <path d="M4 18v-5a8 8 0 0 1 16 0v5" />
    <rect x="3" y="14" width="4" height="6" rx="1.5" />
    <rect x="17" y="14" width="4" height="6" rx="1.5" />
  </svg>
);

export const LeafIcon = (p) => (
  <svg {...base(p)}>
    <path d="M11 3C7 6 4 10 4 14a7 7 0 0 0 14 0c0-2-1-4-3-6" />
    <path d="M11 3c2 4 5 5 5 9" />
  </svg>
);

export const MailIcon = (p) => (
  <svg {...base(p)}>
    <rect x="4" y="6" width="16" height="12" rx="2" />
    <path d="m4 8 8 6 8-6" />
  </svg>
);

/** ★★★★★ rating stars — rendered as a styled text span. */
export const Stars = ({ className = '', count = 5 }) => (
  <span className={`text-wgold tracking-[1px] ${className}`} aria-hidden="true">
    {'★'.repeat(Math.max(0, Math.min(5, count)))}
  </span>
);
