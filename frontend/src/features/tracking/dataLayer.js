/**
 * The versioned browser data layer — the JS mirror of
 * `backend/app/services/analytics/tracking_events.py`.
 *
 * Both sides emit `purchase`. If they disagree about the event name, the
 * parameter shape or the transaction id, the Data Reconciliation view compares
 * two things that were never comparable and reports a discrepancy that isn't
 * real. So every constant below is a copy of the Python one, and
 * `__tests__/contract.test.js` fails the build if the two drift apart.
 *
 * Three rules this module exists to enforce, matching the backend's
 * ==================================================================
 *
 * **1. No PII leaves the building.** `assertNoPii` is the same substring
 * denylist with the same exact-match escapes, and it *throws* rather than
 * stripping the field. A silently stripped field hides the bug instead of
 * fixing it — and the caller that built the payload goes on being copied.
 *
 * **2. `transaction_id` has exactly one derivation rule.** `transactionIdFor`
 * is that rule, byte-identical to `transaction_id_for` in Python.
 *
 * **3. Nothing enters the data layer before consent.** GTM replays the entire
 * `dataLayer` array when it loads, so an event pushed while consent was denied
 * is not "buffered locally" — it is transmitted retroactively the moment the
 * customer says yes. `pushEvent` therefore refuses to push without analytics
 * consent; only Consent Mode signals and GTM's own control messages, which are
 * the consent mechanism itself, take the ungated `pushRaw` path.
 */
import { hasAnalyticsConsent } from './consent.js';

/**
 * Bumped when an event's parameter shape changes in a way a consumer could
 * notice. Sent as `event_schema_version`. MUST equal the backend's
 * `SCHEMA_VERSION`.
 */
export const SCHEMA_VERSION = 1;

/** Standard GA4 e-commerce events. Names are GA4's, not ours. */
export const Ev = Object.freeze({
  PAGE_VIEW: 'page_view',
  VIEW_ITEM_LIST: 'view_item_list',
  SELECT_ITEM: 'select_item',
  VIEW_ITEM: 'view_item',
  ADD_TO_WISHLIST: 'add_to_wishlist',
  ADD_TO_CART: 'add_to_cart',
  REMOVE_FROM_CART: 'remove_from_cart',
  VIEW_CART: 'view_cart',
  BEGIN_CHECKOUT: 'begin_checkout',
  ADD_SHIPPING_INFO: 'add_shipping_info',
  ADD_PAYMENT_INFO: 'add_payment_info',
  PURCHASE: 'purchase',
  REFUND: 'refund',
  VIEW_PROMOTION: 'view_promotion',
  SELECT_PROMOTION: 'select_promotion',
  SEARCH: 'search',
  LOGIN: 'login',
  SIGN_UP: 'sign_up',
});

/** Business events with no GA4 equivalent. None collides with a reserved name. */
export const BizEv = Object.freeze({
  PAYMENT_ATTEMPT: 'payment_attempt',
  PAYMENT_FAILED: 'payment_failed',
  COUPON_APPLIED: 'coupon_applied',
  COUPON_FAILED: 'coupon_failed',
  SEARCH_NO_RESULTS: 'search_no_results',
  ORDER_CANCELLED: 'order_cancelled',
  REVIEW_SUBMITTED: 'review_submitted',
  SUPPORT_STARTED: 'support_started',
});

export const EVENT_NAMES = Object.freeze(Object.values(Ev).slice().sort());
export const BUSINESS_EVENT_NAMES = Object.freeze(
  Object.values(BizEv).slice().sort()
);

const KNOWN_EVENTS = new Set([...EVENT_NAMES, ...BUSINESS_EVENT_NAMES]);

/**
 * Item-level fields, GA4's canonical names. `item_id` is the SKU — not the
 * numeric product id — because a SKU is what a merchandiser recognises in a
 * GA4 report, and it survives a database migration.
 */
export const ITEM_FIELDS = Object.freeze([
  'item_id',
  'item_name',
  'item_brand',
  'item_category',
  'item_category2',
  'item_category3',
  'item_variant',
  'item_list_id',
  'item_list_name',
  'index',
  'price',
  'quantity',
  'discount',
  'coupon',
]);

/** Parameters that must be present for an event to be worth sending at all. */
export const REQUIRED_PARAMS = Object.freeze({
  [Ev.PURCHASE]: Object.freeze(['transaction_id', 'value', 'currency', 'items']),
  [Ev.REFUND]: Object.freeze(['transaction_id', 'currency']),
  [Ev.ADD_TO_CART]: Object.freeze(['currency', 'value', 'items']),
  [Ev.BEGIN_CHECKOUT]: Object.freeze(['currency', 'value', 'items']),
  [Ev.VIEW_ITEM]: Object.freeze(['currency', 'value', 'items']),
  [Ev.SEARCH]: Object.freeze(['search_term']),
});

