/**
 * Display formatting for analytics values. Pure, no React — runs under the
 * project's node-environment vitest config.
 *
 * The single rule this file exists to enforce:
 *
 *   A value the backend could not compute is `null`, and `null` renders as an
 *   em-dash. It NEVER renders as `0`, and never as `₹0.00`.
 *
 * That distinction is the whole point of the analytics contract. The backend
 * deliberately sends `null` for "not computable" (no denominator, source not
 * connected, window outside the watermark) and `0` for "computed, and the
 * answer is zero". Those are different facts. `formatPrice` in `lib/utils.js`
 * coerces anything non-finite to zero — correct for a product price, which
 * always exists, and exactly wrong here. So the missing check happens FIRST,
 * before any number ever reaches `Intl`.
 *
 * Percentages arrive already multiplied by 100 (every `pct` KPI formula in
 * `kpis.py` ends in `* 100`), so nothing here scales them again.
 */
import { formatPrice } from '@/lib/utils.js';
import { isMissing } from './viewState.js';

/** What a value that could not be computed looks like on screen. */
export const MISSING_DISPLAY = '—';

/** The FormatId set shared with the backend (charts, table columns, KPI units). */
export const FORMAT_IDS = Object.freeze([
  'money',
  'int',
  'pct',
  'days',
  'hours',
  'ratio',
  'compact',
  'text',
]);

/** Single-currency store; `currency_handling` in the KPI catalogue relies on it. */
export const DEFAULT_CURRENCY = 'INR';
/** Lakh/crore grouping is what an Indian operator reads without re-counting digits. */
export const DEFAULT_LOCALE = 'en-IN';

let storeCurrency = DEFAULT_CURRENCY;

/** Override the store currency once at boot if settings ever say otherwise. */
export function setStoreCurrency(code) {
  storeCurrency = typeof code === 'string' && code.length === 3 ? code.toUpperCase() : DEFAULT_CURRENCY;
}

export function getStoreCurrency() {
  return storeCurrency;
}

/**
 * Coerce to a finite number, or `null`.
 *
 * Returns `null` — not `0` — for anything unusable, so every caller funnels
 * into the same em-dash. Numeric strings are accepted because the API sends
 * `Decimal` columns as strings to avoid float drift on money.
 */
function toFiniteNumber(value) {
  if (isMissing(value)) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '') return null;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function numberFormat(value, options, locale = DEFAULT_LOCALE) {
  return new Intl.NumberFormat(locale, options).format(value);
}

/** Currency. Delegates to the shared `formatPrice` for the default locale. */
export function formatMoney(value, { currency = storeCurrency, locale = DEFAULT_LOCALE } = {}) {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING_DISPLAY;
  if (locale === DEFAULT_LOCALE) return formatPrice(n, currency);
  return numberFormat(n, { style: 'currency', currency, minimumFractionDigits: 2 }, locale);
}

/** Whole counts — orders, units, customers. Never fractional. */
export function formatInt(value, { locale = DEFAULT_LOCALE } = {}) {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING_DISPLAY;
  return numberFormat(n, { maximumFractionDigits: 0 }, locale);
}

/** Already-scaled percentage: `12.5` renders as `12.5%`. */
export function formatPct(value, { locale = DEFAULT_LOCALE, decimals = 1 } = {}) {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING_DISPLAY;
  return `${numberFormat(n, { maximumFractionDigits: decimals, minimumFractionDigits: 0 }, locale)}%`;
}

function formatDuration(value, unitSingular, unitPlural, locale) {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING_DISPLAY;
  const text = numberFormat(n, { maximumFractionDigits: 1, minimumFractionDigits: 0 }, locale);
  return `${text} ${n === 1 ? unitSingular : unitPlural}`;
}

export function formatDays(value, { locale = DEFAULT_LOCALE } = {}) {
  return formatDuration(value, 'day', 'days', locale);
}

export function formatHours(value, { locale = DEFAULT_LOCALE } = {}) {
  return formatDuration(value, 'hr', 'hrs', locale);
}

/** ROAS, LTV:CAC, inventory turns — all read naturally as a multiplier. */
export function formatRatio(value, { locale = DEFAULT_LOCALE } = {}) {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING_DISPLAY;
  return `${numberFormat(n, { maximumFractionDigits: 2, minimumFractionDigits: 1 }, locale)}×`;
}

/** Axis ticks and dense tiles. en-IN compacts to K / L / Cr, not K / M / B. */
export function formatCompact(value, { locale = DEFAULT_LOCALE } = {}) {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING_DISPLAY;
  return numberFormat(n, { notation: 'compact', maximumFractionDigits: 1 }, locale);
}

/** Labels and dimension values. An empty string is missing, not a blank cell. */
export function formatText(value) {
  if (isMissing(value)) return MISSING_DISPLAY;
  const text = String(value).trim();
  return text === '' ? MISSING_DISPLAY : text;
}

const FORMATTERS = {
  money: formatMoney,
  int: formatInt,
  pct: formatPct,
  days: formatDays,
  hours: formatHours,
  ratio: formatRatio,
  compact: formatCompact,
  text: formatText,
};

/**
 * Format `value` for the given FormatId.
 *
 * An unknown `formatId` falls back to `text` rather than throwing: format ids
 * come from a generated contract, and a registry that added a format the
 * frontend has not learned yet should render a slightly plain number, not
 * white-screen the page.
 */
export function formatValue(value, formatId = 'text', options = {}) {
  const formatter = FORMATTERS[formatId] ?? formatText;
  return formatter(value, options);
}

/** True when `formatValue` would return the em-dash. */
export function isMissingDisplay(text) {
  return text === MISSING_DISPLAY;
}
