/**
 * GA4 purchase-delivery deliverability — the client half of the guard.
 *
 * The defect both halves refuse: `analytics.ga4_purchase_delivery` in a server
 * mode while no Measurement Protocol API secret is saved. In that state every
 * purchase event queues in the server outbox and can never be delivered — GA4
 * reports zero ecommerce revenue while the queue grows silently. The backend
 * rejects such a save with a 422 (code `ga4_server_delivery_without_secret`,
 * same string as the tracking-health warning); this module is what lets the
 * settings form say so on the delivery selector BEFORE the operator hits save,
 * and banner the stored state when a deployment is already broken.
 *
 * Everything here is pure and reasons only about *whether* a secret exists —
 * the API reports `has_value` per field and the mask `***` for a stored
 * secret; the secret's value never reaches the browser and is never needed.
 *
 * The mode values are the backend's canonical select options (`server`,
 * `both`, `browser`), not the storefront tag's aliases in `purchase.js` —
 * this module reasons about what the admin form is about to store.
 */

/** The two settings the guard reasons about. */
export const DELIVERY_KEY = 'analytics.ga4_purchase_delivery';
export const API_SECRET_KEY = 'analytics.ga4_api_secret';

/** Mirrors `REDACTED` in `services/analytics/integrations.py`. */
export const REDACTED = '***';

/** The backend default when the delivery value is empty/unset. */
export const DEFAULT_DELIVERY_MODE = 'server';

/**
 * Modes under which the SERVER sends `purchase` and therefore cannot work
 * without the Measurement Protocol API secret. Mirrors
 * `SERVER_DELIVERY_MODES` in `services/analytics/integrations.py`: the outbox
 * is the only sender in `server` mode and one of the two senders in `both`.
 */
export const SERVER_DELIVERY_MODES = Object.freeze(['server', 'both']);

/** The two ways out, worded once — the banner and the docs say the same thing. */
export const DELIVERY_GUARD_FIXES = Object.freeze([
  'Paste the Measurement Protocol API secret (GA4 Admin → Data Streams → choose the stream → Measurement Protocol API secrets) into the field below and save — it can be saved together with the delivery mode.',
  'Or switch “Purchase events are sent from” to browser-only, once the browser tag is verified to fire purchase.',
]);

/** Error code the backend uses for this rejection — same string as the
 *  tracking-health warning, so one grep finds both surfaces. */
export const DELIVERY_GUARD_ERROR_CODE = 'ga4_server_delivery_without_secret';

/**
 * Does this delivery mode need the API secret? An empty/unset value falls
 * back to the backend default (`server`) — "unset" is not a loophole, it is
 * an implicit choice of server delivery.
 */
export function requiresApiSecret(mode) {
  const effective = String(mode ?? '').trim() || DEFAULT_DELIVERY_MODE;
  return SERVER_DELIVERY_MODES.includes(effective);
}

/**
 * Will a usable secret exist AFTER the pending save?
 *
 * `secretDraft` is the form's current value for the secret field:
 * - the mask (or an untouched/undefined draft) means "keep what is stored";
 * - a non-empty value is a new secret arriving with this save;
 * - an empty string is an explicit clear.
 */
export function secretAfterSave({ hasStoredSecret, secretDraft }) {
  if (secretDraft === undefined || secretDraft === null || secretDraft === REDACTED) {
    return Boolean(hasStoredSecret);
  }
  return String(secretDraft).trim() !== '';
}

/**
 * Inline error for the delivery selector, or null when the pending selection
 * is deliverable. Pure so the form can show the refusal before the backend
 * has to issue it.
 */
export function deliverySelectionError({ deliveryDraft, hasStoredSecret, secretDraft }) {
  if (!requiresApiSecret(deliveryDraft)) return null;
  if (secretAfterSave({ hasStoredSecret, secretDraft })) return null;
  const clearing =
    Boolean(hasStoredSecret) &&
    secretDraft !== undefined &&
    secretDraft !== null &&
    secretDraft !== REDACTED &&
    String(secretDraft).trim() === '';
  if (clearing) {
    return (
      'This save clears the Measurement Protocol API secret that server-side ' +
      'delivery depends on. Keep the secret, or switch to browser-only first.'
    );
  }
  return (
    'Server-side purchase delivery needs the Measurement Protocol API secret, ' +
    'and none is saved. Paste it below (it can be saved together with this ' +
    'setting), or choose browser-only.'
  );
}

/**
 * Is the CURRENTLY STORED state the broken one? Drives the page banner.
 *
 * Gated on GA4 being enabled: with the master switch off nothing is sent
 * from anywhere, and a banner would be noise on a store that does not use
 * GA4 at all. The tracking-health warning (same code) stays ungated as the
 * exhaustive view.
 */
export function storedStateUndeliverable({ enabled, delivery, hasStoredSecret }) {
  const on = enabled === true || enabled === 'true' || enabled === '1' || enabled === 'on';
  if (!on) return false;
  return requiresApiSecret(delivery) && !hasStoredSecret;
}
