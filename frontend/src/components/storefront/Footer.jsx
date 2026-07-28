import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { cn, mediaUrl } from '@/lib/utils';
import { safeUrl } from '@/lib/safeUrl';
import { useFooterConfig } from '@/features/footer/hooks';
import { FOOTER_DEFAULTS, resolveIcon } from '@/features/footer/defaults';
import { useStorefrontConfigWithDefaults } from '@/features/storefront-config/hooks.js';
import { useSubscribeNewsletter } from '@/features/contact/hooks.js';
import CookiePreferencesLink from '@/components/consent/CookiePreferencesLink.jsx';
import { LeafMark } from './Logo';

/**
 * Storefront footer — dark-green Wellvia band (redesign mockups).
 *
 * Left: brand block + tagline + newsletter signup (real POST /newsletter/subscribe).
 * Right: admin-managed link columns + social icons.
 * Bottom: optional address blocks, payment badges, divider + copyright.
 *
 * Wiring:
 *   - useFooterConfig(): link_columns, social_links, newsletter.enabled,
 *     mail_us / registered_office, payment_methods and copyright.
 *   - useStorefrontConfigWithDefaults(): canonical brand identity (logo,
 *     brand name, tagline) shared with the header.
 *   - Newsletter: useSubscribeNewsletter() with success/error states.
 */

const TAGLINE =
  'Delicious wellness gummies crafted to support your everyday goals — from better sleep to daily vitality.';

/** Internal targets use client-side routing; anything else goes through safeUrl. */
function FooterLink({ link, className }) {
  const target = link.to ?? link.href ?? '';
  if (target.startsWith('/')) {
    return (
      <Link to={target} className={className}>
        {link.label}
      </Link>
    );
  }
  return (
    <a href={safeUrl(target)} target="_blank" rel="noreferrer" className={className}>
      {link.label}
    </a>
  );
}

/** True when an admin-editable address block has at least one non-empty line. */
function hasLines(block) {
  return (block?.lines ?? []).some((l) => typeof l === 'string' && l.trim() !== '');
}

