/**
 * Google Tag Manager loader.
 *
 * **The bootstrap lives here, in the bundle — not as an inline `<script>` in
 * `index.html`.** That is the whole point of this module. GTM's copy-paste
 * snippet is inline JavaScript, and shipping it would force `'unsafe-inline'`
 * into the production `script-src`, which retires the CSP as a defence against
 * XSS for every other script on the page. A bundled module does exactly what
 * the snippet does — seed `dataLayer`, push `gtm.js`, append the tag — from a
 * file the CSP can allow by origin.
 *
 * Three gates, all of which must pass before a single byte is requested from
 * googletagmanager.com:
 *
 *   1. a container id is configured by an admin,
 *   2. analytics consent is granted by the customer,
 *   3. the route is not under `/admin`.
 *
 * The third exists because the admin shell renders order details, addresses and
 * customer records. Loading a third-party tag manager there means an arbitrary
 * container — editable by anyone with GTM access, without a deploy — can run
 * JavaScript on a page full of other people's personal data.
 */
import { assertNoPii, getDataLayer, pushRaw } from './dataLayer.js';
import { consentModeSignals, getConsent, hasAnalyticsConsent } from './consent.js';
import { SETTING_KEYS, isAdminRoute, readString, resolvePath } from './config.js';

export const GTM_SCRIPT_ID = 'wv-gtm';
export const GTM_ORIGIN = 'https://www.googletagmanager.com';

/**
 * A container id is `GTM-` plus alphanumerics. Validated because it is
 * interpolated into a script `src`: an admin-controlled settings value is not
 * hostile, but it is also not a reason to skip validating the one string in
 * this feature that becomes executable code.
 */
export const GTM_CONTAINER_RE = /^GTM-[A-Z0-9]{4,20}$/i;

let injected = false;
let consentDefaultsPushed = false;

/** True once the tag has been appended in this page's lifetime. */
export function isGtmLoaded() {
  return injected;
}

/**
 * Should GTM load? Pure — takes the three inputs, returns a reason string, so
 * each gate is assertable on its own without a DOM.
 */
export function shouldLoadGtm({ containerId, analyticsConsent, pathname } = {}) {
  if (!containerId) return { ok: false, reason: 'no-container-id' };
  if (!GTM_CONTAINER_RE.test(containerId)) {
    return { ok: false, reason: 'invalid-container-id' };
  }
  if (!analyticsConsent) return { ok: false, reason: 'no-consent' };
  if (isAdminRoute(pathname)) return { ok: false, reason: 'admin-route' };
  return { ok: true, reason: 'ok' };
}

/**
 * Consent Mode v2 signals. `default` is pushed once per page, before any tag
 * loads, even when everything is denied — that is what tells Google to model
 * rather than to assume, and it must precede the container.
 */
export function pushConsentDefaults(consent = getConsent()) {
  if (consentDefaultsPushed) return false;
  consentDefaultsPushed = gtagPush('consent', 'default', consentModeSignals(consent));
  return consentDefaultsPushed;
}

/** `update` signals, pushed whenever the customer changes their mind. */
export function pushConsentUpdate(consent = getConsent()) {
  return gtagPush('consent', 'update', consentModeSignals(consent));
}

/**
 * The `gtag` shim: Consent Mode reads the `arguments` object, so this is a
 * function declaration rather than an arrow with rest args. The payload is
 * still PII-checked — there is no push path in this feature that isn't.
 */
function gtagPush() {
  const dl = getDataLayer();
  if (!dl) return false;
  assertNoPii(Array.from(arguments));
  dl.push(arguments);
  return true;
}

/**
 * Load GTM if all three gates pass. Idempotent: repeated calls (every route
 * change calls this) append nothing after the first success.
 *
 * Returns `{ loaded, reason }` — `loaded: false` with the reason that stopped
 * it, so a caller can log why nothing is being collected.
 */
export function loadGtm({ settings, pathname, consent } = {}) {
  const containerId = readString(settings, SETTING_KEYS.gtmContainerId);
  const path = resolvePath(pathname);
  const analyticsConsent =
    consent === undefined ? hasAnalyticsConsent() : consent?.categories?.analytics === true;
  const gate = shouldLoadGtm({ containerId, analyticsConsent, pathname: path });
  if (!gate.ok) return { loaded: false, reason: gate.reason };

  if (injected) return { loaded: true, reason: 'already-loaded' };

  const doc = typeof document === 'undefined' ? null : document;
  if (!doc || typeof doc.createElement !== 'function') {
    return { loaded: false, reason: 'no-document' };
  }
  if (typeof doc.getElementById === 'function' && doc.getElementById(GTM_SCRIPT_ID)) {
    injected = true; // someone else already appended it — do not double-count
    return { loaded: true, reason: 'already-loaded' };
  }

  pushConsentDefaults(consent);
  pushRaw({ 'gtm.start': Date.now(), event: 'gtm.js' });

  const script = doc.createElement('script');
  script.async = true;
  script.id = GTM_SCRIPT_ID;
  script.src = `${GTM_ORIGIN}/gtm.js?id=${encodeURIComponent(containerId)}`;
  const parent = doc.head || doc.body;
  if (!parent || typeof parent.appendChild !== 'function') {
    return { loaded: false, reason: 'no-document' };
  }
  parent.appendChild(script);
  injected = true;
  return { loaded: true, reason: 'ok' };
}

/**
 * There is deliberately no `unloadGtm`. A tag manager cannot be un-run: once
 * the container has executed, withdrawing consent has to be expressed as a
 * Consent Mode `update` (which `pushConsentUpdate` does) plus a reload, not as
 * a `<script>` removal that leaves every tag it already fired still resident.
 */
export function resetGtmForTests() {
  injected = false;
  consentDefaultsPushed = false;
}
