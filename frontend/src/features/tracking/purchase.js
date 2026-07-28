/**
 * The browser purchase path.
 *
 * A success-page URL is not proof of purchase — only a backend-confirmed order
 * status is. That is why `analytics.ga4_purchase_delivery` defaults to
 * `server_only`: the authoritative `purchase` event is written by the server
 * inside the order-paid transaction, where the money is known to have moved.
 *
 * **Suppression happens here, at the boundary.** In `server_only` mode this
 * module returns before an event object exists — nothing is built, nothing is
 * pushed, nothing lands in `window.dataLayer` for a container rule to pick up
 * later. "Push it and let a GTM trigger ignore it" is not suppression: the
 * event is still in the data layer, one careless trigger away from being sent,
 * and it would then be a *second* purchase for the same order.
 *
 * When both sides do send (`both`), they must agree byte for byte on
 * `transaction_id` or GA4's deduplication silently fails and revenue inflates
 * in a way that looks like growth. `transactionIdFor` is the shared rule.
 */
import { Ev, pushEvent, toItem, transactionIdFor } from './dataLayer.js';
import { SETTING_KEYS, readSetting } from './config.js';

export const PURCHASE_DELIVERY = Object.freeze({
  /** Default. The server owns the event; the browser stays silent. */
  SERVER_ONLY: 'server_only',
  /** Browser owns it. Loses every purchase where the tab closed on redirect. */
  BROWSER_ONLY: 'browser_only',
  /** Both send; GA4 deduplicates on `transaction_id`. */
  BOTH: 'both',
});

export const DEFAULT_PURCHASE_DELIVERY = PURCHASE_DELIVERY.SERVER_ONLY;

const DELIVERY_ALIASES = Object.freeze({
  server: PURCHASE_DELIVERY.SERVER_ONLY,
  server_only: PURCHASE_DELIVERY.SERVER_ONLY,
  backend: PURCHASE_DELIVERY.SERVER_ONLY,
  browser: PURCHASE_DELIVERY.BROWSER_ONLY,
  client: PURCHASE_DELIVERY.BROWSER_ONLY,
  browser_only: PURCHASE_DELIVERY.BROWSER_ONLY,
  both: PURCHASE_DELIVERY.BOTH,
  dual: PURCHASE_DELIVERY.BOTH,
});

/** Namespace for the per-transaction "already sent" marker. */
const SENT_KEY_PREFIX = 'wv.purchase.';

/**
 * The configured mode. Anything unrecognised resolves to `server_only`: a
 * misconfigured setting must not turn into a duplicate revenue stream.
 */
export function purchaseDeliveryMode(settings) {
  const raw = readSetting(settings, SETTING_KEYS.purchaseDelivery);
  if (raw === undefined || raw === null) return DEFAULT_PURCHASE_DELIVERY;
  return DELIVERY_ALIASES[String(raw).trim().toLowerCase()] || DEFAULT_PURCHASE_DELIVERY;
}

/** Pure: may the browser emit `purchase` in this mode? */
export function browserPurchaseAllowed(mode) {
  return mode === PURCHASE_DELIVERY.BROWSER_ONLY || mode === PURCHASE_DELIVERY.BOTH;
}

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * Build the GA4 `purchase` payload from an order.
 *
 * Pure and exported so the shape can be compared against the server's without
 * a browser. Only named fields are read: an order object carries
 * `shipping_address`, `shipping_address_snapshot` and a billing pair, and
 * spreading it would put a customer's address into GA4 — `assertNoPii` would
 * catch it, but building the payload by allowlist means it never gets that far.
 */
export function buildPurchaseParams(order = {}, opts = {}) {
  const items = Array.isArray(order.items) ? order.items : [];
  return {
    transaction_id: transactionIdFor(order.order_number, order.id),
    value: num(order.total_amount ?? order.total) ?? 0,
    currency: order.currency || 'INR',
    tax: num(order.tax_amount),
    shipping: num(order.shipping_amount),
    coupon: order.coupon_code || undefined,
    items: items.map((line, index) =>
      toItem(line, {
        index,
        quantity: num(line.quantity),
        item_list_id: opts.item_list_id,
        item_list_name: opts.item_list_name,
      })
    ),
  };
}

function sessionStore() {
  try {
    if (typeof sessionStorage === 'undefined' || !sessionStorage) return null;
    return sessionStorage;
  } catch {
    return null;
  }
}

/**
 * A reload of the confirmation page is not a second purchase. Marked in
 * `sessionStorage` rather than memory because the payment return is a full
 * page load, so an in-memory flag would be gone exactly when it is needed.
 */
function alreadySent(transactionId) {
  const store = sessionStore();
  if (!store) return false;
  try {
    return store.getItem(SENT_KEY_PREFIX + transactionId) !== null;
  } catch {
    return false;
  }
}

function markSent(transactionId) {
  const store = sessionStore();
  if (!store) return;
  try {
    store.setItem(SENT_KEY_PREFIX + transactionId, String(Date.now()));
  } catch {
    // Storage unavailable: at worst a reload re-sends, and GA4 deduplicates on
    // transaction_id. Losing the marker is recoverable; failing the page is not.
  }
}

/**
 * Report a purchase from the browser — or, in `server_only`, decline to.
 *
 * Returns `{ delivered, reason, payload }`. `payload` is `null` whenever
 * nothing was sent, including the suppressed case, so a caller cannot
 * accidentally forward a "suppressed" event somewhere else.
 */
export function pushPurchase(order, { settings, ...opts } = {}) {
  const mode = purchaseDeliveryMode(settings);
  if (!browserPurchaseAllowed(mode)) {
    // The boundary. Nothing is built and nothing is pushed — in server_only the
    // browser has no purchase event at all, not a suppressed one.
    return { delivered: false, reason: `suppressed:${mode}`, payload: null };
  }
  if (!order || (order.id === undefined && !order.order_number)) {
    return { delivered: false, reason: 'no-order', payload: null };
  }

  const params = buildPurchaseParams(order, opts);
  if (alreadySent(params.transaction_id)) {
    return { delivered: false, reason: 'already-sent', payload: null };
  }

  const payload = pushEvent(Ev.PURCHASE, params);
  if (!payload) return { delivered: false, reason: 'not-pushed', payload: null };

  markSent(params.transaction_id);
  return { delivered: true, reason: mode, payload };
}
