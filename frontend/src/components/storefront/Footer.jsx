import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { safeUrl } from '@/lib/safeUrl';
import { useFooterConfig } from '@/features/footer/hooks';
import { FOOTER_DEFAULTS, resolveIcon } from '@/features/footer/defaults';
import { useSubscribeNewsletter } from '@/features/contact/hooks.js';
import { LeafMark } from './Logo';

/**
 * Storefront footer — dark-green Wellvia band (redesign mockups).
 *
 * Left: logo block + tagline + newsletter signup (real POST /newsletter/subscribe).
 * Right: Shop / Explore / Customer Care link columns + social icons.
 * Bottom: divider + copyright.
 *
 * Wiring:
 *   - useFooterConfig(): social_links + newsletter.enabled flag + the existing
 *     policy links (Help / Consumer Policy columns), which are reorganized
 *     under "Customer Care". Brand copy is pinned to the Wellvia mockup —
 *     the stored config still carries placeholder branding.
 *   - Newsletter: useSubscribeNewsletter() with success/error states.
 */

const TAGLINE =
  'Delicious wellness gummies crafted to support your everyday goals — from better sleep to daily vitality.';

const SHOP_LINKS = [
  { label: 'All Products', to: '/products' },
  { label: 'Best Sellers', to: '/bestsellers' },
  { label: 'New Arrivals', to: '/new-arrivals' },
  { label: 'Combos', to: '/categories' },
  { label: 'Shop by Goal', to: '/products' },
];

const EXPLORE_LINKS = [
  { label: 'About Us', to: '/about' },
  { label: 'Blog', to: '/stories' },
  { label: 'FAQs', to: '/contact' },
  { label: 'Contact Us', to: '/contact' },
  { label: 'Track Order', to: '/orders' },
];

// Fallback when the footer config carries no policy/help columns.
const CARE_FALLBACK = [
  { label: 'Shipping Policy', to: '/shipping' },
  { label: 'Refund & Cancellation', to: '/refund' },
  { label: 'Terms & Conditions', to: '/terms' },
  { label: 'Privacy Policy', to: '/privacy' },
  { label: 'Contact Us', to: '/contact' },
];

/** Existing policy/help links from the config, reorganized under one heading. */
function customerCareLinks(linkColumns) {
  const links = [];
  const seen = new Set();
  for (const col of linkColumns ?? []) {
    if (!/policy|help|care/i.test(col?.title || '')) continue;
    for (const l of col.links ?? []) {
      const key = (l?.label || '').trim().toLowerCase();
      if (!l?.to || !key || seen.has(key)) continue;
      seen.add(key);
      links.push(l);
    }
  }
  return (links.length > 0 ? links : CARE_FALLBACK).slice(0, 6);
}

export default function Footer() {
  const { data } = useFooterConfig();
  const cfg = { ...FOOTER_DEFAULTS, ...data };
  const year = new Date().getFullYear();

  const socialLinks = cfg.social_links ?? [];
  const columns = [
    { title: 'Shop', links: SHOP_LINKS },
    { title: 'Explore', links: EXPLORE_LINKS },
    { title: 'Customer Care', links: customerCareLinks(cfg.link_columns) },
  ];

  return (
    <footer className="bg-[#08112C] text-wpaper">
      <div className="max-w-[1280px] mx-auto px-5 sm:px-10 lg:px-14 pt-12 lg:pt-16 pb-8">
        <div className="flex flex-wrap gap-x-14 gap-y-12 justify-between">
          {/* ── Brand + newsletter ─────────────────────────────────────── */}
          <div className="flex-1 basis-[340px] min-w-[270px] max-w-[430px]">
            <div className="mb-1.5">
              <LeafMark size={52} color="#D9C9A6" />
            </div>
            <div className="font-display tracking-[0.3em] text-[clamp(26px,2.6vw,34px)] font-medium text-[#E9DDC0]">
              WELLVIA
              <span className="text-[11px] align-super tracking-normal ml-0.5">™</span>
            </div>
            <div className="text-[10px] tracking-[0.42em] uppercase text-wpaper/60 mt-1 mb-6">
              Wellness Redefined
            </div>

            <p className="font-wserif text-[17px] leading-[1.55] text-wpaper/90 m-0 mb-7 max-w-[360px]">
              {TAGLINE}
            </p>

            {cfg.newsletter?.enabled !== false && <NewsletterForm />}
          </div>

          {/* ── Link columns ───────────────────────────────────────────── */}
          <div className="flex-[2] basis-[520px] min-w-[260px]">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-8 gap-y-10">
              {columns.map((col) => (
                <nav key={col.title} aria-label={col.title}>
                  <h3 className="font-wserif font-semibold text-[20px] text-wpaper m-0 mb-5 underline underline-offset-[7px] decoration-1 decoration-wpaper/70">
                    {col.title}
                  </h3>
                  <ul className="m-0 p-0 list-none flex flex-col gap-3.5">
                    {col.links.map((l) => (
                      <li key={`${l.label}-${l.to}`}>
                        <Link
                          to={l.to}
                          className="font-wserif text-[17px] text-wpaper/90 no-underline hover:text-wgold transition-colors"
                        >
                          {l.label}
                        </Link>
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

        {/* ── Bottom bar ───────────────────────────────────────────────── */}
        <div className="border-t border-wpaper/25 mt-10 pt-6 text-center">
          <p className="font-wserif text-[16px] text-wpaper/90 m-0">
            © {year} Wellvia. All rights reserved.
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
