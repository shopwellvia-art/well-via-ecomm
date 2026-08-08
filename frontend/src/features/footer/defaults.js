import {
  Truck,
  RotateCcw,
  ShieldCheck,
  Headphones,
  Gift,
  Store,
  Megaphone,
  LifeBuoy,
  Sparkles,
  Mail,
  CreditCard,
  Heart,
  Star,
  Clock,
  Package,
  BadgeCheck,
  Facebook,
  Twitter,
  Youtube,
  Instagram,
  Linkedin,
  Github,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Icon registries
// ---------------------------------------------------------------------------

/** All icons available in the footer, keyed by the string name stored in the
 *  document. This is the single source of truth for icon rendering. */
export const FOOTER_ICONS = {
  // Trust / badge icons
  Truck,
  RotateCcw,
  ShieldCheck,
  Headphones,
  Gift,
  Store,
  Megaphone,
  LifeBuoy,
  Sparkles,
  Mail,
  CreditCard,
  Heart,
  Star,
  Clock,
  Package,
  BadgeCheck,
  // Social icons
  Facebook,
  Twitter,
  Youtube,
  Instagram,
  Linkedin,
  Github,
};

/** Safe fallback when an icon name is unrecognised at render time.
 *  Returns null so nothing renders — avoids a broken icon slot. */
export function FallbackIcon() {
  return null;
}

/** Resolve an icon name to a component, falling back gracefully. */
export function resolveIcon(name) {
  return FOOTER_ICONS[name] || FallbackIcon;
}

// Admin dropdown option lists
export const TRUST_ICON_NAMES = [
  'Truck',
  'RotateCcw',
  'ShieldCheck',
  'Headphones',
  'Gift',
  'LifeBuoy',
  'Sparkles',
  'Mail',
  'CreditCard',
  'Heart',
  'Star',
  'Clock',
  'Package',
  'BadgeCheck',
];

export const SOCIAL_ICON_NAMES = [
  'Facebook',
  'Twitter',
  'Youtube',
  'Instagram',
  'Linkedin',
  'Github',
];

export const BOTTOM_ICON_NAMES = [
  'Store',
  'Megaphone',
  'Gift',
  'LifeBuoy',
  'Star',
  'Heart',
  'Package',
  'BadgeCheck',
  'CreditCard',
];

// ---------------------------------------------------------------------------
// Canonical default document — the live Wellvia footer content, rendered when
// the footer API is unreachable. Must stay in sync with the backend footer
// defaults. The {year} token in copyright is replaced at render time; without
// the token the year is inserted after the © sign.
// ---------------------------------------------------------------------------

export const FOOTER_DEFAULTS = {
  trust_features: [
    { icon: 'Truck', title: 'Free Shipping', sub: 'On orders over ₹500' },
    { icon: 'RotateCcw', title: 'Easy Returns', sub: '30-day return window' },
    { icon: 'ShieldCheck', title: 'Secure Payment', sub: '256-bit SSL encryption' },
    { icon: 'Headphones', title: '24/7 Support', sub: 'Real humans, anytime' },
  ],

  brand: {
    name: 'WELLVIA',
    tagline: 'Wellness Redefined',
    // When set, the storefront shows this uploaded logo image in place of the
    // icon + wordmark in both the navbar and footer.
    logo_url: '',
  },

  newsletter: {
    enabled: true,
    placeholder: 'you@example.com',
    note: 'No spam. Unsubscribe anytime.',
    success: "You're on the list. Welcome to Wellvia.",
  },

  link_columns: [
    {
      title: 'Shop',
      links: [
        { label: 'All Products', to: '/products' },
        { label: 'Best Sellers', to: '/bestsellers' },
        { label: 'New Arrivals', to: '/new-arrivals' },
        { label: 'Combos', to: '/categories' },
        { label: 'Shop by Goal', to: '/products' },
      ],
    },
    {
      title: 'Explore',
      links: [
        { label: 'About Us', to: '/about' },
        { label: 'Blog', to: '/stories' },
        { label: 'FAQs', to: '/contact' },
        { label: 'Contact Us', to: '/contact' },
        { label: 'Track Order', to: '/orders' },
      ],
    },
    {
      title: 'Customer Care',
      links: [
        { label: 'Shipping Policy', to: '/shipping' },
        { label: 'Refund & Cancellation', to: '/refund' },
        { label: 'Terms & Conditions', to: '/terms' },
        { label: 'Privacy Policy', to: '/privacy' },
        { label: 'Contact Us', to: '/contact' },
      ],
    },
  ],

  // Admin-editable address blocks — hidden until real lines are filled in.
  mail_us: {
    heading: 'Mail Us',
    lines: [],
  },

  registered_office: {
    heading: 'Registered Office Address',
    lines: [],
    cin: '',
    phones: [],
  },

  social_links: [
    { icon: 'Facebook', label: 'Facebook', href: 'https://facebook.com/wellvia' },
    { icon: 'Twitter', label: 'Twitter', href: 'https://twitter.com/wellvia' },
    { icon: 'Youtube', label: 'YouTube', href: 'https://youtube.com/wellvia' },
    { icon: 'Instagram', label: 'Instagram', href: 'https://instagram.com/wellvia' },
  ],

  bottom_links: [
    { icon: 'Store', label: 'Become a Seller', to: '/sell' },
    { icon: 'Megaphone', label: 'Advertise', to: '/advertise' },
    { icon: 'Gift', label: 'Gift Cards', to: '/gift-cards' },
    { icon: 'LifeBuoy', label: 'Help Center', to: '/help' },
  ],

  payment_methods: ['VISA', 'Mastercard', 'RuPay', 'UPI', 'AMEX', 'PayPal'],

  copyright: '© Wellvia. All rights reserved.',
};
