import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Mail, ArrowRight, Check } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { safeUrl } from '@/lib/safeUrl.js';
import { useFooterConfig } from '@/features/footer/hooks.js';
import { FOOTER_DEFAULTS, resolveIcon } from '@/features/footer/defaults.js';

/**
 * Storefront footer — ShopFlow reference style.
 * Layout:
 *   1. Light trust-badges row (white bg, 4 items with blue icons)
 *   2. Navy dark footer (#0D1B36) with newsletter band
 *   3. Multi-column links: brand col + link columns + address cols
 *   4. Bottom bar: copyright + payment chips
 */
export default function Footer() {
  const { data } = useFooterConfig();
  const cfg = { ...FOOTER_DEFAULTS, ...data };
  const year = new Date().getFullYear();
  const copyright = (cfg.copyright || '').replace('{year}', year);

  return (
    <footer className="mt-6">
      {/* Trust badges row — light surface ABOVE the navy footer */}
      {cfg.trust_features?.length > 0 && (
        <TrustBadgesRow features={cfg.trust_features} />
      )}

      {/* Navy footer */}
      <div className="bg-[#0D1B36] text-white">
        {/* Newsletter band */}
        {cfg.newsletter?.enabled !== false && (
          <NewsletterBand newsletter={cfg.newsletter} brand={cfg.brand} />
        )}

        {/* Divider */}
        <div className="border-t border-white/10" />

        {/* Main link columns */}
        <div className="mx-auto max-w-content px-6 py-10">
          <div className="grid grid-cols-2 gap-8 sm:grid-cols-3 lg:grid-cols-[1.6fr_repeat(4,1fr)_1.4fr_1.4fr]">
            {/* Brand column */}
            <div className="col-span-2 sm:col-span-3 lg:col-span-1">
              <Link
                to="/"
                className="inline-flex items-center gap-2 rounded-xs focus-visible:focus-ring"
              >
                {cfg.brand?.logo_url ? (
                  <img
                    src={cfg.brand.logo_url}
                    alt={cfg.brand?.name || 'Store'}
                    className="h-8 w-auto max-w-[160px] object-contain"
                  />
                ) : (
                  <span className="text-lg font-bold text-white tracking-tight">
                    {cfg.brand?.name || 'Store'}
                  </span>
                )}
              </Link>

              {cfg.brand?.tagline && (
                <p className="mt-2 max-w-[220px] text-xs leading-relaxed text-[#878787]">
                  {cfg.brand.tagline}
                </p>
              )}

              {/* Social links */}
              {cfg.social_links?.length > 0 && (
                <ul className="mt-4 flex items-center gap-2">
                  {cfg.social_links.map(({ href, label, icon }) => {
                    const Icon = resolveIcon(icon);
                    return (
                      <li key={label}>
                        <a
                          href={safeUrl(href)}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={label}
                          className="grid size-8 place-items-center rounded-xs border border-white/15 bg-white/8 text-white/60 transition-colors duration-150 hover:border-white/30 hover:bg-white/15 hover:text-white focus-visible:focus-ring"
                        >
                          <Icon className="size-3.5" aria-hidden="true" />
                        </a>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {/* Link columns */}
            {cfg.link_columns.map((col) => (
              <LinkColumn key={col.title} title={col.title} links={col.links} />
            ))}

            {/* Mail Us */}
            <div>
              <SectionHeading>{cfg.mail_us?.heading || 'Mail Us'}</SectionHeading>
              <address className="not-italic text-xs leading-6 text-white/60">
                {(cfg.mail_us?.lines || []).map((line) => (
                  <span key={line} className="block">
                    {line}
                  </span>
                ))}
              </address>
            </div>

            {/* Registered Office */}
            <div>
              <SectionHeading>
                {cfg.registered_office?.heading || 'Registered Office'}
              </SectionHeading>
              <address className="not-italic text-xs leading-6 text-white/60">
                {(cfg.registered_office?.lines || []).map((line) => (
                  <span key={line} className="block">
                    {line}
                  </span>
                ))}
                {cfg.registered_office?.cin && (
                  <span className="mt-1.5 block">CIN: {cfg.registered_office.cin}</span>
                )}
              </address>
              {cfg.registered_office?.phones?.length > 0 && (
                <p className="mt-1.5 text-xs text-white/60">
                  {cfg.registered_office.phones.map((p, i) => (
                    <span key={p.tel}>
                      <a
                        href={`tel:${p.tel}`}
                        className="rounded-xs text-[#878787] transition-colors hover:text-white focus-visible:focus-ring"
                      >
                        {p.display}
                      </a>
                      {i < cfg.registered_office.phones.length - 1 && (
                        <span className="text-white/25"> / </span>
                      )}
                    </span>
                  ))}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Bottom bar */}
        <BottomBar
          paymentMethods={cfg.payment_methods}
          bottomLinks={cfg.bottom_links}
          copyright={copyright}
        />
      </div>
    </footer>
  );
}

/* -------------------------------- Trust badges row -------------------------------- */

function TrustBadgesRow({ features }) {
  return (
    <div className="border-b border-line-subtle bg-bg-elevated">
      <div className="mx-auto max-w-content px-6 py-5">
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {features.map(({ icon, title, sub }) => {
            const Icon = resolveIcon(icon);
            return (
              <li key={title} className="flex items-center gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-sm border border-line-subtle bg-accent/5 text-accent">
                  <Icon className="size-5" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-ink-primary leading-tight">{title}</p>
                  {sub && <p className="text-xs text-ink-secondary leading-tight">{sub}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/* -------------------------------- Newsletter -------------------------------- */

function NewsletterBand({ newsletter = {}, brand = {} }) {
  return (
    <div className="mx-auto max-w-content px-6 py-6">
      <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Mail className="size-5 shrink-0 text-[#878787]" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-white">
              Subscribe &amp; get {brand?.name || 'exclusive'} deals in your inbox
            </p>
            <p className="text-xs text-[#878787]">
              No spam. Unsubscribe anytime.
            </p>
          </div>
        </div>
        <NewsletterForm newsletter={newsletter} brand={brand} />
      </div>
    </div>
  );
}

function NewsletterForm({ newsletter = {}, brand = {} }) {
  const {
    placeholder = 'you@example.com',
    success = `You're on the list. Welcome to ${brand?.name || 'our store'}.`,
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
    <form onSubmit={onSubmit} noValidate aria-label="Subscribe to the newsletter" className="shrink-0">
      <div className="flex gap-2">
        <label htmlFor="footer-newsletter" className="sr-only">
          Email address
        </label>
        <div className="relative">
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
              'h-9 w-56 rounded-xs border bg-white/8 px-3 text-xs text-white',
              'placeholder:text-white/35 transition-colors duration-150',
              'focus:outline-none focus-visible:focus-ring',
              error ? 'border-danger' : 'border-white/15 focus:border-white/40',
            )}
          />
        </div>
        <button
          type="submit"
          className={cn(
            'inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-xs',
            'bg-accent px-4 text-xs font-semibold text-white',
            'transition-colors duration-150 hover:bg-accent-hover',
            'focus-visible:focus-ring',
          )}
        >
          {submitted ? (
            <>
              <Check className="size-3.5" aria-hidden="true" />
              Subscribed
            </>
          ) : (
            <>
              Subscribe
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </>
          )}
        </button>
      </div>
      <p
        id="footer-newsletter-msg"
        role="status"
        aria-live="polite"
        className={cn(
          'mt-1 min-h-[1rem] text-[11px]',
          error ? 'text-danger' : submitted ? 'text-[#9be8c0]' : 'sr-only',
        )}
      >
        {error || (submitted ? success : '')}
      </p>
    </form>
  );
}

/* -------------------------------- Bottom bar -------------------------------- */

function BottomBar({ paymentMethods, bottomLinks, copyright }) {
  return (
    <div className="border-t border-white/10">
      <div className="mx-auto max-w-content px-6">
        <div className="flex flex-col gap-4 py-5 lg:flex-row lg:items-center lg:justify-between">
          {/* Copyright */}
          <p className="text-[11px] text-[#878787] lg:shrink-0">{copyright}</p>

          {/* Legal links */}
          <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
            {bottomLinks.map(({ to, label, icon }) => {
              const Icon = resolveIcon(icon);
              return (
                <li key={to}>
                  <Link
                    to={to}
                    className="flex items-center gap-1 rounded-xs text-[11px] text-[#878787] transition-colors hover:text-white focus-visible:focus-ring"
                  >
                    <Icon className="size-3 text-[#878787]" aria-hidden="true" />
                    <span>{label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>

          {/* Payment method chips */}
          <ul className="flex flex-wrap items-center gap-1.5">
            {paymentMethods.map((m) => (
              <li
                key={m}
                aria-label={m}
                className="grid h-[20px] min-w-[2.5rem] place-items-center rounded-xs border border-white/12 bg-white/8 px-1.5 text-[10px] font-semibold uppercase tracking-wider text-[#878787]"
              >
                {m}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------- Sub-components ─────────────────────────── */

function LinkColumn({ title, links }) {
  return (
    <div>
      <SectionHeading>{title}</SectionHeading>
      <ul className="space-y-2">
        {links.map((l) => (
          <li key={l.to}>
            <Link
              to={l.to}
              className="inline-block rounded-xs text-xs text-white/70 transition-colors duration-150 hover:text-white focus-visible:focus-ring"
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
        'mb-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[#878787]',
        className,
      )}
    >
      {children}
    </h3>
  );
}
