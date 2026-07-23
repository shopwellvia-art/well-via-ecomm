import {
  Sparkles,
  Heart,
  Leaf,
  ShieldCheck,
  HeartHandshake,
  TrendingUp,
  Gift,
  Globe,
  Mail,
  Phone,
  MessageCircle,
  MapPin,
  Star,
  Truck,
  Award,
  Users,
  Building2,
  Newspaper,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Icon registry — single source of truth for icon-name → component rendering.
// Mirrors the string names stored in the site-pages document.
// ---------------------------------------------------------------------------

export const PAGE_ICONS = {
  Sparkles,
  Heart,
  Leaf,
  ShieldCheck,
  HeartHandshake,
  TrendingUp,
  Gift,
  Globe,
  Mail,
  Phone,
  MessageCircle,
  MapPin,
  Star,
  Truck,
  Award,
  Users,
  Building2,
  Newspaper,
};

/** Resolve an icon name to a component, falling back to Sparkles. */
export function resolvePageIcon(name) {
  return PAGE_ICONS[name] || Sparkles;
}

/** Names offered in admin icon dropdowns. */
export const PAGE_ICON_NAMES = Object.keys(PAGE_ICONS);

// ---------------------------------------------------------------------------
// Canonical defaults — mirrors backend `DEFAULT_SITE_PAGES`. Used as the
// instant render source before the network responds and as the admin reset.
// ---------------------------------------------------------------------------

const REGISTERED_ADDRESS = [
  'Lumen Internet Pvt. Ltd.,',
  'Buildings Alyssa, Begonia & Clove Embassy Tech Village,',
  'Outer Ring Road, Devarabeesanahalli Village,',
  'Bengaluru, 560103, Karnataka, India',
];

export const SITE_PAGES_DEFAULTS = {
  about: {
    enabled: true,
    hero: {
      eyebrow: 'Our Story',
      title: 'We make modern essentials, thoughtfully sourced',
      subtitle:
        "Lumen began with a simple idea — beautifully made everyday products shouldn't cost the earth, or the planet.",
    },
    intro: [
      'Founded in 2007, Lumen set out to reimagine how everyday essentials are designed, made and delivered. What started as a small team obsessing over materials is now a community of makers, designers and customers who care about quality that lasts.',
      "Today we serve millions of customers across the country, but our promise hasn't changed: thoughtful products, honest pricing, and service from real humans who actually want to help.",
    ],
    stats: [
      { value: '10M+', label: 'Happy customers' },
      { value: '120+', label: 'Cities served' },
      { value: '4.8/5', label: 'Average rating' },
      { value: '2007', label: 'Founded' },
    ],
    values: [
      {
        icon: 'Heart',
        title: 'Customer obsessed',
        text: 'Every decision starts with the people we serve. Real support, fair returns, no fine print.',
      },
      {
        icon: 'Leaf',
        title: 'Sustainably sourced',
        text: "We choose responsible materials and partners, and we're honest about where we can do better.",
      },
      {
        icon: 'ShieldCheck',
        title: 'Built to last',
        text: "We'd rather make fewer things well than chase fast, disposable trends.",
      },
      {
        icon: 'Sparkles',
        title: 'Thoughtful design',
        text: 'Form and function in balance — products that feel considered in the hand and in the home.',
      },
    ],
    mission: {
      heading: 'Our mission',
      body: "To make beautifully designed, responsibly made essentials accessible to everyone — and to treat every customer, maker and community we touch with genuine care.\n\nWe're building a company we'd be proud to buy from ourselves.",
    },
  },

  contact: {
    enabled: true,
    // hero: {
    //   eyebrow: 'Contact Us',
    //   title: "We'd love to hear from you",
    //   subtitle: 'Questions about an order, a product, or a partnership? Our team is here to help.',
    // },
    // intro:
    //   "Reach us through any of the channels below kavya, or drop us a message and 
    //   we'll get back within one business day.",
    methods: [
      { icon: 'Mail', title: 'Email us', detail: 'support@lumen.com', href: 'mailto:support@lumen.com' },
      { icon: 'Phone', title: 'Call us', detail: '044-4561 4700', href: 'tel:+914445614700' },
      { icon: 'MessageCircle', title: 'Live chat', detail: 'Mon–Sat, 9am – 8pm IST', href: '' },
      { icon: 'MapPin', title: 'Visit us', detail: 'Embassy Tech Village, Bengaluru', href: '' },
    ],
    form: {
      heading: 'Send us a message',
      note: 'We typically reply within one business day.',
      success: "Thanks for reaching out — we'll be in touch shortly.",
    },
    offices: [
      {
        city: 'Bengaluru (HQ)',
        lines: [
          'Buildings Alyssa, Begonia & Clove',
          'Embassy Tech Village, Outer Ring Road,',
          'Devarabeesanahalli, Bengaluru 560103',
        ],
      },
      {
        city: 'Mumbai',
        lines: ['Level 12, Trade Centre,', 'Bandra Kurla Complex,', 'Mumbai 400051'],
      },
    ],
  },

  careers: {
    enabled: true,
    hero: {
      eyebrow: 'Careers',
      title: 'Build the future of everyday essentials',
      subtitle: 'Join a team that cares deeply about craft, customers and each other.',
    },
    intro:
      "We're a curious, kind and ambitious bunch. If you want to do the best work of your career alongside people who'll cheer you on, we'd love to meet you.",
    perks: [
      {
        icon: 'HeartHandshake',
        title: 'People first',
        text: 'Generous leave, parental support, and a culture that respects your life outside work.',
      },
      {
        icon: 'TrendingUp',
        title: 'Grow fast',
        text: 'Learning budgets, mentorship and real ownership from day one.',
      },
      { icon: 'Gift', title: 'Great perks', text: 'Health cover, employee discounts and meaningful equity.' },
      {
        icon: 'Globe',
        title: 'Flexible & remote-friendly',
        text: 'Work where you do your best — hybrid by default, fully flexible hours.',
      },
    ],
    openings: [
      {
        title: 'Senior Frontend Engineer',
        department: 'Engineering',
        location: 'Bengaluru / Remote',
        type: 'Full-time',
        url: 'mailto:careers@lumen.com?subject=Senior%20Frontend%20Engineer',
      },
      {
        title: 'Product Designer',
        department: 'Design',
        location: 'Bengaluru',
        type: 'Full-time',
        url: 'mailto:careers@lumen.com?subject=Product%20Designer',
      },
      {
        title: 'Customer Experience Lead',
        department: 'Operations',
        location: 'Remote',
        type: 'Full-time',
        url: 'mailto:careers@lumen.com?subject=Customer%20Experience%20Lead',
      },
    ],
    culture: {
      heading: 'Life at Lumen',
      body: 'We move quickly without losing the plot. We disagree openly, decide clearly, and back each other once we commit.\n\nMost of all, we keep the customer at the center of everything — because the best ideas come from genuinely caring about the people we build for.',
    },
  },

  stories: {
    enabled: true,
    hero: {
      eyebrow: 'Lumen Stories',
      title: 'Ideas, people and behind-the-scenes',
      subtitle: 'Notes from our makers, customers and the journey of building Lumen.',
    },
    intro: 'Long reads, short notes and everything in between.',
    posts: [
      {
        title: "How we source materials we're proud of",
        excerpt: 'A look inside the trips, tests and tough calls behind every Lumen product.',
        image: '',
        category: 'Behind the scenes',
        date: '2026-05-12',
        url: '',
      },
      {
        title: 'Meet the makers: the Aura collection',
        excerpt: 'The designers and craftspeople who brought our flagship line to life.',
        image: '',
        category: 'People',
        date: '2026-04-28',
        url: '',
      },
      {
        title: 'Small changes, big impact: our packaging redesign',
        excerpt: 'How we cut plastic by 60% without compromising on the unboxing.',
        image: '',
        category: 'Sustainability',
        date: '2026-03-09',
        url: '',
      },
    ],
  },

  press: {
    enabled: true,
    hero: {
      eyebrow: 'Press',
      title: 'Lumen in the news',
      subtitle: 'Announcements, media coverage and resources for journalists.',
    },
    intro: 'For interviews, assets or comment, reach our communications team below.',
    releases: [
      { date: '2026-05-20', title: 'Lumen crosses 10 million customers', source: 'Company announcement', url: '' },
      {
        date: '2026-02-14',
        title: 'Lumen launches its most sustainable collection yet',
        source: 'Business Standard',
        url: '',
      },
      {
        date: '2025-11-02',
        title: "Lumen named among the year's fastest-growing D2C brands",
        source: 'The Economic Times',
        url: '',
      },
    ],
    contact: {
      heading: 'Media enquiries',
      email: 'press@lumen.com',
      phone: '044-6741 5800',
    },
    kit_url: '',
  },

  corporate: {
    enabled: true,
    hero: {
      eyebrow: 'Corporate Information',
      title: 'About the company behind Lumen',
      subtitle: 'Governance, leadership and statutory details.',
    },
    sections: [
      {
        heading: 'Company overview',
        body: 'Lumen Internet Pvt. Ltd. operates the Lumen.com storefront and associated brands. We are a private limited company incorporated in India.\n\nThis page brings together the statutory and governance information required under applicable law.',
      },
      {
        heading: 'Compliance & grievance',
        body: 'In accordance with the Consumer Protection (E-Commerce) Rules, our Grievance Officer can be reached at grievance@lumen.com. We endeavour to acknowledge complaints within 48 hours and resolve them within one month.',
      },
    ],
    leadership: [
      { name: 'A. Sharma', title: 'Chief Executive Officer', image: '' },
      { name: 'R. Mehta', title: 'Chief Operating Officer', image: '' },
      { name: 'K. Iyer', title: 'Chief Financial Officer', image: '' },
    ],
    entity: {
      name: 'Lumen Internet Pvt. Ltd.',
      cin: 'U51109KA2026PTC066107',
      address_lines: REGISTERED_ADDRESS,
      email: 'compliance@lumen.com',
      phone: '044-4561 4700',
    },
    downloads: [
      { label: 'Certificate of Incorporation', url: '' },
      { label: 'Terms of Use', url: '/terms' },
      { label: 'Privacy Policy', url: '/privacy' },
    ],
  },
};
