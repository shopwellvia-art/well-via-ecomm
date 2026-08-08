/**
 * Microsoft Clarity — session replay and heatmaps.
 *
 * Clarity is a different risk class from GA4. GA4 receives the parameters we
 * choose; Clarity records *the screen*, which means it receives whatever the
 * page happens to be displaying, including things nobody decided to send. The
 * rules below all follow from that one difference.
 *
 * **Gates.** Same three as GTM — project id configured, analytics consent
 * granted, route allowed — except the route gate is wider: admin, staff and
 * internal routes are excluded outright. A replay of the admin order screen is
 * a recording of a colleague reading someone else's address out loud.
 *
 * **Masking.** On checkout, account, auth and order routes every input is
 * masked; everywhere else the fields whose name, type or autocomplete hint
 * marks them sensitive are masked individually. Clarity's own masking defaults
 * are not relied upon, because they are a dashboard setting — someone can turn
 * them off without touching this repository, and nothing here would notice.
 *
 * **Tags.** Exactly five keys are permitted, all of them low-cardinality
 * dimensions. Names, emails, phone numbers, addresses, payment details, OTPs
 * and raw customer identifiers are never sent — not as a tag, not as an
 * identifier, not as an "upgrade reason". A pseudonymous id can be attached
 * only when an admin has explicitly enabled it, and only if it is opaque.
 */
import { looksLikePii } from './dataLayer.js';
import { hasAnalyticsConsent } from './consent.js';
import {
  SETTING_KEYS,
  isInternalRoute,
  isSensitiveFormRoute,
  readFlag,
  readString,
  resolvePath,
} from './config.js';

export const CLARITY_SCRIPT_ID = 'wv-clarity';
export const CLARITY_ORIGIN = 'https://www.clarity.ms';

/** Clarity project ids are short lowercase alphanumerics. */
export const CLARITY_PROJECT_RE = /^[a-z0-9]{6,20}$/i;

/**
 * The only custom tags this integration may set. Every one is a bounded
 * dimension: there is no key here into which a free-text customer value could
 * be written without the caller having to lie about what it is.
 */
export const CLARITY_ALLOWED_TAGS = Object.freeze([
  'page_type',
  'device_category',
  'customer_type',
  'checkout_step',
  'experiment_id',
]);

/**
 * Fields masked on every route. Matched on type, name and autocomplete because
 * the three disagree often enough that relying on any one of them leaves gaps.
 */
export const CLARITY_MASK_SELECTORS = Object.freeze([
  'input[type="password"]',
  'input[type="email"]',
  'input[type="tel"]',
  '[name*="email" i]',
  '[name*="phone" i]',
  '[name*="mobile" i]',
  '[name*="name" i]',
  '[name*="address" i]',
  '[name*="street" i]',
  '[name*="pincode" i]',
  '[name*="postcode" i]',
  '[name*="zip" i]',
  '[name*="otp" i]',
  '[name*="card" i]',
  '[name*="cvv" i]',
  '[name*="upi" i]',
  '[name*="password" i]',
  '[autocomplete*="cc-" i]',
  '[autocomplete*="tel" i]',
  '[autocomplete*="email" i]',
  '[autocomplete*="name" i]',
  '[autocomplete*="postal" i]',
  '[autocomplete*="street" i]',
  '[data-sensitive]',
]);

/** Everything that holds typed input, masked wholesale on sensitive routes. */
const ALL_INPUT_SELECTOR = 'input, textarea, select, [contenteditable="true"]';

/** An opaque id: no `@`, no bare phone-shaped digits, no readable identity. */
export const PSEUDONYMOUS_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

let injected = false;
let observer = null;
let maskScheduled = false;
let maskPath;

/** True once the Clarity tag has been appended in this page's lifetime. */
export function isClarityLoaded() {
  return injected;
}

/** Should Clarity load? Pure, one reason per gate. */
export function shouldLoadClarity({ projectId, analyticsConsent, pathname } = {}) {
  if (!projectId) return { ok: false, reason: 'no-project-id' };
  if (!CLARITY_PROJECT_RE.test(projectId)) {
    return { ok: false, reason: 'invalid-project-id' };
  }
  if (!analyticsConsent) return { ok: false, reason: 'no-consent' };
  if (isInternalRoute(pathname)) return { ok: false, reason: 'internal-route' };
  return { ok: true, reason: 'ok' };
}

