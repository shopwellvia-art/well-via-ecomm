/**
 * URL ⇄ filter-object codec for the analytics views. Pure, no React.
 *
 * Mirrors `AnalyticsFilters` in `backend/app/services/analytics/filters.py`.
 * The backend is the authority on what a filter *means*; this file is the
 * authority on how it is written into, and read back out of, the address bar.
 *
 * Four properties, in the order they mattered when this was written:
 *
 * 1. **Round-trip stable.** `parseFilters(toSearchParams(f, view), view)` deep
 *    equals `f` for any already-normalised `f`. Without that, every render that
 *    re-serialises state pushes a subtly different URL and the back button
 *    becomes a random walk.
 *
 * 2. **Defaults are omitted.** A view opened with no filters has a clean
 *    address bar, so what IS in the URL is exactly what the operator changed —
 *    and it matches the backend's `cache_key_part()`, which also drops
 *    defaults so `?period=30d` and `?period=30d&limit=20` are one cache entry.
 *
 * 3. **Garbage degrades, it does not throw.** These URLs get hand-edited and
 *    pasted into Slack. `?period=last_tuesday` opens on the default period; it
 *    does not blank the page and does not 422.
 *
 * 4. **Only what the view declares is emitted.** Navigating from Courier
 *    Performance to Product Performance must not carry `courier=bluedart` into
 *    a request that would silently ignore it — an ignored filter in a shared
 *    link is a number someone will misread.
 */

export const PERIODS = Object.freeze(['7d', '30d', '90d', 'mtd', 'qtd', 'ytd', 'custom']);
export const COMPARISONS = Object.freeze(['none', 'previous_period', 'previous_year']);
export const GRANULARITIES = Object.freeze(['hour', 'day', 'week', 'month']);

/** Caps copied from `filters.py` — see MAX_RANGE_DAYS / MAX_LIMIT there. */
export const MAX_RANGE_DAYS = 400;
export const MAX_HOURLY_RANGE_DAYS = 14;
export const MAX_LIMIT = 200;

/**
 * Longest window each preset can resolve to, used to reject `granularity=hour`
 * before the request is made. The backend only range-checks explicit dates, so
 * `?period=90d&granularity=hour` would pass validation and then ask for 2,160
 * buckets.
 */
const PRESET_MAX_SPAN_DAYS = {
  '7d': 7,
  '30d': 30,
  '90d': 90,
  mtd: 31,
  qtd: 92,
  ytd: 366,
};

const DAY_MS = 86_400_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// --- value parsers -------------------------------------------------------
// Each returns the parsed value, or `undefined` to mean "unusable, use the
// default". Returning `undefined` rather than throwing is what makes a
// hand-mangled URL degrade instead of 500.

const oneOf = (allowed) => (raw) => (allowed.includes(raw) ? raw : undefined);

