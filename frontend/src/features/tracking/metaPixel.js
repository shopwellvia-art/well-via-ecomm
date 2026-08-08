/**
 * Meta (Facebook/Instagram) Pixel — ad attribution and conversion optimisation.
 *
 * Pixel `1335939612023667`. Everything the pixel does on this site is in this
 * one file: pages call the named helpers below and never touch `fbq`, so
 * removing the pixel or swapping it for another ad platform is a one-file edit.
 *
 * **The bootstrap is bundled, not an inline `<script>`.** Meta's copy-paste
 * snippet is inline JavaScript from a foreign origin, and shipping it would
 * force `'unsafe-inline'` into the production `script-src` — retiring the CSP as
 * an XSS defence for every other script on the page. This module does exactly
 * what the snippet does (define `fbq`, queue calls, append the tag) from a file
 * the CSP can allow by origin. Same reasoning as `gtm.js`; see that file.
 *
 * Four gates, all of which must pass before a byte is requested from
 * facebook.net:
 *
 *   1. **a production build** — see PROD gate below,
 *   2. a syntactically valid pixel id,
 *   3. **marketing** consent granted by the customer,
 *   4. the route is not admin/staff/internal.
 *
 * On (1): unlike the GTM container id, the pixel id is compiled in rather than
 * typed by an admin, so there is no "unset in dev" state to rely on. Without a
 * PROD gate every `npm run dev` session and every local test order would reach
 * the live pixel, and Meta would learn that our typical buyer is a developer
 * placing ₹1 test orders — which is worse than no data, because ad delivery
 * optimises against it. Dev silence is therefore deliberate. To see the pixel
 * fire locally: `npm run build && npm run preview`.
 *
 * On (3): **marketing**, not analytics. The pixel exists to attribute and
 * optimise advertising, which is exactly what the consent banner's Marketing
 * category describes ("lets advertising platforms know that a visit or a
 * purchase came from an ad they showed you"). Loading it under analytics
 * consent would collect for a purpose the customer was not asked about.
 *
 * Nothing here runs on import: this module loads no script, sets no cookie and
 * contacts nobody until a caller invokes `loadMetaPixel` and the gates pass.
 */
import { assertNoPii, transactionIdFor } from './dataLayer.js';
import { getConsent } from './consent.js';
import { isInternalRoute, resolvePath } from './config.js';

/**
 * Our pixel id. Overridable at build time (`VITE_META_PIXEL_ID`) so a staging
 * deploy can point at a throwaway pixel without a code change.
 *
 * Not a secret — a pixel id is readable in the page source of every site that
 * has one. Access to the data is protected by the Meta Business account, not by
 * this string.
 */
export const META_PIXEL_ID = readEnv('VITE_META_PIXEL_ID') || '1335939612023667';

export const META_SCRIPT_ID = 'wv-meta-pixel';
export const META_SCRIPT_ORIGIN = 'https://connect.facebook.net';
export const META_SCRIPT_SRC = `${META_SCRIPT_ORIGIN}/en_US/fbevents.js`;

/**
 * A pixel id is a numeric string. Validated because it is passed to a foreign
 * script as an account identifier: a typo'd id silently reports our whole funnel
 * into someone else's account, which no error would ever surface.
 */
export const META_PIXEL_ID_RE = /^\d{10,20}$/;

/** The standard events this site sends. Names are Meta's, spelling included. */
export const MetaEv = Object.freeze({
  PAGE_VIEW: 'PageView',
  VIEW_CONTENT: 'ViewContent',
  ADD_TO_CART: 'AddToCart',
  INITIATE_CHECKOUT: 'InitiateCheckout',
  PURCHASE: 'Purchase',
  COMPLETE_REGISTRATION: 'CompleteRegistration',
  LEAD: 'Lead',
});

/** Namespace for the per-transaction "already sent" marker. */
const SENT_KEY_PREFIX = 'wv.metapixel.purchase.';

let injected = false;