/**
 * Parameter names that must NEVER appear in an outbound event, at any depth.
 * Checked by substring, deliberately: `customer_email`, `billing_email` and
 * `email_address` are all caught by "email".
 */
export const PII_DENYLIST = Object.freeze([
  'email',
  'phone',
  'mobile',
  'name', // deliberately broad — see PII_ALLOWED_EXACT for the escapes
  'address',
  'street',
  'pincode',
  'postcode',
  'zip',
  'password',
  'otp',
  'card',
  'cvv',
  'upi',
  'gstin',
  'pan',
  'dob',
  'birth',
]);

/**
 * GA4's own vocabulary collides with the denylist. These exact keys are the
 * documented exceptions, matched exactly rather than by substring so
 * `item_name` passes while `customer_name` does not.
 */
export const PII_ALLOWED_EXACT = Object.freeze([
  'item_name',
  'item_list_name',
  'promotion_name',
  'creative_name',
  'currency',
]);

const ALLOWED_EXACT = new Set(PII_ALLOWED_EXACT);

/**
 * Parameters whose value is typed by a customer rather than chosen by us.
 * A key-based denylist cannot see into these — see `looksLikePii`.
 */
export const FREE_TEXT_PARAMS = Object.freeze(['search_term']);

/** Raised when an outbound payload contains a denylisted parameter. */
export class PiiLeak extends Error {
  constructor(message, offenders = []) {
    super(message);
    this.name = 'PiiLeak';
    this.offenders = offenders;
  }
}

/** Raised when an event does not match the contract this module mirrors. */
export class ContractViolation extends Error {
  constructor(message) {
    super(message);
    this.name = 'ContractViolation';
  }
}

const IS_DEV = (() => {
  try {
    return Boolean(import.meta.env && import.meta.env.DEV);
  } catch {
    return false;
  }
})();

function isPlainish(value) {
  return value !== null && typeof value === 'object';
}

/** Mirrors `_offending_keys` — same traversal, same substring rule. */
export function offendingKeys(payload, path = '') {
  const found = [];
  if (Array.isArray(payload)) {
    payload.forEach((value, i) => {
      found.push(...offendingKeys(value, `${path}[${i}]`));
    });
    return found;
  }
  if (isPlainish(payload)) {
    for (const [key, value] of Object.entries(payload)) {
      const here = path ? `${path}.${key}` : String(key);
      const lowered = String(key).toLowerCase();
      if (!ALLOWED_EXACT.has(lowered) && PII_DENYLIST.some((bad) => lowered.includes(bad))) {
        found.push(here);
      }
      found.push(...offendingKeys(value, here));
    }
  }
  return found;
}

/**
 * Throw if an outbound payload carries a denylisted parameter.
 *
 * Called at the delivery boundary, so it catches a leak introduced by any
 * caller rather than only the ones someone remembered to review. Deliberately
 * fatal: a payload carrying PII means the *caller* is wrong, and silently
 * sanitising it would let the bug ship and recur everywhere that caller is
 * copied. Do not wrap this in a swallowing try/catch.
 */
export function assertNoPii(payload) {
  const offenders = offendingKeys(payload);
  if (offenders.length) {
    throw new PiiLeak(
      'refusing to send an analytics event containing PII-shaped parameters: ' +
        `${JSON.stringify(offenders.slice().sort())}. Remove them at the source — ` +
        'this is not sanitised automatically, because a silently stripped field ' +
        'hides the bug instead of fixing it.',
      offenders
    );
  }
}

const EMAIL_RE = /[^\s@,;]+@[^\s@,;]+\.[A-Za-z]{2,}/;
const PHONE_RE = /\+?\d[\d\s().-]{8,}\d/;

/**
 * Value-level screen for the handful of parameters a customer types.
 *
 * The backend denylist is key-based, and this function deliberately does NOT
 * change that — `assertNoPii` stays a byte-for-byte mirror so the two sides
 * never disagree about what is sendable. This is the extra check for the one
 * case a key cannot catch: a customer typing their own email or phone number
 * into a search box, which is customer error rather than developer error and
 * so must not throw and take the page down with it.
 */
export function looksLikePii(value) {
  if (value === null || value === undefined) return false;
  const text = String(value);
  if (EMAIL_RE.test(text)) return true;
  const match = text.match(PHONE_RE);
  return Boolean(match && match[0].replace(/\D/g, '').length >= 10);
}

function contractViolation(message) {
  if (IS_DEV) throw new ContractViolation(message);
  if (typeof console !== 'undefined') console.warn(`[tracking] ${message}`);
  return null;
}

/**
 * Build the exact object that would be pushed — pure, so the whole contract is
 * exercised by the node-environment test suite without a DOM.
 *
 * Returns `null` when the event is dropped (unknown name, missing required
 * parameter, customer-typed PII in a free-text parameter). Throws `PiiLeak`
 * when a *developer* put a denylisted key in the payload.
 */