function parseIsoDate(raw) {
  if (!ISO_DATE.test(raw)) return undefined;
  const d = new Date(`${raw}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return undefined;
  // Rejects 2025-02-30, which `Date` would happily roll forward to 02 March.
  return d.toISOString().slice(0, 10) === raw ? raw : undefined;
}

function parseBoundedInt(min, max) {
  return (raw) => {
    if (!/^-?\d+$/.test(raw)) return undefined;
    const n = Number(raw);
    return n >= min && n <= max ? n : undefined;
  };
}

function parseText(maxLength) {
  return (raw) => {
    const trimmed = raw.trim();
    // Over-length is dropped rather than truncated: a silently shortened SKU
    // would filter to the wrong product instead of to nothing.
    if (trimmed === '' || trimmed.length > maxLength) return undefined;
    return trimmed;
  };
}

function parseCountry(raw) {
  const trimmed = raw.trim().toUpperCase();
  return /^[A-Z]{2}$/.test(trimmed) ? trimmed : undefined;
}

function parseDimension(raw) {
  const trimmed = raw.trim();
  // A label the server maps to a column — never a column name itself. Bound
  // to a conservative charset so nothing exotic even leaves the browser.
  return /^[a-z][a-z0-9_]{0,39}$/.test(trimmed) ? trimmed : undefined;
}

/**
 * Every URL key, in the order it is written to the address bar.
 *
 * `capability` is the token a view must declare in its registry `filters` list
 * for the key to survive. `null` means the key is a shape control (what to
 * break down by, how many rows) rather than a filter, and is always allowed.
 */
const FIELDS = [
  { key: 'period', capability: 'date_range', default: '30d', parse: oneOf(PERIODS) },
  { key: 'date_from', capability: 'date_range', default: null, parse: parseIsoDate },
  { key: 'date_to', capability: 'date_range', default: null, parse: parseIsoDate },
  { key: 'comparison', capability: 'comparison', default: 'previous_period', parse: oneOf(COMPARISONS) },
  { key: 'granularity', capability: 'granularity', default: 'day', parse: oneOf(GRANULARITIES) },
  { key: 'dimension', capability: null, default: null, parse: parseDimension },
  { key: 'limit', capability: null, default: 20, parse: parseBoundedInt(1, MAX_LIMIT) },
  { key: 'product_id', capability: 'product', default: null, parse: parseBoundedInt(1, Number.MAX_SAFE_INTEGER) },
  { key: 'category_id', capability: 'category', default: null, parse: parseBoundedInt(1, Number.MAX_SAFE_INTEGER) },
  { key: 'sku', capability: 'sku', default: null, parse: parseText(64) },
  { key: 'customer_segment', capability: 'customer_segment', default: null, parse: parseText(32) },
  { key: 'new_or_returning', capability: 'new_or_returning', default: null, parse: oneOf(['new', 'returning']) },
  { key: 'source', capability: 'source', default: null, parse: parseText(64) },
  { key: 'medium', capability: 'medium', default: null, parse: parseText(64) },
  { key: 'campaign', capability: 'campaign', default: null, parse: parseText(120) },
  { key: 'device', capability: 'device', default: null, parse: parseText(32) },
  { key: 'country', capability: 'country', default: null, parse: parseCountry },
  { key: 'state', capability: 'state', default: null, parse: parseText(64) },
  { key: 'city', capability: 'city', default: null, parse: parseText(64) },
  { key: 'payment_method', capability: 'payment_method', default: null, parse: parseText(32) },
  { key: 'payment_gateway', capability: 'payment_gateway', default: null, parse: parseText(40) },
  { key: 'courier', capability: 'courier', default: null, parse: parseText(64) },
  { key: 'order_status', capability: 'order_status', default: null, parse: parseText(20) },
  { key: 'coupon', capability: 'coupon', default: null, parse: parseText(64) },
];

/** Every URL key this codec knows, in serialisation order. */
export const FILTER_KEYS = Object.freeze(FIELDS.map((f) => f.key));

/** The complete filter object with nothing set. Frozen — clone, don't mutate. */
export const DEFAULT_FILTERS = Object.freeze(
  Object.fromEntries(FIELDS.map((f) => [f.key, f.default])),
);

/**
 * Filter tokens some views declare that `AnalyticsFilters` has no field for.
 *
 * Declared here rather than silently ignored so the gap is greppable: a view
 * offering one of these in its registry entry cannot currently express it as a
 * query param, and the control should not be rendered until the backend model
 * grows the field.
 */
export const UNMAPPED_VIEW_FILTERS = Object.freeze([
  'channel',
  'fulfilment_status',
  'warehouse',
  'return_reason',
  'marketplace',
]);

// --- view capability handling -------------------------------------------

/**
 * The tokens a view declares. Accepts the registry view object (either
 * `filters` or `supported_filters`), a bare array, or `null`/`undefined` to
 * mean "no view context — allow every key".
 */
function declaredTokens(view) {
  if (view == null) return null;
  if (Array.isArray(view)) return view;
  const declared = view.supported_filters ?? view.filters;
  return Array.isArray(declared) ? declared : null;
}

function isAllowed(field, tokens) {
  if (field.capability === null) return true;
  if (tokens === null) return true;
  return tokens.includes(field.capability);
}

/** The URL keys this view honours — what the filter bar should render. */
export function supportedFilterKeys(view) {
  const tokens = declaredTokens(view);
  return FIELDS.filter((f) => isAllowed(f, tokens)).map((f) => f.key);
}

// --- normalisation -------------------------------------------------------

function spanDays(from, to) {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS);
}

/**
 * Enforce the cross-field rules so that whatever comes out is something the
 * backend will accept, and so that parse and serialise agree on a single
 * canonical shape (which is what makes the round trip stable).
 */
function normaliseWindow(f) {
  // The window is half-open [from, to); from >= to is empty, not a range.
  if (f.date_from && f.date_to && spanDays(f.date_from, f.date_to) <= 0) {
    f.date_from = null;
    f.date_to = null;
  }
  // Over the cap the backend errors rather than truncating. A hand-edited URL
  // asking for five years falls back to the default window instead.
  if (f.date_from && f.date_to && spanDays(f.date_from, f.date_to) > MAX_RANGE_DAYS) {
    f.date_from = null;
    f.date_to = null;
  }
  // `period=custom` without both ends is not a window.
  if (f.period === 'custom' && !(f.date_from && f.date_to)) {
    f.period = DEFAULT_FILTERS.period;
  }
  // Explicit dates only mean anything for `custom`; a preset resolves its own
  // window server-side. Dropping them keeps the URL honest and the round trip
  // idempotent.
  if (f.period !== 'custom') {
    f.date_from = null;
    f.date_to = null;
  }
  // Hourly buckets over a long window is an accidental table scan.
  if (f.granularity === 'hour') {
    const span =
      f.period === 'custom'
        ? spanDays(f.date_from, f.date_to)
        : (PRESET_MAX_SPAN_DAYS[f.period] ?? MAX_RANGE_DAYS);
    if (span > MAX_HOURLY_RANGE_DAYS) f.granularity = DEFAULT_FILTERS.granularity;
  }
  return f;
}

/**
 * Coerce any object into the canonical, complete filter shape for `view`.
 *
 * Unknown keys are dropped, invalid values fall back to their default, and
 * keys the view does not declare are reset — so state carried over from
 * another view disappears here rather than at the API boundary.
 */
export function normaliseFilters(filters, view) {
  const tokens = declaredTokens(view);
  const input = filters ?? {};
  const out = {};
  for (const field of FIELDS) {
    if (!isAllowed(field, tokens)) {
      out[field.key] = field.default;
      continue;
    }
    const raw = input[field.key];
    if (raw === undefined || raw === null || raw === '') {
      out[field.key] = field.default;
      continue;
    }
    const parsed = field.parse(String(raw));
    out[field.key] = parsed === undefined ? field.default : parsed;
  }
  return normaliseWindow(out);
}

// --- codec ---------------------------------------------------------------

function readParam(source, key) {
  if (source == null) return null;
  if (typeof source.get === 'function') return source.get(key);
  if (typeof source === 'string') return new URLSearchParams(source).get(key);
  const value = source[key];
  return value === undefined ? null : value;
}

/**
 * Read a complete filter object out of the URL.
 *
 * Accepts a `URLSearchParams`, a query string, or a plain object. Always
 * returns every key — callers can read `filters.limit` without a fallback.
 */
export function parseFilters(searchParams, view) {
  const raw = {};
  for (const field of FIELDS) {
    const value = readParam(searchParams, field.key);
    if (value !== null && value !== undefined) raw[field.key] = value;
  }
  return normaliseFilters(raw, view);
}

/** Write the non-default filters this view supports into a `URLSearchParams`. */
export function toSearchParams(filters, view) {
  const normalised = normaliseFilters(filters, view);
  const params = new URLSearchParams();
  for (const field of FIELDS) {
    const value = normalised[field.key];
    if (value === null || value === field.default) continue;
    params.set(field.key, String(value));
  }
  return params;
}

/** `toSearchParams` as a string, ready to hand to `navigate()`. */
export function toQueryString(filters, view) {
  return toSearchParams(filters, view).toString();
}

/** How many filters the operator has actually changed — for the "N active" chip. */
export function activeFilterCount(filters, view) {
  return [...toSearchParams(filters, view).keys()].length;
}

/**
 * Stable cache/query key fragment. Mirrors the intent of `cache_key_part()`:
 * two filter objects that mean the same thing produce the same string.
 */
export function filterKey(filters, view) {
  const params = toSearchParams(filters, view);
  params.sort();
  return params.toString();
}