function doc() {
  if (typeof document === 'undefined') return null;
  return document;
}

function clarityFn() {
  if (typeof window === 'undefined') return null;
  return typeof window.clarity === 'function' ? window.clarity : null;
}

/**
 * Load Clarity if the gates pass. Idempotent; safe to call on every route
 * change. Returns `{ loaded, reason }`.
 */
export function loadClarity({ settings, pathname, consent } = {}) {
  const projectId = readString(settings, SETTING_KEYS.clarityProjectId);
  const path = resolvePath(pathname);
  const analyticsConsent =
    consent === undefined ? hasAnalyticsConsent() : consent?.categories?.analytics === true;
  const gate = shouldLoadClarity({ projectId, analyticsConsent, pathname: path });
  if (!gate.ok) return { loaded: false, reason: gate.reason };

  if (injected) return { loaded: true, reason: 'already-loaded' };

  const document_ = doc();
  if (!document_ || typeof document_.createElement !== 'function') {
    return { loaded: false, reason: 'no-document' };
  }
  if (
    typeof document_.getElementById === 'function' &&
    document_.getElementById(CLARITY_SCRIPT_ID)
  ) {
    injected = true;
    return { loaded: true, reason: 'already-loaded' };
  }

  // Mask before the recorder exists, not after: a field first masked on the
  // second tick has already been recorded once.
  applyMaskingHints(path);
  startMaskingObserver();

  // The queue shim the official inline snippet installs, written here instead
  // so no inline script is needed — see the note in `gtm.js` about the CSP.
  if (typeof window !== 'undefined' && typeof window.clarity !== 'function') {
    // `arguments`, not rest args: this is the queue the real tag drains with
    // `.apply`, and matching the official snippet exactly costs nothing.
    const queued = function clarity() {
      (queued.q = queued.q || []).push(arguments);
    };
    window.clarity = queued;
  }

  const script = document_.createElement('script');
  script.async = true;
  script.id = CLARITY_SCRIPT_ID;
  script.src = `${CLARITY_ORIGIN}/tag/${encodeURIComponent(projectId)}`;
  const parent = document_.head || document_.body;
  if (!parent || typeof parent.appendChild !== 'function') {
    return { loaded: false, reason: 'no-document' };
  }
  parent.appendChild(script);
  injected = true;

  // Clarity's own cookie-consent signal. We only reach this line with consent
  // granted, so it is always the affirmative form.
  const clarity = clarityFn();
  if (clarity) clarity('consent');

  return { loaded: true, reason: 'ok' };
}

/**
 * Mark sensitive fields so the recorder never captures their contents.
 *
 * Runs against the live DOM because the components that render those fields
 * are owned elsewhere; adding the attribute here keeps the masking rule in the
 * same file as the recorder it protects, where it can be reviewed as one thing.
 * No-op without a document.
 */
export function applyMaskingHints(pathname) {
  const document_ = doc();
  if (!document_ || typeof document_.querySelectorAll !== 'function') return 0;
  maskPath = pathname;

  const selectors = isSensitiveFormRoute(pathname)
    ? [ALL_INPUT_SELECTOR, ...CLARITY_MASK_SELECTORS]
    : CLARITY_MASK_SELECTORS;

  let masked = 0;
  for (const selector of selectors) {
    let nodes;
    try {
      nodes = document_.querySelectorAll(selector);
    } catch {
      continue; // an engine that rejects the `i` attribute flag — skip, not throw
    }
    for (const node of nodes) {
      if (node && typeof node.setAttribute === 'function') {
        node.setAttribute('data-clarity-mask', 'true');
        masked += 1;
      }
    }
  }
  return masked;
}

/**
 * Re-mask as the page changes under the recorder.
 *
 * A single pass at route-change time is not enough on this app: the checkout
 * page lazy-loads, then renders its payment fields only after the cart and the
 * active gateways resolve. Those inputs appear several hundred milliseconds
 * after the route effect ran, and a field that is unmasked for the first frame
 * is unmasked in the replay of that frame.
 *
 * Only `childList` is observed, so setting the mask attribute cannot retrigger
 * the observer, and the pass is coalesced to one per task — React commits
 * mutations in bursts and re-querying on each one would be felt on a long page.
 */