function readEnv(key) {
  try {
    const value = import.meta.env?.[key];
    return typeof value === 'string' && value.trim() !== '' ? value.trim() : null;
  } catch {
    return null;
  }
}

function isProductionBuild() {
  try {
    return Boolean(import.meta.env?.PROD);
  } catch {
    return false;
  }
}

/** True once the tag has been appended in this page's lifetime. */
export function isMetaPixelLoaded() {
  return injected;
}

/**
 * Should the pixel load? Pure — takes the four inputs and returns a reason
 * string, so each gate is assertable on its own without a DOM or a build.
 */
export function shouldLoadMetaPixel({
  pixelId,
  marketingConsent,
  pathname,
  production = isProductionBuild(),
} = {}) {
  if (!production) return { ok: false, reason: 'not-production' };
  if (!pixelId) return { ok: false, reason: 'no-pixel-id' };
  if (!META_PIXEL_ID_RE.test(String(pixelId))) return { ok: false, reason: 'invalid-pixel-id' };
  if (!marketingConsent) return { ok: false, reason: 'no-consent' };
  if (isInternalRoute(pathname)) return { ok: false, reason: 'internal-route' };
  return { ok: true, reason: 'ok' };
}

function hasMarketingConsent(consent) {
  const record = consent === undefined ? getConsent() : consent;
  return record?.categories?.marketing === true;
}

/**
 * Define `window.fbq` and its call queue.
 *
 * This is Meta's snippet, rewritten as a module. The queue matters: helpers can
 * fire an event in the same tick the tag is appended, and `fbevents.js` replays
 * `fbq.queue` once it finishes loading. `fbq.callMethod` is what the real script
 * installs over the top — its presence is how the shim knows to stop queueing.
 */
function ensureFbq(win) {
  if (typeof win.fbq === 'function') return win.fbq;

  const shim = function fbq(...args) {
    if (shim.callMethod) shim.callMethod.apply(shim, args);
    else shim.queue.push(args);
  };
  shim.queue = [];
  shim.push = shim;
  shim.loaded = true;
  shim.version = '2.0';

  win.fbq = shim;
  if (!win._fbq) win._fbq = shim;
  return shim;
}

/**
 * Load the pixel if all four gates pass. Idempotent: every route change calls
 * this and nothing is appended after the first success.
 *
 * Returns `{ loaded, reason }` — `loaded: false` carries the gate that stopped
 * it, so a caller can log why nothing is being collected.
 */
export function loadMetaPixel({ settings, pathname, consent, production } = {}) {
  // `settings` is accepted (and ignored) so this loader is call-compatible with
  // loadGtm/loadClarity inside initTracking. The pixel id is compiled in rather
  // than admin-configurable — moving it into settings needs a new
  // `analytics.*` key on the backend integrations endpoint, which is the only
  // write path for those (the generic PATCH /settings rejects them).
  void settings;

  const path = resolvePath(pathname);
  const gate = shouldLoadMetaPixel({
    pixelId: META_PIXEL_ID,
    marketingConsent: hasMarketingConsent(consent),
    pathname: path,
    ...(production === undefined ? {} : { production }),
  });
  if (!gate.ok) return { loaded: false, reason: gate.reason };

  if (injected) return { loaded: true, reason: 'already-loaded' };

  const doc = typeof document === 'undefined' ? null : document;
  const win = typeof window === 'undefined' ? null : window;
  if (!doc || !win || typeof doc.createElement !== 'function') {
    return { loaded: false, reason: 'no-document' };
  }
  if (typeof doc.getElementById === 'function' && doc.getElementById(META_SCRIPT_ID)) {
    injected = true; // already appended by someone else — do not double-init
    return { loaded: true, reason: 'already-loaded' };
  }

  const parent = doc.head || doc.body;
  if (!parent || typeof parent.appendChild !== 'function') {
    return { loaded: false, reason: 'no-document' };
  }

  const fbq = ensureFbq(win);
  fbq('init', META_PIXEL_ID);

  const script = doc.createElement('script');
  script.async = true;
  script.id = META_SCRIPT_ID;
  script.src = META_SCRIPT_SRC;
  parent.appendChild(script);
  injected = true;
  return { loaded: true, reason: 'ok' };
}

