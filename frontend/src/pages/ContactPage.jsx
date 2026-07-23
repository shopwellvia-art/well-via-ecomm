import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { MapPin, Mail, Phone } from 'lucide-react';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';

// ── Decorative hero leaves (inline SVG, no image asset required) ───────────

function HeroLeaves({ className, flip = false }) {
  return (
    <svg
      viewBox="0 0 160 160"
      className={className}
      style={flip ? { transform: 'scaleX(-1)' } : undefined}
      aria-hidden="true"
      focusable="false"
    >
      <g opacity="0.5">
        <path
          d="M0 20 C 40 10, 70 35, 75 80 C 78 110, 65 135, 40 155"
          fill="none"
          stroke="#5F7A52"
          strokeWidth="2"
          strokeLinecap="round"
        />
        {[
          { x: 20, y: 40, rot: 25 },
          { x: 45, y: 65, rot: -20 },
          { x: 30, y: 95, rot: 40 },
          { x: 55, y: 115, rot: -10 },
        ].map((l, i) => (
          <ellipse
            key={i}
            cx={l.x}
            cy={l.y}
            rx="14"
            ry="6"
            fill="#7C9A6C"
            opacity="0.55"
            transform={`rotate(${l.rot} ${l.x} ${l.y})`}
          />
        ))}
      </g>
    </svg>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ContactPage() {
  // Site-pages config drives the enabled flag, info-card details and form copy.
  const { data, isLoading } = useSitePages();

  if (isLoading && !data) {
    return (
      <div className="min-h-[70vh] px-5 sm:px-10 lg:px-14 py-10">
        <div className="max-w-[1050px] mx-auto animate-pulse space-y-6">
          <div className="h-[220px] rounded-b-3xl bg-wline/40" />
          <div className="h-[180px] max-w-[560px] mx-auto rounded-xl2 bg-wline/40" />
          <div className="h-[420px] max-w-[560px] mx-auto rounded-xl2 bg-wline/40" />
        </div>
      </div>
    );
  }

  const page = { ...SITE_PAGES_DEFAULTS.contact, ...data?.contact };

  if (page.enabled === false) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center px-6 py-20">
        <div className="text-center">
          <p className="font-wserif text-4xl leading-tight text-wink">Contact Us</p>
          <p className="mt-4 text-base text-wmuted">
            This page isn&apos;t available right now. Please check back soon.
          </p>
        </div>
      </div>
    );
  }

  // Contact details from the admin-managed contact methods (with fallbacks).
  const methods = page.methods ?? [];
  const findDetail = (...keys) =>
    methods.find((m) =>
      keys.some(
        (k) =>
          (m.icon || '').toLowerCase().includes(k) ||
          (m.title || '').toLowerCase().includes(k),
      ),
    )?.detail;

  const address = findDetail('mappin', 'visit', 'address') || 'Bengaluru, Karnataka, India';
  const email = findDetail('mail', 'email') || 'care@shopwellvia.in';
  const phone = findDetail('phone', 'call') || '+91 90000 00000';

  const hours = page.hours || 'Monday – Saturday (9:00 AM – 6:00 PM IST)';
  const responseNote =
    page.responseNote || 'We aim to respond to all queries within 24–48 business hours.';

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="pb-16"
    >
      {/* Hero banner */}
     <div className="relative overflow-hidden min-h-[180px] sm:min-h-[300px] px-6 py-10 sm:py-20 flex flex-col justify-center items-center text-center">
  <img
    src="/hero-contact.png"
    alt=""
    className="absolute inset-0 w-full h-full object-cover object-center"
  />

  {/* Optional overlay for better text readability */}
  <div className="absolute inset-0 bg-black/20" />

  {/* <h1 className="relative z-10 font-wserif font-semibold text-[clamp(30px,4vw,42px)] text-white m-0">
    Contact Us
  </h1>

  <p className="relative z-10 mt-3 max-w-md mx-auto font-wserif text-[clamp(15px,1.6vw,18px)] text-white">
    {page.intro ||
      "We're here to help! Reach out for any queries, feedback or support."}
  </p> */}
</div>

      <div className="max-w-[620px] mx-auto px-5 sm:px-10 mt-10 lg:mt-14">
        <p className="text-center font-wserif text-[19px] text-wink mb-6">
          Feel free to reach out to us at any time.
        </p>

        {/* Contact details block */}
        <div className="rounded-2xl border border-wline bg-wcard px-6 py-6 sm:px-10 sm:py-8 flex flex-col gap-4">
          {[
            { label: 'Mail id', value: email, Icon: Mail },
            { label: 'Phone', value: phone, Icon: Phone },
            { label: 'Address', value: address, Icon: MapPin },
          ].map(({ label, value, Icon }) => (
            <div key={label} className="flex items-start gap-3 text-[15px]">
              <Icon className="size-[17px] mt-0.5 text-wgreen shrink-0" strokeWidth={1.6} aria-hidden="true" />
              <p className="m-0 text-wink">
                <span className="font-medium">{label} :</span>{' '}
                <span className="text-wmuted">{value}</span>
              </p>
            </div>
          ))}
        </div>

        {/* Support hours */}
        <div className="text-center mt-10">
          <p className="font-wserif text-[18px] text-wink m-0 mb-1">Customer Support Hours:</p>
          <p className="text-[15px] font-medium text-wink m-0">{hours}</p>
          <p className="text-[13px] text-wmuted mt-2 max-w-sm mx-auto">{responseNote}</p>
        </div>

        {/* Sage track-order box */}
        <div className="bg-wsage rounded-xl2 px-5 py-4 mt-10 mb-4 text-center">
          <div className="font-wserif text-[18px] text-[#16301f]">
            Track an order instead?
          </div>
          <Link
            to="/orders"
            className="mt-1 inline-block text-[14px] text-[#08112C] underline underline-offset-2 hover:text-wgreen-dark transition-colors"
          >
            Go to Track Order →
          </Link>
        </div>
      </div>
    </motion.div>
  );
}