export function startMaskingObserver() {
  if (observer) return false;
  const document_ = doc();
  if (!document_ || !document_.body) return false;
  if (typeof MutationObserver === 'undefined') return false;

  observer = new MutationObserver(() => {
    if (maskScheduled) return;
    maskScheduled = true;
    setTimeout(() => {
      maskScheduled = false;
      applyMaskingHints(maskPath);
    }, 0);
  });
  observer.observe(document_.body, { childList: true, subtree: true });
  return true;
}

/** Stop re-masking. Called on reset; there is no other reason to stop. */
export function stopMaskingObserver() {
  if (!observer) return false;
  observer.disconnect();
  observer = null;
  return true;
}

/** Raised when a caller tries to put something into Clarity that must not go. */
export class ClarityTagRejected extends Error {
  constructor(message) {
    super(message);
    this.name = 'ClarityTagRejected';
  }
}

/**
 * Set one of the five permitted tags.
 *
 * Both halves are checked. The key must be on the allowlist — a tag named
 * anything else is a caller inventing a new dimension without review. The
 * value must be a short scalar that does not look like an email or a phone
 * number: tag values are the one Clarity surface a developer can write freely
 * into, so the value guard lives here rather than being assumed.
 *
 * Throws rather than skipping: unlike a search box, nothing a customer types
 * reaches this function, so a rejected tag is always a developer's mistake.
 */
export function setClarityTag(key, value) {
  if (!CLARITY_ALLOWED_TAGS.includes(key)) {
    throw new ClarityTagRejected(
      `"${key}" is not a permitted Clarity tag. The allowed tags are ` +
        `${CLARITY_ALLOWED_TAGS.join(', ')} — anything else risks sending a ` +
        'customer value into a session-replay tool.'
    );
  }
  if (value === undefined || value === null || value === '') return false;
  if (typeof value !== 'string' && typeof value !== 'number') {
    throw new ClarityTagRejected(
      `Clarity tag "${key}" must be a string or number; objects and arrays can ` +
        'carry fields nobody reviewed.'
    );
  }
  const text = String(value);
  if (text.length > 100) {
    throw new ClarityTagRejected(
      `Clarity tag "${key}" is ${text.length} characters — tags are dimensions, ` +
        'not free text. Anything this long is carrying data it should not.'
    );
  }
  if (looksLikePii(text)) {
    throw new ClarityTagRejected(
      `Clarity tag "${key}" has a value shaped like an email or phone number. ` +
        'Clarity must never receive a customer identifier.'
    );
  }

  if (!hasAnalyticsConsent()) return false;
  const clarity = clarityFn();
  if (!clarity) return false;
  clarity('set', key, text);
  return true;
}

/**
 * Attach a pseudonymous id to the session — opt-in, and only ever opaque.
 *
 * Disabled unless an admin has switched it on, because linking replays to a
 * stable id changes what the recording *is*: an anonymous UX sample becomes a
 * per-person behavioural record. Even then the id must be opaque; the raw
 * customer id, email or phone number is refused, and Clarity's optional
 * friendly-name and page-title arguments are never passed at all.
 */
export function identifyClarity(pseudonymousId, { settings } = {}) {
  if (!readFlag(settings, SETTING_KEYS.clarityPseudonymousId)) return false;
  if (!hasAnalyticsConsent()) return false;
  if (typeof pseudonymousId !== 'string' || !PSEUDONYMOUS_ID_RE.test(pseudonymousId)) {
    throw new ClarityTagRejected(
      'the Clarity identifier must be an opaque token (8-64 of [A-Za-z0-9_-]). ' +
        'A raw customer id, email or phone number is not pseudonymous.'
    );
  }
  if (looksLikePii(pseudonymousId)) {
    throw new ClarityTagRejected('the Clarity identifier looks like a customer contact detail.');
  }
  const clarity = clarityFn();
  if (!clarity) return false;
  clarity('identify', pseudonymousId);
  return true;
}

/** Test-only. Clears the injected flag; the DOM is the test's to reset. */
export function resetClarityForTests() {
  stopMaskingObserver();
  injected = false;
  maskScheduled = false;
  maskPath = undefined;
}