/**
 * Send one event, if the pixel is loaded.
 *
 * Silent no-op when it is not — a blocked, unconsented or dev-mode pixel must
 * never break a page, and roughly 20–30% of visitors block `fbevents.js`
 * outright. Every payload is PII-checked with the same `assertNoPii` the GA4
 * path uses, so one denylist governs everything that leaves the browser.
 *
 * `opts.eventID` is Meta's deduplication key: repeats of the same name + id
 * inside ~48 hours are dropped. It is also the join key for a future
 * server-side Conversions API, which is why purchases carry one from day one.
 */
export function metaTrack(event, params, opts) {
  if (!injected) return { sent: false, reason: 'not-loaded' };
  const win = typeof window === 'undefined' ? null : window;
  if (!win || typeof win.fbq !== 'function') return { sent: false, reason: 'no-fbq' };

  const payload = params === undefined ? undefined : params;
  if (payload !== undefined) assertNoPii(payload);

  if (payload === undefined) win.fbq('track', event);
  else if (opts === undefined) win.fbq('track', event, payload);
  else win.fbq('track', event, payload, opts);

  return { sent: true, reason: 'ok', event, params: payload ?? null };
}

// ---------------------------------------------------------------------------
// The seven standard events
// ---------------------------------------------------------------------------

/**
 * Our catalog id convention: the bare product id as a string.
 *
 * A string, not a number, because Meta matches `content_ids` against the
 * product catalog's ids as text — `42` and `'42'` are not the same key there.
 */
function contentId(id) {
  return id === undefined || id === null ? null : String(id);
}

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * `PageView` for the current route.
 *
 * Fired from `initTracking`, which already re-runs on every navigation — this
 * app is a single-page app, so without a per-route call Meta would record one
 * PageView for an entire visit.
 */
export function metaPageView({ pathname } = {}) {
  const path = resolvePath(pathname);
  if (path === null || isInternalRoute(path)) return { sent: false, reason: 'internal-route' };
  return metaTrack(MetaEv.PAGE_VIEW);
}

/**
 * `ViewContent` — a product detail page rendered with its data.
 *
 * Deliberately no `content_name`: the shared PII denylist matches "name" by
 * substring, and `content_name` is not one of its documented exceptions. Adding
 * it there would break the byte-for-byte mirror with the backend denylist for a
 * field Meta does not need — it resolves names from the catalog by id.
 */
export function trackViewContent(product) {
  const id = contentId(product?.id);
  if (!id) return { sent: false, reason: 'no-product' };
  return metaTrack(MetaEv.VIEW_CONTENT, {
    content_type: 'product',
    content_ids: [id],
    value: num(product.price) ?? 0,
    currency: 'INR',
  });
}

/** `AddToCart` — one line added. */
export function trackAddToCart({ productId, quantity = 1, price } = {}) {
  const id = contentId(productId);
  if (!id) return { sent: false, reason: 'no-product' };
  const qty = num(quantity) ?? 1;
  const unit = num(price);
  return metaTrack(MetaEv.ADD_TO_CART, {
    content_type: 'product',
    content_ids: [id],
    contents: [{ id, quantity: qty }],
    num_items: qty,
    // Omitted rather than sent as 0 when the caller has no price: a 0-value
    // AddToCart is worse than none, because value-based optimisation would
    // learn that our cart adds are worthless.
    ...(unit === undefined ? {} : { value: unit * qty, currency: 'INR' }),
  });
}

