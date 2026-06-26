import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { safeUrl } from '@/lib/safeUrl';
import { useFooterConfig } from '@/features/footer/hooks';
import { FOOTER_DEFAULTS, resolveIcon } from '@/features/footer/defaults';
import { LeafMark } from './Logo';
import { LockIcon } from './Icons';

/**
 * Storefront footer — wellness aesthetic.
 *
 * Data shape mirrors the existing Flipkart-era Footer.jsx but styled with
 * Wellvia tokens (wcanvas/wpaper/wcard bg, wgreen/wgold accents, wserif headings).
 *
 * Wired to:
 *   - useFooterConfig() + FOOTER_DEFAULTS  (brand, link_columns, newsletter,
 *     trust_features, social_links, bottom_links, payment_methods, copyright)
 *   - newsletter form: local state only (no API, mirrors existing Footer.jsx)
 */
export default function Footer() {
  const { data } = useFooterConfig();
  const cfg = { ...FOOTER_DEFAULTS, ...data };
  const year = new Date().getFullYear();
  const copyright = (cfg.copyright || '').replace('{year}', year);

  const trustFeatures = cfg.trust_features ?? [];
  const linkColumns = cfg.link_columns ?? [];
  const socialLinks = cfg.social_links ?? [];
  const bottomLinks = cfg.bottom_links ?? [];
  const paymentMethods = cfg.payment_methods ?? [];

  return (
    <footer className="relative overflow-hidden border-t border-wline bg-wpaper">
      {/* Decorative gradient wash at the top */}
      <div
        className="pointer-events-none absolute top-0 inset-x-0 h-[140px] bg-gradient-to-b from-wgold/10 to-transparent z-0"
        aria-hidden="true"
      />

      <div className="relative z-[1] max-w-[1120px] mx-auto px-5 sm:px-10 lg:px-14 pt-10 lg:pt-14 pb-8">

        {/* ── Logo block ─────────────────────────────────────────────────── */}
        <div className="flex flex-col items-center gap-1 mb-7 lg:mb-9">
          <LeafMark size={48} />
          <div className="font-display tracking-[0.28em] text-[clamp(24px,3.2vw,34px)] font-medium text-wgreen pl-[0.28em]">
            WELLVIA
          </div>
          {cfg.brand?.tagline && (
            <p className="mt-1.5 text-[13px] text-wmuted text-center max-w-[320px] leading-relaxed">
              {cfg.brand.tagline}
            </p>
          )}
        </div>

        <div className="h-px bg-wline mb-7 lg:mb-9" />

        {/* ── Main content grid ───────────────────────────────────────────── */}
        <div className="flex flex-wrap gap-8 lg:gap-12 justify-between items-start">

          {/* First 3 link columns + newsletter */}
          <div className="flex-1 basis-[440px] min-w-[280px]">
            {linkColumns.length > 0 && (
              <div className="flex flex-wrap">
                {linkColumns.slice(0, 3).map((col, i) => (
                  <div
                    key={col.title}
                    className={cn(
                      'flex-1 min-w-[130px] px-6',
                      i === 0 ? 'pl-0' : 'border-l border-wline',
                    )}
                  >
                    <h3 className="font-wserif font-semibold text-[19px] mb-4 text-wink">
                      {col.title}
                    </h3>
                    <ul className="flex flex-col gap-2.5">
                      {(col.links ?? []).map((l) => (
                        <li key={l.to}>
                          <Link
                            to={l.to}
                            className="text-[13.5px] text-wmuted hover:text-wgreen no-underline transition-colors"
                          >
                            {l.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}

            {/* Newsletter */}
            {cfg.newsletter?.enabled !== false && (
              <div className="mt-7 lg:mt-9">
                <NewsletterForm newsletter={cfg.newsletter} brand={cfg.brand} />
              </div>
            )}

            {/* Social links */}
            {socialLinks.length > 0 && (
              <ul className="mt-5 flex items-center gap-2.5">
                {socialLinks.map(({ href, label, icon }) => {
                  const Icon = resolveIcon(icon);
                  return (
                    <li key={label}>
                      <a
                        href={safeUrl(href)}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={label}
                        className="grid size-8 place-items-center rounded-full border border-wline bg-wcard text-wmuted transition-colors hover:border-wgreen hover:bg-wgreen hover:text-white"
                      >
                        <Icon className="size-3.5" aria-hidden="true" />
                      </a>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Extra link columns (4th+) + address info */}
          {linkColumns.length > 3 && (
            <div className="flex-[0_1_280px] min-w-[200px] flex flex-col gap-6">
              {linkColumns.slice(3).map((col) => (
                <div key={col.title}>
                  <h3 className="font-wserif font-semibold text-[17px] mb-3 text-wink">
                    {col.title}
                  </h3>
                  <ul className="flex flex-col gap-2">
                    {(col.links ?? []).map((l) => (
                      <li key={l.to}>
                        <Link
                          to={l.to}
                          className="text-[13px] text-wmuted hover:text-wgreen no-underline transition-colors"
                        >
                          {l.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}

              {/* Mail Us */}
              {cfg.mail_us?.lines?.length > 0 && (
                <div>
                  <h3 className="font-wserif font-semibold text-[17px] mb-3 text-wink">
                    {cfg.mail_us.heading || 'Mail Us'}
                  </h3>
                  <address className="not-italic text-[12.5px] leading-[1.8] text-wmuted">
                    {cfg.mail_us.lines.map((line) => (
                      <span key={line} className="block">{line}</span>
                    ))}
                  </address>
                </div>
              )}

              {/* Registered Office */}
              {cfg.registered_office?.lines?.length > 0 && (
                <div>
                  <h3 className="font-wserif font-semibold text-[17px] mb-3 text-wink">
                    {cfg.registered_office.heading || 'Registered Office'}
                  </h3>
                  <address className="not-italic text-[12.5px] leading-[1.8] text-wmuted">
                    {cfg.registered_office.lines.map((line) => (
                      <span key={line} className="block">{line}</span>
                    ))}
                    {cfg.registered_office.cin && (
                      <span className="block mt-1">
                        CIN: {cfg.registered_office.cin}
                      </span>
                    )}
                  </address>
                  {(cfg.registered_office.phones ?? []).length > 0 && (
                    <p className="mt-1.5 text-[12px] text-wmuted">
                      {cfg.registered_office.phones.map((p, i) => (
                        <span key={p.tel}>
                          <a
                            href={`tel:${p.tel}`}
                            className="text-wmuted hover:text-wgreen transition-colors"
                          >
                            {p.display}
                          </a>
                          {i < cfg.registered_office.phones.length - 1 && (
                            <span className="text-wline mx-1"> / </span>
                          )}
                        </span>
                      ))}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="h-px bg-wline my-7 lg:my-9" />

        {/* ── Trust badges ────────────────────────────────────────────────── */}
        {trustFeatures.length > 0 && (
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3.5 mb-7">
            {trustFeatures.map(({ icon, title, sub }) => {
              const Icon = resolveIcon(icon);
              return (
                <div
                  key={title}
                  className="flex items-center gap-2.5 text-[12.5px] text-wmuted"
                >
                  <Icon className="size-[18px] text-wgold shrink-0" aria-hidden="true" />
                  <span>
                    {title}
                    {sub && (
                      <span className="hidden sm:inline text-wmuted/70">
                        {' '}— {sub}
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* ── Bottom bar ──────────────────────────────────────────────────── */}
        <div className="border-t border-wline pt-5 flex flex-wrap items-center justify-between gap-4">
          {/* Copyright */}
          <p className="text-[11.5px] text-wmuted tracking-wide order-1">
            {copyright}
          </p>

          {/* Bottom / legal links */}
          {bottomLinks.length > 0 && (
            <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 order-2">
              {bottomLinks.map(({ to, label }) => (
                <li key={to}>
                  <Link
                    to={to}
                    className="text-[11px] text-wmuted hover:text-wgreen no-underline transition-colors"
                  >
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {/* Payment method chips */}
          {paymentMethods.length > 0 && (
            <ul
              className="flex flex-wrap items-center gap-1.5 order-3"
              aria-label="Accepted payment methods"
            >
              {paymentMethods.map((m) => (
                <li
                  key={m}
                  className="grid h-[18px] min-w-[2.2rem] place-items-center rounded border border-wline bg-wcard px-1.5 text-[9px] font-semibold uppercase tracking-wider text-wmuted"
                >
                  {m}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </footer>
  );
}

/* ── Newsletter form ─────────────────────────────────────────────────────────
   Local state only — no API call (mirrors existing Footer.jsx behaviour).
   Validates email, shows success for 4 seconds, then resets.          */
function NewsletterForm({ newsletter = {}, brand = {} }) {
  const {
    placeholder = 'you@example.com',
    success = `You're on the list. Welcome to ${brand?.name || 'Wellvia'}.`,
  } = newsletter;

  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  // Stable IDs when Footer is rendered more than once (e.g. tests).
  const uid = useId();
  const inputId = `${uid}-email`;
  const msgId = `${uid}-msg`;

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
    <div>
      {/* Gold botanical ornament */}
      <div className="flex items-center gap-3.5 mb-3.5">
        <svg width="40" height="22" viewBox="0 0 48 24" fill="none" className="shrink-0" aria-hidden="true">
          <path d="M8 16c-3-1-5-4-5-8 4 0 7 2 8 6" stroke="#B49A63" strokeWidth="1.2" />
          <path d="M20 14c-3-1-5-4-5-8 4 0 7 2 8 6" stroke="#B49A63" strokeWidth="1.2" />
          <path d="M32 16c-3-1-5-4-5-8 4 0 7 2 8 6" stroke="#B49A63" strokeWidth="1.2" />
        </svg>
        <p className="font-wserif font-medium text-[16px] text-wink leading-snug">
          Join the Wellvia community
        </p>
      </div>

      <form onSubmit={onSubmit} noValidate aria-label="Subscribe to the newsletter">
        <div className="flex items-center border border-wline rounded-full bg-wcard max-w-[430px] p-1">
          <label htmlFor={inputId} className="sr-only">
            Email address
          </label>
          <input
            id={inputId}
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (error) setError('');
            }}
            placeholder={placeholder}
            autoComplete="email"
            aria-invalid={error ? 'true' : undefined}
            aria-describedby={msgId}
            className="flex-1 border-0 bg-transparent px-[18px] py-2.5 text-[13.5px] outline-none text-wink placeholder:text-wmuted"
          />
          <button
            type="submit"
            className="bg-wgreen text-white border-0 px-6 py-2.5 text-[13px] cursor-pointer rounded-full whitespace-nowrap hover:bg-wgreen-dark transition-colors"
          >
            {submitted ? 'Joined!' : 'Join Now'}
          </button>
        </div>

        {/* Validation / success message */}
        <p
          id={msgId}
          role="status"
          aria-live="polite"
          className={cn(
            'mt-1.5 min-h-[1rem] text-[11.5px]',
            error ? 'text-danger' : submitted ? 'text-wgreen' : 'sr-only',
          )}
        >
          {error || (submitted ? success : '')}
        </p>
      </form>

      {/* Secure notice */}
      <div className="flex items-center gap-2 mt-2.5 text-[12.5px] text-wmuted">
        <LockIcon size={13} stroke="#B49A63" />
        Checkout is encrypted &amp; secure.
      </div>
    </div>
  );
}
