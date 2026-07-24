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
// Canonical default document — mirrors the exact current Footer.jsx values.
// The first trust feature sub is updated to use ₹ instead of $.
// The {year} token in copyright is replaced at render time.
// ---------------------------------------------------------------------------

export const FOOTER_DEFAULTS = {
  trust_features: [
    { icon: 'Truck', title: 'Free Shipping', sub: 'On orders over ₹500' },
    { icon: 'RotateCcw', title: 'Easy Returns', sub: '30-day return window' },
    { icon: 'ShieldCheck', title: 'Secure Payment', sub: '256-bit SSL encryption' },
    { icon: 'Headphones', title: '24/7 Support', sub: 'Real humans, anytime' },
  ],

  brand: {
    name: 'Lumen',
    tagline:
      'Modern essentials, thoughtfully sourced. Join our newsletter for early drops and member-only pricing.',
    // When set, the storefront shows this uploaded logo image in place of the
    // icon + wordmark in both the navbar and footer.
    logo_url: '',
  },

  newsletter: {
    enabled: true,
    placeholder: 'you@example.com',
    note: 'No spam. Unsubscribe anytime.',
    success: "You're on the list. Welcome to Lumen.",
  },

  link_columns: [
    {
      title: 'About',
      links: [
        { label: 'Contact Us', to: '/contact' },
        { label: 'About Us', to: '/about' },
        { label: 'Careers', to: '/careers' },
        { label: 'Lumen Stories', to: '/stories' },
        { label: 'Press', to: '/press' },
        { label: 'Corporate Information', to: '/corporate' },
      ],
    },
    {
      title: 'Group',
      links: [
        { label: 'Aura', to: '/brands/aura' },
        { label: 'Voyage', to: '/brands/voyage' },
        { label: 'Forge', to: '/brands/forge' },
      ],
    },
    {
      title: 'Consumer Policy',
      links: [
        { label: 'Shipping Policy', to: '/shipping' },
        { label: 'Refund & Cancellation', to: '/refund' },
        { label: 'Terms & Conditions', to: '/terms' },
        { label: 'Privacy Policy', to: '/privacy' },
        { label: 'Contact Us', to: '/contact' },
      ],
    },
  ],

  mail_us: {
    heading: 'Mail Us',
    lines: [
      'Lumen Internet Pvt. Ltd.,',
      'Buildings Alyssa, Begonia &',
      'Clove Embassy Tech Village,',
      'Outer Ring Road, Devarabeesanahalli Village,',
      'Bengaluru, 560103,',
      'Karnataka, India',
    ],
  },

  registered_office: {
    heading: 'Registered Office Address',
    lines: [
      'Lumen Internet Pvt. Ltd.,',
      'Buildings Alyssa, Begonia &',
      'Clove Embassy Tech Village,',
      'Outer Ring Road, Devarabeesanahalli Village,',
      'Bengaluru, 560103,',
      'Karnataka, India',
    ],
    cin: 'U51109KA2026PTC066107',
    phones: [
      { display: '044-4561 4700', tel: '+914445614700' },
      { display: '044-6741 5800', tel: '+914467415800' },
    ],
  },

  social_links: [
    { icon: 'Facebook', label: 'Facebook', href: 'https://facebook.com/lumen' },
    { icon: 'Twitter', label: 'Twitter', href: 'https://twitter.com/lumen' },
    { icon: 'Youtube', label: 'YouTube', href: 'https://youtube.com/lumen' },
    { icon: 'Instagram', label: 'Instagram', href: 'https://instagram.com/lumen' },
  ],

  bottom_links: [
    { icon: 'Store', label: 'Become a Seller', to: '/sell' },
    { icon: 'Megaphone', label: 'Advertise', to: '/advertise' },
    { icon: 'Gift', label: 'Gift Cards', to: '/gift-cards' },
    { icon: 'LifeBuoy', label: 'Help Center', to: '/help' },
  ],

  payment_methods: ['VISA', 'Mastercard', 'RuPay', 'UPI', 'AMEX', 'PayPal'],

  copyright: '© 2007–{year} Lumen.com',
};
