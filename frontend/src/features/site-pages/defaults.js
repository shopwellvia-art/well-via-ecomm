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
      title: 'Wellness, made simple and honest',
      subtitle:
        "Wellvia began with a simple belief — feeling your best shouldn't be complicated. We craft science-backed wellness essentials that fit effortlessly into everyday life.",
    },
    intro: [
      'Wellvia was founded to reimagine everyday wellness — nutrition and self-care that actually taste good, work as promised, and are easy to stick with. What started with a handful of thoughtfully formulated gummies is now a community that believes small daily habits create lasting change.',
      "Today we help people across India take better care of themselves, but our promise hasn't changed: clean, effective formulas, honest labels, and support from real people who genuinely care about your wellbeing.",
    ],
    stats: [
      { value: '1M+', label: 'Happy customers' },
      { value: '50+', label: 'Wellness formulas' },
      { value: '4.8/5', label: 'Average rating' },
      { value: '100%', label: 'Clean ingredients' },
    ],
    values: [
      {
        icon: 'Heart',
        title: 'Customer obsessed',
        text: 'Every formula starts with the people we serve. Real support, easy returns, and no fine print.',
      },
      {
        icon: 'Leaf',
        title: 'Clean & natural',
        text: "We choose clean, thoughtfully sourced ingredients — no unnecessary fillers, and we're honest about what goes in.",
      },
      {
        icon: 'ShieldCheck',
        title: 'Science-backed',
        text: 'Every product is built on real research and tested for quality and safety you can trust.',
      },
      {
        icon: 'Sparkles',
        title: 'Made to enjoy',
        text: "Wellness should feel like a treat, not a chore — delicious formats you'll actually look forward to.",
      },
    ],
    mission: {
      heading: 'Our mission',
      body: "To make effective, delightful wellness accessible to everyone — and to treat every customer and community we touch with genuine care.\n\nWe're building a wellness brand we'd be proud to use ourselves, every single day.",
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

  privacy: {
    enabled: true,
    hero: {
      eyebrow: 'Legal',
      title: 'Privacy Policy',
      subtitle:
        'How Wellvia collects, uses, and protects the personal information you share with us.',
    },
    updated: 'Last updated: 11 July 2026',
    sections: [
      {
        heading: 'Information we collect',
        body: 'We collect information you provide directly — such as your name, email, phone number, shipping address, and payment details — when you create an account, place an order, or contact us. We also automatically collect limited technical data such as your device, browser, and how you use our site.',
      },
      {
        heading: 'How we use your information',
        body: 'We use your information to process and deliver orders, provide customer support, personalise your experience, send order updates, and — where you have opted in — share offers and wellness content. We never sell your personal data.',
      },
      {
        heading: 'Cookies & tracking',
        body: 'We use cookies and similar technologies to keep you signed in, remember your cart, and understand how our store is used so we can improve it. You can control cookies through your browser settings.',
      },
      {
        heading: 'Sharing & disclosure',
        body: 'We share information only with the partners who help us run our business — payment processors, logistics and delivery providers, and analytics services — and only as needed. We may also disclose information where required by law.',
      },
      {
        heading: 'Data security',
        body: 'We use industry-standard safeguards to protect your data. No method of transmission over the internet is completely secure, but we work hard to protect your information and review our practices regularly.',
      },
      {
        heading: 'Your rights',
        body: 'You may access, correct, or delete your personal information, and opt out of marketing at any time, by updating your account or contacting us at support@shopwellvia.in.',
      },
    ],
  },

  terms: {
    enabled: true,
    hero: {
      eyebrow: 'Legal',
      title: 'Terms & Conditions',
      subtitle:
        'The terms that govern your use of the Wellvia website and the purchases you make with us.',
    },
    updated: 'Last updated: 11 July 2026',
    sections: [
      {
        heading: 'Acceptance of terms',
        body: 'By accessing or using the Wellvia website and placing an order, you agree to be bound by these Terms & Conditions. If you do not agree, please do not use the site.',
      },
      {
        heading: 'Use of the website',
        body: 'You agree to use the site only for lawful purposes and not to misuse it, interfere with its operation, or attempt to access it in any unauthorised way. You are responsible for keeping your account credentials secure.',
      },
      {
        heading: 'Products & pricing',
        body: 'We aim to describe and price every product accurately. Colours, packaging, and availability may vary, and we reserve the right to correct errors, change prices, or update product information at any time before your order is confirmed.',
      },
      {
        heading: 'Health disclaimer',
        body: 'Wellvia products are dietary supplements and are not intended to diagnose, treat, cure, or prevent any disease. Please read the label and consult a qualified healthcare professional before use, especially if you are pregnant, nursing, or on medication.',
      },
      {
        heading: 'Orders & payment',
        body: 'All orders are subject to acceptance and availability. Payment must be completed through our approved payment methods before an order is dispatched. We may cancel any order in the event of suspected fraud or pricing errors.',
      },
      {
        heading: 'Limitation of liability',
        body: 'To the fullest extent permitted by law, Wellvia shall not be liable for any indirect or consequential loss arising from the use of our site or products beyond the value of the order in question.',
      },
      {
        heading: 'Governing law',
        body: 'These terms are governed by the laws of India, and any disputes shall be subject to the exclusive jurisdiction of the courts at our registered office location.',
      },
    ],
  },

  refund: {
    enabled: true,
    hero: {
      eyebrow: 'Customer Care',
      title: 'Refund & Cancellation Policy',
      subtitle: 'How order cancellations, returns, and refunds work at Wellvia.',
    },
    updated: 'Last updated: 11 July 2026',
    sections: [
      {
        heading: 'Order cancellation',
        body: 'You can cancel your order any time before it is dispatched for a full refund. Once an order has shipped, it can no longer be cancelled, but you may be eligible to return it under the terms below.',
      },
      {
        heading: 'Returns & eligibility',
        body: 'As our products are consumable wellness items, returns are accepted only for products that arrive damaged, defective, expired, or incorrect. Requests must be raised within 7 days of delivery with the item unopened and in its original packaging.',
      },
      {
        heading: 'Non-returnable items',
        body: 'For hygiene and safety reasons, opened or used products, and items marked as final sale, cannot be returned unless they were received damaged or defective.',
      },
      {
        heading: 'Refund process & timelines',
        body: 'Once your return is received and inspected, we will notify you of approval. Approved refunds are processed to your original payment method within 5–7 business days.',
      },
      {
        heading: 'Damaged or incorrect items',
        body: 'If you receive a damaged, defective, or wrong item, contact us at support@shopwellvia.in within 48 hours of delivery with your order number and a photo, and we will arrange a replacement or refund at no extra cost.',
      },
    ],
  },

  shipping: {
    enabled: true,
    hero: {
      eyebrow: 'Customer Care',
      title: 'Shipping Policy',
      subtitle: 'Delivery timelines, charges, and coverage for your Wellvia orders.',
    },
    updated: 'Last updated: 11 July 2026',
    sections: [
      {
        heading: 'Order processing',
        body: 'Orders are processed within 1–2 business days. Orders placed on weekends or public holidays are processed on the next business day. You will receive a confirmation once your order is dispatched.',
      },
      {
        heading: 'Delivery timelines',
        body: 'Once dispatched, orders are typically delivered within 3–7 business days depending on your location. Remote areas may take a little longer.',
      },
      {
        heading: 'Shipping charges',
        body: 'Shipping charges, if any, are calculated at checkout based on your order value and delivery location. Orders above the eligible value qualify for free shipping.',
      },
      {
        heading: 'Order tracking',
        body: 'As soon as your order ships, we will email you a tracking link. You can also track your order any time from the "My Orders" section of your account.',
      },
      {
        heading: 'Delivery areas',
        body: 'We currently ship across India. If we are unable to deliver to your pin code, you will be notified at checkout.',
      },
      {
        heading: 'Delays',
        body: 'Occasionally, deliveries may be delayed due to weather, logistics, or events beyond our control. We will keep you informed and do our best to get your order to you quickly.',
      },
    ],
  },
};
