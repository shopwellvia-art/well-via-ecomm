/**
 * Razorpay Standard Checkout (embedded modal) helpers.
 *
 * Payment Links sent the customer on a full-page redirect; Standard Checkout
 * keeps them on our checkout page and opens Razorpay's modal via checkout.js.
 * The moving parts are split so the pure pieces stay unit-testable:
 *
 *   loadRazorpayScript()   — inject checkout.js exactly once
 *   buildRazorpayOptions() — pure mapping: backend `checkout` object → options
 *   openRazorpayCheckout() — load + construct + open (browser-only glue)
 */

const RAZORPAY_SCRIPT_URL = 'https://checkout.razorpay.com/v1/checkout.js';

// Matches tailwind.config.js `wgreen.DEFAULT` — the modal header should read
// as the same brand green as the Place Order button that opened it.
export const RAZORPAY_THEME_COLOR = '#044D39';

// Module-level so every caller shares one in-flight injection. Reset to null
// on load error so a later Place Order can retry after a network blip.
let scriptPromise = null;

/**
 * Inject checkout.js once and resolve with the `window.Razorpay` constructor.
 *
 * Resolves immediately when a previous load (or another script tag) already
 * defined `window.Razorpay`; concurrent callers share the same pending
 * promise so only one <script> tag is ever appended.
 *
 * @returns {Promise<Function>} the `window.Razorpay` constructor
 */
export function loadRazorpayScript() {
  if (typeof window !== 'undefined' && window.Razorpay) {
    return Promise.resolve(window.Razorpay);
  }
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = RAZORPAY_SCRIPT_URL;
    script.async = true;
    script.onload = () => resolve(window.Razorpay);
    script.onerror = () => {
      // Drop the failed promise AND the dead tag so a retry starts clean
      // instead of rejecting forever off a single network blip.
      scriptPromise = null;
      script.remove();
      reject(new Error('Failed to load the Razorpay checkout script.'));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

/**
 * Pure mapping from the backend's `checkout` object (POST /checkout response)
 * to the options object for `new window.Razorpay(options)`.
 *
 * @param {object} checkout - `response.checkout`: {key_id, order_id, amount,
 *   currency, name, description, prefill, notes}
 * @param {object} [callbacks]
 * @param {Function} [callbacks.onSuccess] - Razorpay `handler`: fires with
 *   {razorpay_order_id, razorpay_payment_id, razorpay_signature} after a
 *   successful payment
 * @param {Function} [callbacks.onDismiss] - `modal.ondismiss`: the customer
 *   closed the modal without completing payment
 * @param {object} [callbacks.prefillOverrides] - name/contact collected by our
 *   checkout form; blank values are dropped so they never clobber the
 *   server-side prefill (e.g. the account email)
 * @returns {object} options for `new window.Razorpay(options)`
 */
export function buildRazorpayOptions(
  checkout,
  { onSuccess, onDismiss, prefillOverrides } = {},
) {
  // Drop empty overrides: a blank phone field must not erase the email the
  // backend prefilled from the customer's account.
  const overrides = Object.fromEntries(
    Object.entries(prefillOverrides || {}).filter(
      ([, v]) => v != null && String(v).trim() !== '',
    ),
  );
  return {
    key: checkout.key_id,
    order_id: checkout.order_id,
    amount: checkout.amount,
    currency: checkout.currency,
    name: checkout.name,
    description: checkout.description,
    notes: checkout.notes || {},
    prefill: { ...(checkout.prefill || {}), ...overrides },
    theme: { color: RAZORPAY_THEME_COLOR },
    handler: onSuccess,
    modal: { ondismiss: onDismiss },
  };
}

/**
 * Load checkout.js, build the modal, and open it.
 *
 * @param {object} checkout - `response.checkout` from POST /checkout
 * @param {object} [callbacks] - see buildRazorpayOptions; additionally:
 * @param {Function} [callbacks.onFailure] - Razorpay `payment.failed` event:
 *   an attempt inside the modal was declined. Razorpay keeps the modal open
 *   for retries, so this is informational — success/dismiss still decide the
 *   final outcome.
 * @returns {Promise<object>} the constructed Razorpay instance
 */
export async function openRazorpayCheckout(checkout, callbacks = {}) {
  const Razorpay = await loadRazorpayScript();
  const rzp = new Razorpay(buildRazorpayOptions(checkout, callbacks));
  if (callbacks.onFailure) rzp.on('payment.failed', callbacks.onFailure);
  rzp.open();
  return rzp;
}