/** `InitiateCheckout` — the checkout page opened with a non-empty cart. */
export function trackInitiateCheckout(cart) {
  const items = Array.isArray(cart?.items) ? cart.items : [];
  if (!items.length) return { sent: false, reason: 'empty-cart' };
  const contents = items
    .map((line) => ({
      id: contentId(line.product_id ?? line.id),
      quantity: num(line.quantity) ?? 1,
    }))
    .filter((entry) => entry.id);
  return metaTrack(MetaEv.INITIATE_CHECKOUT, {
    content_type: 'product',
    content_ids: contents.map((entry) => entry.id),
    contents,
    num_items: contents.reduce((sum, entry) => sum + entry.quantity, 0),
    value: num(cart.total ?? cart.subtotal) ?? 0,
    currency: cart.currency || 'INR',
  });
}

function sessionStore() {
  try {
    if (typeof sessionStorage === 'undefined' || !sessionStorage) return null;
    return sessionStorage;
  } catch {
    return null;
  }
}

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
    // Storage unavailable: at worst a reload re-sends, and Meta deduplicates on
    // eventID. Losing the marker is recoverable; failing the page is not.
  }
}

/**
 * `Purchase` — the only revenue-grade event. Two layers of protection against
 * counting one order twice:
 *
 *   1. a `sessionStorage` marker per transaction id, because the payment-return
 *      page polls, can re-render, and is reachable from browser history;
 *   2. `eventID` on the event itself, so Meta drops any repeat that survives
 *      layer 1 — a different tab, or storage being unavailable.
 *
 * `transactionId` defaults to `transactionIdFor` — the same rule the GA4 path
 * and the server use — but callers may override it. `/payments/return` does:
 * its status response carries `order_id` but no `order_number`, so the default
 * would compute `ORD{id}` while the server's own event uses the order number,
 * and the two ids would never dedupe against each other. It passes the merchant
 * transaction id instead, which both sides know verbatim. Whichever id is used,
 * a future Conversions API must send the identical string for Meta to merge the
 * server event with this one rather than count two sales.
 */
export function trackPurchase(order, { transactionId: overrideId } = {}) {
  if (!order || (order.id === undefined && !order.order_number && !overrideId)) {
    return { sent: false, reason: 'no-order' };
  }
  const transactionId = overrideId || transactionIdFor(order.order_number, order.id);
  if (alreadySent(transactionId)) return { sent: false, reason: 'already-sent' };

  const items = Array.isArray(order.items) ? order.items : [];
  const contents = items
    .map((line) => ({
      id: contentId(line.product_id ?? line.id),
      quantity: num(line.quantity) ?? 1,
    }))
    .filter((entry) => entry.id);

  const result = metaTrack(
    MetaEv.PURCHASE,
    {
      content_type: 'product',
      content_ids: contents.map((entry) => entry.id),
      contents,
      num_items: contents.reduce((sum, entry) => sum + entry.quantity, 0),
      // A plain number. Meta cannot compute ROAS from "₹1,499".
      value: num(order.total_amount ?? order.total) ?? 0,
      currency: order.currency || 'INR',
    },
    { eventID: transactionId },
  );

  // Marked only on a real send, so a suppressed event (pixel not loaded) does
  // not burn the one chance a later, consented page load would have had.
  if (result.sent) markSent(transactionId);
  return result;
}

/** `CompleteRegistration` — a new account was created. */
export function trackRegistration() {
  return metaTrack(MetaEv.COMPLETE_REGISTRATION, { currency: 'INR', value: 0 });
}

/**
 * `Lead` — someone raised a hand (contact form, newsletter).
 *
 * `kind` is a fixed vocabulary chosen by us, never customer text: it becomes an
 * event parameter, and free text there is how a search box ends up in an ad
 * platform.
 */
export function trackLead(kind = 'contact') {
  return metaTrack(MetaEv.LEAD, { content_category: String(kind) });
}

/**
 * There is deliberately no `unloadMetaPixel`. Once `fbevents.js` has run it has
 * already set its cookie and sent its PageView; withdrawing consent has to be a
 * reload, not a `<script>` removal that leaves the script resident.
 */
export function resetMetaPixelForTests() {
  injected = false;
}
