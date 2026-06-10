import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { Sparkles, Mail, ArrowRight, Check, Gift, Zap, Percent, LifeBuoy } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { safeUrl } from '@/lib/safeUrl.js';
import { useFooterConfig } from '@/features/footer/hooks.js';
import { FOOTER_DEFAULTS, resolveIcon } from '@/features/footer/defaults.js';

/**
 * Storefront footer — deep indigo surface with ambient depth and 3D icon tiles.
 *
 * Fully data-driven from admin footer config. All motion respects
 * prefers-reduced-motion. Uses design-system tokens where applicable;
 * the dark indigo background is deliberately fixed to ensure the footer
 * reads as a distinct grounding layer in both themes.
 */
export default function Footer() {
  const { data } = useFooterConfig();
  const cfg = { ...FOOTER_DEFAULTS, ...data };
  const reduce = useReducedMotion();
  const year = new Date().getFullYear();
  const copyright = (cfg.copyright || '').replace('{year}', year);

  return (
    <footer
      className="relative isolate mt-24 overflow-hidden text-white"
      style={{ background: 'linear-gradient(180deg,#07091c 0%,#0b0f28 48%,#111530 100%)' }}
    >
      <FooterAura />
      <FloatingOrbs reduce={reduce} />

      <div className="relative z-10 mx-auto max-w-content px-6">
        {cfg.newsletter?.enabled !== false && (
          <NewsletterBand newsletter={cfg.newsletter} brand={cfg.brand} reduce={reduce} />
        )}

        {/* Brand + links */}
        <div className="grid gap-10 py-16 lg:grid-cols-[1.2fr_2.8fr] lg:gap-16">
          {/* Brand column */}
          <div>
            <Link
              to="/"
              className="inline-flex items-center gap-3 rounded-sm focus-visible:focus-ring"
            >
              {cfg.brand?.logo_url ? (
                <img
                  src={cfg.brand.logo_url}
                  alt={cfg.brand?.name || 'Lumen'}
                  className="h-11 w-auto max-w-[200px] object-contain"
                />
              ) : (
                <>
                  <Icon3D reduce={reduce} className="size-11" gradient="from-[#7C7FF5] to-[#B794F4]">
                    <Sparkles className="size-5" aria-hidden="true" />
                  </Icon3D>
                  <span className="text-2xl font-semibold tracking-tight">
                    {cfg.brand?.name || 'Lumen'}
                  </span>
                </>
              )}
            </Link>
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-white/55">
              {cfg.brand?.tagline}
            </p>

            {/* Social */}
            <ul className="mt-5 flex items-center gap-2">
              {cfg.social_links.map(({ href, label, icon }) => {
                const Icon = resolveIcon(icon);
                return (
                  <li key={label}>
                    <a
                      href={safeUrl(href)}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={label}
                      className="grid size-9 place-items-center rounded-full border border-white/12 bg-white/6 text-white/65 transition-[background-color,border-color,color,transform] duration-200 hover:-translate-y-px hover:border-accent-mid/60 hover:bg-white/10 hover:text-white focus-visible:focus-ring"
                    >
                      <Icon className="size-[15px]" aria-hidden="true" />
                    </a>
                  </li>
                );
              })}
            </ul>

            {/* Trust features */}
            <ul className="mt-7 grid grid-cols-2 gap-3">
              {cfg.trust_features.map(({ icon, title, sub }) => {
                const Icon = resolveIcon(icon);
                return (
                  <li key={title} className="flex items-center gap-2.5">
                    <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-white/8 text-accent-mid">
                      <Icon className="size-[15px]" aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold leading-tight text-white">{title}</p>
                      <p className="truncate text-xs text-white/50 leading-tight mt-0.5">{sub}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>

          {/* Link columns + contact */}
          <div className="grid grid-cols-2 gap-x-6 gap-y-10 sm:grid-cols-3 lg:grid-cols-6">
            {cfg.link_columns.map((col) => (
              <LinkColumn key={col.title} title={col.title} links={col.links} />
            ))}

            <div>
              <SectionHeading>{cfg.mail_us?.heading || 'Mail Us'}</SectionHeading>
              <address className="not-italic text-sm leading-6 text-white/55">
                {(cfg.mail_us?.lines || []).map((line) => (
                  <span key={line} className="block">
                    {line}
                  </span>
                ))}
              </address>
            </div>

            <div>
              <SectionHeading>
                {cfg.registered_office?.heading || 'Registered Office'}
              </SectionHeading>
              <address className="not-italic text-sm leading-6 text-white/55">
                {(cfg.registered_office?.lines || []).map((line) => (
                  <span key={line} className="block">
                    {line}
                  </span>
                ))}
                {cfg.registered_office?.cin && (
                  <span className="mt-2 block">CIN: {cfg.registered_office.cin}</span>
                )}
              </address>
              {cfg.registered_office?.phones?.length > 0 && (
                <p className="mt-2 text-sm text-white/55">
                  {cfg.registered_office.phones.map((p, i) => (
                    <span key={p.tel}>
                      <a
                        href={`tel:${p.tel}`}
                        className="rounded-sm text-accent-mid transition-colors hover:text-white focus-visible:focus-ring"
                      >
                        {p.display}
                      </a>
                      {i < cfg.registered_office.phones.length - 1 && (
                        <span className="text-white/30"> / </span>
                      )}
                    </span>
                  ))}
                </p>
              )}
            </div>
          </div>
        </div>

        <Equalizer reduce={reduce} />

        <MemberPromo reduce={reduce} />

        <BottomBar
          paymentMethods={cfg.payment_methods}
          bottomLinks={cfg.bottom_links}
          copyright={copyright}
        />
      </div>
    </footer>
  );
}

/* ---------------------------------- 3D bits --------------------------------- */

/** Glossy gradient tile that floats and tilts in 3D on hover. */
function Icon3D({ children, className, gradient = 'from-[#7C7FF5] to-[#B794F4]', reduce }) {
  return (
    <span className="inline-block [perspective:800px]">
      <motion.span
        className={cn(
          'relative grid place-items-center rounded-2xl bg-gradient-to-br text-white',
          'shadow-[0_12px_30px_-8px_rgba(124,127,245,0.65)]',
          gradient,
          className,
        )}
        style={{ transformStyle: 'preserve-3d' }}
        animate={reduce ? undefined : { y: [0, -6, 0] }}
        transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
        whileHover={reduce ? undefined : { rotateY: 16, rotateX: -10, scale: 1.05 }}
      >
        {/* glossy top highlight */}
        <span
          className="pointer-events-none absolute inset-x-1 top-1 h-2/5 rounded-[14px] bg-white/25 blur-[2px]"
          aria-hidden="true"
        />
        <span className="relative">{children}</span>
      </motion.span>
    </span>
  );
}

/** Ambient lighting: a glow at the top edge + soft colour washes for depth. */
function FooterAura() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-0">
      <div
        className="absolute inset-x-0 top-0 h-80"
        style={{
          background:
            'radial-gradient(60% 100% at 50% 0%, rgba(124,92,255,0.30), transparent 72%)',
        }}
      />
      <div
        className="absolute -left-24 top-44 size-80 rounded-full blur-[130px]"
        style={{ background: 'rgba(99,102,241,0.22)' }}
      />
      <div
        className="absolute -right-20 bottom-24 size-80 rounded-full blur-[130px]"
        style={{ background: 'rgba(236,72,153,0.16)' }}
      />
    </div>
  );
}

/** Glossy, colourful 3D spheres that float in the background. */
function FloatingOrbs({ reduce }) {
  const orbs = [
    { cls: 'left-[4%] top-[15%]', size: 58, color: '#7C7FF5', d: 7 },
    { cls: 'right-[7%] top-[20%]', size: 42, color: '#ec4899', d: 9 },
    { cls: 'left-[33%] bottom-[24%]', size: 50, color: '#38bdf8', d: 8 },
    { cls: 'right-[23%] bottom-[30%]', size: 34, color: '#f59e0b', d: 6 },
    { cls: 'left-[16%] top-[44%]', size: 26, color: '#B794F4', d: 10 },
    { cls: 'right-[40%] top-[12%]', size: 20, color: '#34d399', d: 11 },
  ];
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-0">
      {orbs.map((o, i) => (
        <motion.span
          key={i}
          className={cn('absolute rounded-full', o.cls)}
          style={{
            width: o.size,
            height: o.size,
            background: `radial-gradient(circle at 32% 28%, rgba(255,255,255,0.92), ${o.color} 48%, rgba(8,11,30,0.95) 100%)`,
            boxShadow: `0 12px 30px -6px ${o.color}80, inset -4px -6px 12px rgba(0,0,0,0.35)`,
          }}
          animate={reduce ? undefined : { y: [0, -16, 0], x: [0, 8, 0], rotate: [0, 10, 0] }}
          transition={{ duration: o.d, repeat: Infinity, ease: 'easeInOut' }}
        />
      ))}
    </div>
  );
}

/** Animated equalizer — a subtle, unique audio-wave accent. */
function Equalizer({ reduce }) {
  const bars = [8, 16, 26, 14, 22, 32, 18, 28, 12, 24, 30, 16, 20, 10, 26, 18];
  return (
    <div
      aria-hidden="true"
      className="my-2 flex h-9 items-end justify-center gap-[3px] opacity-50"
    >
      {bars.map((h, i) => (
        <motion.span
          key={i}
          className="w-[3px] rounded-full"
          style={{
            height: h,
            background: 'linear-gradient(to top,#7C7FF5,#ec4899)',
          }}
          animate={reduce ? undefined : { height: [h, Math.min(h * 1.9, 36), h] }}
          transition={{
            duration: 1.1 + (i % 5) * 0.18,
            repeat: Infinity,
            ease: 'easeInOut',
            delay: i * 0.05,
          }}
        />
      ))}
    </div>
  );
}

/* -------------------------------- Newsletter -------------------------------- */

function NewsletterBand({ newsletter = {}, brand = {}, reduce }) {
  return (
    <div className="pt-14">
      <div
        className="relative grid items-center gap-8 overflow-hidden rounded-[24px] border border-white/12 p-8 sm:p-10 lg:grid-cols-[1fr_auto]"
        style={{
          background: 'linear-gradient(118deg,#3836d4 0%,#7241f5 48%,#c240d0 100%)',
          boxShadow:
            '0 24px 80px -20px rgba(114,65,245,0.65), inset 0 1px 0 rgba(255,255,255,0.18)',
        }}
      >
        {/* sheen */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-10 -top-16 size-56 rounded-full bg-white/15 blur-3xl"
        />
        <div className="relative flex items-start gap-5">
          <Icon3D reduce={reduce} className="size-14 shrink-0" gradient="from-[#8b8cf8] to-[#c79bf6]">
            <Mail className="size-6" aria-hidden="true" />
          </Icon3D>
          <div>
            <h2 className="text-2xl font-semibold text-white">
              Stay in the loop with {brand?.name || 'Lumen'}
            </h2>
            <p className="mt-1.5 max-w-md text-sm text-white/70">
              Join thousands of members who get early access, exclusive drops &
              member-only deals.
            </p>
          </div>
        </div>

        <div className="relative">
          <NewsletterForm newsletter={newsletter} brand={brand} />
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-white/80">
            {['No spam', 'Unsubscribe anytime', 'Exclusive deals'].map((t) => (
              <li key={t} className="inline-flex items-center gap-1.5">
                <Check className="size-3.5 text-[#9be8c0]" aria-hidden="true" />
                {t}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function NewsletterForm({ newsletter = {}, brand = {} }) {
  const {
    placeholder = 'you@example.com',
    success = `You're on the list. Welcome to ${brand?.name || 'Lumen'}.`,
  } = newsletter;

  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  function onSubmit(e) {
    e.preventDefault();
    const value = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      setError('Please enter a valid email address.');
      return;
    }
    setError('');
    setSubmitted(true);
    setEmail('');
    window.setTimeout(() => setSubmitted(false), 4000);
  }

  return (
    <form onSubmit={onSubmit} noValidate aria-label="Subscribe to the newsletter">
      <div className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="footer-newsletter" className="sr-only">
          Email address
        </label>
        <div className="relative sm:w-72">
          <Mail
            className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-white/50"
            aria-hidden="true"
          />
          <input
            id="footer-newsletter"
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (error) setError('');
            }}
            placeholder={placeholder}
            autoComplete="email"
            aria-invalid={error ? 'true' : undefined}
            aria-describedby="footer-newsletter-msg"
            className={cn(
              'h-11 w-full rounded-full border bg-white/10 pl-10 pr-4 text-sm text-white',
              'placeholder:text-white/45 backdrop-blur transition-colors duration-200',
              'focus-visible:focus-ring',
              error ? 'border-danger' : 'border-white/15 hover:border-white/30',
            )}
          />
        </div>
        <button
          type="submit"
          className={cn(
            'inline-flex h-11 items-center justify-center gap-2 whitespace-nowrap rounded-full px-6 text-sm font-semibold text-white',
            'transition-transform duration-200 hover:-translate-y-0.5 focus-visible:focus-ring',
          )}
          style={{ background: 'linear-gradient(90deg,#7C7FF5,#c084fc)' }}
        >
          {submitted ? (
            <>
              <Check className="size-4" aria-hidden="true" />
              Subscribed
            </>
          ) : (
            <>
              Subscribe
              <ArrowRight className="size-4" aria-hidden="true" />
            </>
          )}
        </button>
      </div>
      <p
        id="footer-newsletter-msg"
        role="status"
        aria-live="polite"
        className={cn(
          'mt-2 min-h-[1.1rem] text-xs',
          error ? 'text-danger' : submitted ? 'text-[#9be8c0]' : 'sr-only',
        )}
      >
        {error || (submitted ? success : '')}
      </p>
    </form>
  );
}

/* ------------------------------- Member promo ------------------------------- */

const PERKS = [
  { icon: Zap, label: 'Early Access' },
  { icon: Percent, label: 'Special Discounts' },
  { icon: LifeBuoy, label: 'Priority Support' },
];

function MemberPromo({ reduce }) {
  return (
    <div
      className="relative mb-12 mt-2 flex flex-col items-start gap-6 overflow-hidden rounded-[24px] border border-white/15 p-7 sm:flex-row sm:items-center sm:justify-between"
      style={{
        background:
          'linear-gradient(110deg, rgba(124,77,255,0.20), rgba(236,72,153,0.12)), linear-gradient(110deg,#161b3e,#1d1942)',
        boxShadow: '0 24px 60px -28px rgba(124,77,255,0.55)',
      }}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -left-10 -top-12 size-44 rounded-full bg-[#7C7FF5]/25 blur-3xl"
      />
      <div className="relative flex items-center gap-5">
        <Icon3D reduce={reduce} className="size-14 shrink-0" gradient="from-[#f0903a] to-[#ec4899]">
          <Gift className="size-6" aria-hidden="true" />
        </Icon3D>
        <div>
          <h3 className="text-lg font-semibold text-white">
            Become a member &amp; unlock exclusive perks
          </h3>
          <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 text-sm text-white/65">
            {PERKS.map(({ icon: Icon, label }) => (
              <li key={label} className="inline-flex items-center gap-1.5">
                <Icon className="size-4 text-[#B794F4]" aria-hidden="true" />
                {label}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <Link
        to="/rewards"
        className="relative inline-flex h-11 shrink-0 items-center gap-2 rounded-full px-6 text-sm font-semibold text-white shadow-lg transition-transform hover:-translate-y-0.5 focus-visible:focus-ring"
        style={{ background: 'linear-gradient(90deg,#f0903a,#ec4899)' }}
      >
        Join Now
        <ArrowRight className="size-4" aria-hidden="true" />
      </Link>
    </div>
  );
}

/* -------------------------------- Bottom bar -------------------------------- */

function BottomBar({ paymentMethods, bottomLinks, copyright }) {
  return (
    <div className="flex flex-col gap-5 border-t border-white/8 py-7 lg:flex-row lg:items-center lg:justify-between">
      {/* Payment method chips */}
      <ul className="flex flex-wrap items-center justify-center gap-1.5">
        {paymentMethods.map((m) => (
          <li
            key={m}
            aria-label={m}
            className="nums grid h-[22px] min-w-[2.75rem] place-items-center rounded border border-white/12 bg-white/8 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/75"
          >
            {m}
          </li>
        ))}
      </ul>

      {/* Legal / policy links */}
      <ul className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
        {bottomLinks.map(({ to, label, icon }) => {
          const Icon = resolveIcon(icon);
          return (
            <li key={to}>
              <Link
                to={to}
                className="flex items-center gap-1.5 rounded-sm text-xs text-white/55 transition-colors hover:text-white/90 focus-visible:focus-ring"
              >
                <Icon className="size-3.5 text-accent-mid" aria-hidden="true" />
                <span>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>

      <p className="nums text-center text-xs text-white/40">{copyright}</p>
    </div>
  );
}

function LinkColumn({ title, links }) {
  return (
    <div>
      <SectionHeading>{title}</SectionHeading>
      <ul className="space-y-2">
        {links.map((l) => (
          <li key={l.to}>
            <Link
              to={l.to}
              className="inline-block rounded-sm text-sm text-white/55 transition-[color,transform] duration-150 hover:-translate-y-px hover:text-white/95 focus-visible:focus-ring"
            >
              {l.label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SectionHeading({ children, className }) {
  return (
    <h3
      className={cn(
        'mb-3.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-white/40',
        className,
      )}
    >
      {children}
    </h3>
  );
}
