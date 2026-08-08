/**
 * Where the tracking layer is allowed to run, and which settings it reads.
 *
 * Two things live here because they answer the same question — *may we track
 * this page, with what?* — and because putting them in one place means neither
 * `gtm.js` nor `clarity.js` gets to invent its own idea of "an admin route".
 *
 * **Settings shape.** `/settings/public` returns a FLAT dict keyed by the
 * dotted setting name (`{"shipping.free_threshold": "999"}`) — see
 * `backend/app/api/v1/endpoints/settings.py`. Values arrive as raw strings, so
 * every read goes through a coercion helper rather than being trusted as a
 * boolean. A nested `{analytics: {...}}` shape is also accepted, because the
 * admin-settings surface is owned by another module and this layer should not
 * break if it ever hands us the grouped form.
 *
 * **Fail closed.** Every unknown or unreadable value resolves to the option
 * that sends the least data: no container id, no consent, no browser purchase.
 */

/** The settings keys this layer reads. The only place they are written down. */
export const SETTING_KEYS = Object.freeze({
  gtmContainerId: 'analytics.gtm_container_id',
  ga4MeasurementId: 'analytics.ga4_measurement_id',
  purchaseDelivery: 'analytics.ga4_purchase_delivery',
  clarityProjectId: 'analytics.clarity_project_id',
  clarityPseudonymousId: 'analytics.clarity_pseudonymous_id_enabled',
});

/**
 * Read one setting, tolerating both the flat dotted form the public endpoint
 * actually returns and a nested object form.
 */
export function readSetting(settings, key) {
  if (!settings || typeof settings !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(settings, key)) return settings[key];

  let node = settings;
  for (const part of String(key).split('.')) {
    if (!node || typeof node !== 'object') return undefined;
    node = node[part];
  }
  return node;
}

/** Trimmed string, or `null` when absent/blank. Never returns `''`. */
export function readString(settings, key) {
  const raw = readSetting(settings, key);
  if (raw === undefined || raw === null) return null;
  const value = String(raw).trim();
  return value === '' ? null : value;
}

/** Boolean from the string-ish values the settings table stores. */
export function readFlag(settings, key) {
  const raw = readSetting(settings, key);
  if (raw === undefined || raw === null) return false;
  if (typeof raw === 'boolean') return raw;
  return ['true', '1', 'yes', 'on'].includes(String(raw).trim().toLowerCase());
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** The admin shell. GTM and Clarity must never run here. */
export const ADMIN_ROUTE_RE = /^\/admin(?:\/|$)/;

/**
 * Routes that are staff-facing or internal plumbing. Recording these captures
 * a colleague doing their job over customer data — never a customer journey —
 * and it is the fastest way to fill a session-replay tool with order details,
 * addresses and user records nobody consented to share.
 */
export const INTERNAL_ROUTE_RES = Object.freeze([
  ADMIN_ROUTE_RE,
  /^\/staff(?:\/|$)/,
  /^\/internal(?:\/|$)/,
  /^\/payments\/mock(?:\/|$)/,
]);

/** Routes whose form fields are masked wholesale before Clarity records them. */
export const SENSITIVE_FORM_ROUTE_RES = Object.freeze([
  /^\/checkout(?:\/|$)/,
  /^\/account(?:\/|$)/,
  /^\/orders(?:\/|$)/,
  /^\/login(?:\/|$)/,
  /^\/forgot-password(?:\/|$)/,
  /^\/auth(?:\/|$)/,
  /^\/payments(?:\/|$)/,
]);

/**
 * The current path, as a string starting with `/`.
 *
 * `undefined` means "work it out from the window"; anything else — including an
 * explicit `null` from a caller that does not know where it is — resolves to
 * `null`, which every route predicate treats as excluded. An unknown route
 * never gets a tag loaded onto it by accident.
 */
export function resolvePath(pathname) {
  if (pathname === undefined) {
    if (typeof window === 'undefined' || !window.location) return null;
    return window.location.pathname || null;
  }
  return typeof pathname === 'string' && pathname !== '' ? pathname : null;
}

function matchesAny(pathname, patterns) {
  const path = resolvePath(pathname);
  if (path === null) return true; // unknown route → treat as excluded
  return patterns.some((re) => re.test(path));
}

/** True for `/admin` and anything under it. */
export function isAdminRoute(pathname) {
  return matchesAny(pathname, [ADMIN_ROUTE_RE]);
}

/** True for admin, staff, internal and payment-mock routes. */
export function isInternalRoute(pathname) {
  return matchesAny(pathname, INTERNAL_ROUTE_RES);
}

/** True where every form field should be masked, not just the obvious ones. */
export function isSensitiveFormRoute(pathname) {
  const path = resolvePath(pathname);
  if (path === null) return true;
  return SENSITIVE_FORM_ROUTE_RES.some((re) => re.test(path));
}

/**
 * Coarse page category — a GA4 dimension and one of the five Clarity tags.
 *
 * Deliberately coarse: it must not encode an id, a search term or anything
 * else that varies per customer, because it is sent on every page.
 */
export function pageTypeFor(pathname) {
  const path = resolvePath(pathname);
  if (path === null) return 'unknown';
  if (path === '/') return 'home';
  if (/^\/products\/[^/]+/.test(path)) return 'product_detail';
  if (/^\/(products|bestsellers|new-arrivals|categories)(?:\/|$)/.test(path)) {
    return 'product_list';
  }
  if (/^\/cart(?:\/|$)/.test(path)) return 'cart';
  if (/^\/checkout(?:\/|$)/.test(path)) return 'checkout';
  if (/^\/payments(?:\/|$)/.test(path)) return 'payment';
  if (/^\/orders(?:\/|$)/.test(path)) return 'order';
  if (/^\/(account|rewards|wishlist)(?:\/|$)/.test(path)) return 'account';
  if (/^\/(login|forgot-password|auth)(?:\/|$)/.test(path)) return 'auth';
  if (isInternalRoute(path)) return 'internal';
  return 'content';
}