export default function Footer() {
  const { data } = useFooterConfig();
  const cfg = { ...FOOTER_DEFAULTS, ...data };
  const { config: brand } = useStorefrontConfigWithDefaults();
  const year = new Date().getFullYear();

  const socialLinks = cfg.social_links ?? [];
  const columns = (cfg.link_columns ?? []).filter(
    (col) => (col?.links ?? []).length > 0,
  );
  const paymentMethods = (cfg.payment_methods ?? []).filter(Boolean);
  const showMailUs = hasLines(cfg.mail_us);
  const showRegisteredOffice = hasLines(cfg.registered_office);

  // "{year}" token → computed year; without the token the year is inserted
  // after the © sign so the bottom bar keeps reading "© 2026 …".
  const copyrightRaw = cfg.copyright || FOOTER_DEFAULTS.copyright;
  const copyright = copyrightRaw.includes('{year}')
    ? copyrightRaw.replace('{year}', String(year))
    : copyrightRaw.replace(/^©\s*/, `© ${year} `);

  return (
    <footer className="bg-[#08112C] text-wpaper">
      <div className="max-w-[1280px] mx-auto px-5 sm:px-10 lg:px-14 pt-12 lg:pt-16 pb-8">
        <div className="flex flex-wrap gap-x-14 gap-y-12 justify-between">
          {/* ── Brand + newsletter ─────────────────────────────────────── */}
          <div className="flex-1 basis-[340px] min-w-[270px] max-w-[430px]">
            {brand.logo_url ? (
              <img
                src={mediaUrl(brand.logo_url)}
                alt={brand.brand_name}
                className="max-h-[64px] w-auto object-contain mb-1.5"
              />
            ) : (
              <>
                <div className="mb-1.5">
                  <LeafMark size={52} color="#D9C9A6" />
                </div>
                <div className="font-display tracking-[0.3em] text-[clamp(26px,2.6vw,34px)] font-medium text-[#E9DDC0]">
                  {brand.brand_name}
                  <span className="text-[11px] align-super tracking-normal ml-0.5">™</span>
                </div>
              </>
            )}
            <div className="text-[10px] tracking-[0.42em] uppercase text-wpaper/60 mt-1 mb-6">
              {brand.tagline}
            </div>

            <p className="font-wserif text-[17px] leading-[1.55] text-wpaper/90 m-0 mb-7 max-w-[360px]">
              {TAGLINE}
            </p>

            {cfg.newsletter?.enabled !== false && <NewsletterForm />}
          </div>

          {/* ── Link columns ───────────────────────────────────────────── */}
          <div className="flex-[2] basis-[520px] min-w-[260px]">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-8 gap-y-10">
              {columns.map((col, i) => (
                <nav key={`${col.title}-${i}`} aria-label={col.title}>
                  <h3 className="font-wserif font-semibold text-[20px] text-wpaper m-0 mb-5 underline underline-offset-[7px] decoration-1 decoration-wpaper/70">
                    {col.title}
                  </h3>
                  <ul className="m-0 p-0 list-none flex flex-col gap-3.5">
                    {col.links.map((l) => (
                      <li key={`${l.label}-${l.to ?? l.href}`}>
                        <FooterLink
                          link={l}
                          className="font-wserif text-[17px] text-wpaper/90 no-underline hover:text-wgold transition-colors"
                        />
                      </li>
                    ))}
                  </ul>
                </nav>
              ))}
            </div>

            {/* Social icons */}
            {socialLinks.length > 0 && (
              <ul className="m-0 mt-10 p-0 list-none flex items-center justify-start sm:justify-end gap-5">
                {socialLinks.map(({ href, label, icon }) => {
                  const Icon = resolveIcon(icon);
                  return (
                    <li key={label}>
                      <a
                        href={safeUrl(href)}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={label}
                        className="grid size-10 place-items-center rounded-full text-wpaper transition-colors hover:text-wgold"
                      >
                        <Icon className="size-7" aria-hidden="true" />
                      </a>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* ── Address blocks (admin-editable, hidden while empty) ──────── */}
        {(showMailUs || showRegisteredOffice) && (
          <div className="flex flex-wrap gap-x-14 gap-y-8 mt-12">
            {showMailUs && (
              <div className="flex-1 basis-[280px] min-w-[240px]">
                <h3 className="font-wserif font-semibold text-[17px] text-wpaper m-0 mb-3">
                  {cfg.mail_us.heading || 'Mail Us'}
                </h3>
                <address className="not-italic text-[13.5px] leading-[1.7] text-wpaper/75 m-0">
                  {cfg.mail_us.lines
                    .filter((l) => l && l.trim())
                    .map((line) => (
                      <span key={line} className="block">
                        {line}
                      </span>
                    ))}
                </address>
              </div>
            )}
            {showRegisteredOffice && (
              <div className="flex-1 basis-[280px] min-w-[240px]">
                <h3 className="font-wserif font-semibold text-[17px] text-wpaper m-0 mb-3">
                  {cfg.registered_office.heading || 'Registered Office Address'}
                </h3>
                <address className="not-italic text-[13.5px] leading-[1.7] text-wpaper/75 m-0">
                  {cfg.registered_office.lines
                    .filter((l) => l && l.trim())
                    .map((line) => (
                      <span key={line} className="block">
                        {line}
                      </span>
                    ))}
                  {cfg.registered_office.cin && (
                    <span className="block mt-2">CIN: {cfg.registered_office.cin}</span>
                  )}
                  {(cfg.registered_office.phones ?? []).map((p) => (
                    <a
                      key={p.tel || p.display}
                      href={safeUrl(`tel:${p.tel}`)}
                      className="block text-wpaper/75 no-underline hover:text-wgold transition-colors"
                    >
                      {p.display}
                    </a>
                  ))}
                </address>
              </div>
            )}
          </div>
        )}

        {/* ── Bottom bar ───────────────────────────────────────────────── */}
        <div className="border-t border-wpaper/25 mt-10 pt-6 text-center">
          {paymentMethods.length > 0 && (
            <ul
              className="m-0 mb-4 p-0 list-none flex flex-wrap items-center justify-center gap-2.5"
              aria-label="Accepted payment methods"
            >
              {paymentMethods.map((m) => (
                <li
                  key={m}
                  className="rounded-[6px] border border-wpaper/30 px-2.5 py-1 text-[11px] tracking-[0.08em] text-wpaper/80"
                >
                  {m}
                </li>
              ))}
            </ul>
          )}
          <p className="font-wserif text-[16px] text-wpaper/90 m-0">{copyright}</p>
          {/*
           * The withdrawal path. It is on every storefront page and one click
           * from any of them, because consent that is easy to give and hard to
           * take back is not consent — it is a one-way ratchet.
           */}
          <p className="m-0 mt-3">
            <CookiePreferencesLink className="font-wserif text-[14px] text-wpaper/80 hover:text-wgold transition-colors" />
          </p>
        </div>
      </div>
    </footer>
  );
}

/* ── Newsletter form — real subscription via POST /newsletter/subscribe ───── */
function NewsletterForm() {
  const subscribe = useSubscribeNewsletter();
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');

  // Stable IDs when Footer is rendered more than once (e.g. tests).
  const uid = useId();
  const inputId = `${uid}-email`;
  const msgId = `${uid}-msg`;

  // Let the "Subscribed!" note rest for a few seconds, then reset.
  useEffect(() => {
    if (!subscribe.isSuccess) return undefined;
    const t = window.setTimeout(() => subscribe.reset(), 5000);
    return () => window.clearTimeout(t);
  }, [subscribe.isSuccess, subscribe]);

  function onSubmit(e) {
    e.preventDefault();
    const value = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      setError('Please enter a valid email address.');
      return;
    }
    setError('');
    subscribe.mutate(value, {
      onSuccess: () => setEmail(''),
    });
  }

  const apiError = subscribe.isError
    ? subscribe.error?.response?.status === 429
      ? 'Too many attempts — please try again in a little while.'
      : 'Could not subscribe. Please try again.'
    : '';
  const message = error || apiError || (subscribe.isSuccess ? 'Subscribed!' : '');

  return (
    <form onSubmit={onSubmit} noValidate aria-label="Subscribe to the newsletter">
      <label
        htmlFor={inputId}
        className="block font-wserif font-medium text-[19px] text-wpaper mb-3"
      >
        Subscribe to Our Newsletter
      </label>
      <div className="flex items-stretch gap-2.5 max-w-[380px]">
        <input
          id={inputId}
          type="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (error) setError('');
            if (subscribe.isError) subscribe.reset();
          }}
          placeholder="Mail id"
          autoComplete="email"
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={msgId}
          className="flex-1 min-w-0 rounded-[6px] border-0 bg-wpaper px-4 py-2.5 text-[14px] text-wink placeholder:text-wmuted outline-none focus:ring-2 focus:ring-wgold/60"
        />
        <button
          type="submit"
          disabled={subscribe.isPending}
          className="shrink-0 rounded-[6px] border border-wpaper/70 bg-transparent px-5 py-2.5 font-wserif text-[16px] text-wpaper cursor-pointer hover:bg-wpaper hover:text-wgreen disabled:opacity-60 disabled:cursor-wait transition-colors"
        >
          {subscribe.isPending ? 'Subscribing…' : 'Subscribe'}
        </button>
      </div>

      {/* Validation / success / error message */}
      <p
        id={msgId}
        role="status"
        aria-live="polite"
        className={cn(
          'mt-2 min-h-[1rem] text-[12.5px]',
          error || apiError
            ? 'text-[#f2b8ae]'
            : subscribe.isSuccess
              ? 'text-[#cfe3d0]'
              : 'sr-only',
        )}
      >
        {message}
      </p>
    </form>
  );
}