export function buildEvent(name, params = {}) {
  if (!KNOWN_EVENTS.has(name)) {
    return contractViolation(
      `unknown event "${name}" — it is not in the shared contract, so the ` +
        'server has no matching definition and reconciliation cannot pair it.'
    );
  }

  assertNoPii(params);

  const required = REQUIRED_PARAMS[name] || [];
  const missing = required.filter(
    (key) => params[key] === undefined || params[key] === null
  );
  if (missing.length) {
    return contractViolation(
      `event "${name}" is missing required parameters ${JSON.stringify(missing)}`
    );
  }

  const tainted = FREE_TEXT_PARAMS.filter((key) => looksLikePii(params[key]));
  if (tainted.length) {
    if (typeof console !== 'undefined') {
      console.warn(
        `[tracking] dropped "${name}": ${JSON.stringify(tainted)} contains a ` +
          'value shaped like an email or phone number. The whole event is ' +
          'dropped rather than edited — a half-sent event is a lie.'
      );
    }
    return null;
  }

  return { event: name, event_schema_version: SCHEMA_VERSION, ...params };
}

/** The global `dataLayer` array, created on demand. `null` without a window. */
export function getDataLayer() {
  if (typeof window === 'undefined') return null;
  if (!Array.isArray(window.dataLayer)) window.dataLayer = [];
  return window.dataLayer;
}

/**
 * Low-level push. Used only by GTM's own control messages and Consent Mode
 * signals — everything that describes a customer goes through `pushEvent`.
 * Still PII-checked: there is no path into the data layer that skips the guard.
 */
export function pushRaw(entry) {
  assertNoPii(entry);
  const dl = getDataLayer();
  if (!dl) return null;
  dl.push(entry);
  return entry;
}

/**
 * Push a contract event. Returns the pushed payload, or `null` when nothing
 * was pushed (no consent, no window, contract violation).
 */
export function pushEvent(name, params = {}) {
  if (!hasAnalyticsConsent()) return null;
  const payload = buildEvent(name, params);
  if (!payload) return null;
  return pushRaw(payload);
}

function numberOrUndefined(value) {
  if (value === undefined || value === null || value === '') return undefined;
  const num = Number(value);
  return Number.isFinite(num) ? num : undefined;
}

function itemId(product) {
  if (product.sku) return String(product.sku);
  const id = product.product_id ?? product.id;
  return id === undefined || id === null ? undefined : String(id);
}

/**
 * Shape a catalogue product, cart line or order line into a GA4 item.
 *
 * Only `ITEM_FIELDS` survive, so an unrelated field on a product object cannot
 * ride along into GA4. `opts` is PII-checked *before* shaping: dropping an
 * unknown key is contract-shaping, but dropping `customer_email` silently
 * would hide exactly the bug this layer exists to surface.
 */
export function toItem(product = {}, opts = {}) {
  assertNoPii(opts);

  const base = {
    item_id: itemId(product),
    item_name: product.name ?? product.item_name ?? product.title,
    item_brand: product.brand,
    item_category: product.category_name ?? product.category?.name,
    item_variant: product.flavour ?? product.variant,
    price: numberOrUndefined(product.price ?? product.unit_price),
    quantity: numberOrUndefined(product.quantity),
    discount: numberOrUndefined(product.discount),
    coupon: product.coupon_code ?? product.coupon,
  };

  const item = {};
  for (const field of ITEM_FIELDS) {
    const value = opts[field] !== undefined ? opts[field] : base[field];
    if (value !== undefined && value !== null && value !== '') item[field] = value;
  }
  return item;
}

/** `toItem` across a list, filling `index` (GA4's list position) when absent. */
export function toItems(products, opts = {}) {
  if (!Array.isArray(products)) return [];
  return products.map((product, i) => toItem(product, { index: i, ...opts }));
}

/**
 * The ONE rule for deriving a GA4 transaction id from an order.
 *
 * `orders.order_number` is nullable by design (allocated after the flush that
 * assigns the id, so it can embed it), and the rest of the codebase falls back
 * to `ORD{id}`. Browser and server must use this same function, or GA4 receives
 * two ids for one order and deduplication silently fails — inflating revenue in
 * a way that looks like growth. Mirrors `transaction_id_for` exactly, including
 * the falsy-empty-string fallback.
 */
export function transactionIdFor(orderNumber, orderId) {
  return orderNumber || `ORD${orderId}`;
}

/** The contract as data — what `__tests__/contract.test.js` compares. */
export function contractSnapshot() {
  return {
    schema_version: SCHEMA_VERSION,
    events: EVENT_NAMES.slice(),
    business_events: BUSINESS_EVENT_NAMES.slice(),
    item_fields: ITEM_FIELDS.slice(),
    required_params: Object.fromEntries(
      Object.keys(REQUIRED_PARAMS)
        .sort()
        .map((key) => [key, REQUIRED_PARAMS[key].slice()])
    ),
    pii_denylist: PII_DENYLIST.slice(),
    pii_allowed_exact: PII_ALLOWED_EXACT.slice().sort(),
  };
}
