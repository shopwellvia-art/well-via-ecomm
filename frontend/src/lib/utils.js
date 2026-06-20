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

/** Human stock label per the business rules. */
export function stockLabel(stock) {
  if (stock <= 0) return { text: 'Out of stock', tone: 'danger' };
  if (stock <= 5) return { text: `Only ${stock} left`, tone: 'warning' };
  return { text: 'In stock', tone: 'success' };
}
