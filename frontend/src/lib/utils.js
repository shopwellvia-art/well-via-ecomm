import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional class names, resolving Tailwind conflicts. */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/** Format a numeric value as a currency string. Server is authoritative for money. */
export function formatPrice(value, currency = 'INR') {
  const n = Number(value);
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
}

/**
 * Normalize an uploaded-media URL to a same-origin relative path.
 *
 * Legacy rows stored an absolute `http://localhost:8000/media/...` URL, which
 * is cross-origin and blocked by the storefront CSP (`img-src 'self'`) on the
 * nginx build. Both the Vite dev proxy and the nginx build proxy `/media` →
 * backend, so a relative `/media/...` path loads same-origin everywhere.
 * External URLs (S3/CDN https links, which have no `/media/` path) pass through
 * untouched.
 */
export function mediaUrl(url) {
  if (!url || typeof url !== 'string') return url;
  if (url.startsWith('/')) return url; // already relative
  try {
    const u = new URL(url);
    if (u.pathname.startsWith('/media/')) return u.pathname + u.search;
  } catch {
    // Not a parseable absolute URL — leave it as-is.
  }
  return url;
}

/**
 * Expand a 2-letter ISO country code to its display name.
 *
 * Addresses store the code (`"IN"`), which reads like a typo on an order page —
 * Flipkart/Amazon both spell the country out. `Intl.DisplayNames` covers every
 * code without shipping a lookup table; anything already spelled out, or an
 * unknown code, passes through untouched.
 */
export function countryName(code) {
  if (!code || typeof code !== 'string') return code;
  const trimmed = code.trim();
  if (trimmed.length !== 2) return trimmed; // already a name
  try {
    // fallback: 'code' is already the spec default; stated explicitly so an
    // unassigned code (e.g. "QQ") is guaranteed to come back as itself rather
    // than as some placeholder string. Note "ZZ" is NOT unassigned — CLDR
    // defines it as "Unknown Region", so that is what it correctly returns.
    return (
      new Intl.DisplayNames(['en'], {
        type: 'region',
        fallback: 'code',
      }).of(trimmed.toUpperCase()) || trimmed
    );
  } catch {
    return trimmed;
  }
}

/**
 * Format an Indian mobile number for display: `+91 76437 93833`.
 *
 * Only reformats what it recognises as a 10-digit Indian mobile (optionally
 * already carrying a 91/+91 prefix). Anything else — landlines, international
 * numbers, part-entered values — is returned unchanged rather than mangled,
 * because a wrong-looking phone number on a shipping label costs a delivery.
 */
export function formatPhone(phone) {
  if (!phone || typeof phone !== 'string') return phone;
  const digits = phone.replace(/\D/g, '');
  const local =
    digits.length === 10
      ? digits
      : digits.length === 12 && digits.startsWith('91')
        ? digits.slice(2)
        : null;
  // Indian mobiles start 6-9; a 10-digit number outside that range is something
  // else and is left alone.
  if (!local || !/^[6-9]/.test(local)) return phone;
  return `+91 ${local.slice(0, 5)} ${local.slice(5)}`;
}

/** Human stock label per the business rules. */
export function stockLabel(stock) {
  if (stock <= 0) return { text: 'Out of stock', tone: 'danger' };
  if (stock <= 5) return { text: `Only ${stock} left`, tone: 'warning' };
  return { text: 'In stock', tone: 'success' };
}
