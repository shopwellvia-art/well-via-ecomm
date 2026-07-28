/**
 * Consent state for the four tracking categories.
 *
 * The shape of the problem: consent is not a boolean and it is not permanent.
 * A customer agrees to a *specific* policy at a *specific* time, and if the
 * policy changes the old agreement no longer covers the new one. So the stored
 * record carries the policy version and the timestamp of the decision, and a
 * record written against a superseded policy is discarded rather than honoured
 * — the customer is asked again.
 *
 * `necessary` is granted and cannot be revoked: it covers the session cookie
 * and the cart, without which there is no site to consent to. Everything else
 * starts **denied** and stays denied until the customer actively chooses. There
 * is no "implied consent by continuing to browse" here.
 *
 * All decision logic is pure and exported (`normalizeConsent`, `mergeConsent`)
 * so it is exercised by the node test suite; only `getConsent`/`setConsent`
 * touch storage, and they tolerate its absence — Safari private mode throws on
 * `localStorage`, and the SSR/node case has none at all.
 */

/** The four categories. `necessary` is structurally different from the rest. */
export const CONSENT_CATEGORIES = Object.freeze([
  'necessary',
  'analytics',
  'marketing',
  'personalization',
]);

/**
 * Bump when the privacy policy changes in a way that invalidates prior
 * agreement. Every stored record naming an older version is discarded and the
 * customer is asked again.
 */
export const CONSENT_POLICY_VERSION = 1;

/** Storage key. Namespaced so it cannot collide with a zustand persist key. */
export const CONSENT_STORAGE_KEY = 'wv.consent';

/** Denied-by-default. Only `necessary` starts granted, and only it can't move. */
export const DEFAULT_CATEGORIES = Object.freeze({
  necessary: true,
  analytics: false,
  marketing: false,
  personalization: false,
});

let cached = null;
const listeners = new Set();

/** A fresh, undecided record: everything optional denied. */
export function defaultConsent() {
  return {
    policyVersion: CONSENT_POLICY_VERSION,
    updatedAt: null,
    decided: false,
    categories: { ...DEFAULT_CATEGORIES },
  };
}

/**
 * Coerce anything that came out of storage into a valid record.
 *
 * Pure. Returns a fresh default for junk, for a record written against another
 * policy version, and for a record missing categories — all three mean "we do
 * not have a usable agreement", which is the same thing as no agreement.
 */
export function normalizeConsent(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return defaultConsent();
  if (raw.policyVersion !== CONSENT_POLICY_VERSION) return defaultConsent();

  const stored = raw.categories && typeof raw.categories === 'object' ? raw.categories : {};
  const categories = { ...DEFAULT_CATEGORIES };
  for (const category of CONSENT_CATEGORIES) {
    if (category === 'necessary') continue; // never read back — always granted
    categories[category] = stored[category] === true;
  }

  return {
    policyVersion: CONSENT_POLICY_VERSION,
    updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : null,
    decided: raw.decided === true,
    categories,
  };
}

/**
 * Apply a patch to a record. Pure; the clock is injected so tests are stable.
 *
 * `necessary: false` is not an error and is not honoured — a UI may well send
 * the whole category map back, and rejecting it loudly would just push every
 * caller into filtering the key itself.
 */
export function mergeConsent(current, patch = {}, now = new Date()) {
  const base = normalizeConsent(current);
  const categories = { ...base.categories };

  for (const [key, value] of Object.entries(patch)) {
    if (!CONSENT_CATEGORIES.includes(key)) {
      throw new Error(
        `unknown consent category "${key}" — the four categories are ` +
          `${CONSENT_CATEGORIES.join(', ')}`
      );
    }
    if (key === 'necessary') continue; // always granted, cannot be revoked
    categories[key] = value === true;
  }
  categories.necessary = true;

  return {
    policyVersion: CONSENT_POLICY_VERSION,
    updatedAt: now.toISOString(),
    decided: true,
    categories,
  };
}

function storage() {
  try {
    if (typeof localStorage === 'undefined' || !localStorage) return null;
    return localStorage;
  } catch {
    return null; // private mode / disabled storage
  }
}

function readStored() {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(CONSENT_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeStored(record) {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(CONSENT_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Quota or private mode. The in-memory record still governs this page
    // view; the customer will simply be asked again next visit.
  }
}

/** The current record. Reads storage once, then serves from memory. */
export function getConsent() {
  if (!cached) cached = normalizeConsent(readStored());
  return cached;
}

/** Record a decision. Returns the new record and notifies subscribers. */
export function setConsent(patch) {
  cached = mergeConsent(getConsent(), patch);
  writeStored(cached);
  emit();
  return cached;
}

/**
 * Forget the decision entirely — the "withdraw consent" path, and the reset
 * a policy-version bump performs implicitly.
 */
export function resetConsent() {
  const store = storage();
  if (store) {
    try {
      store.removeItem(CONSENT_STORAGE_KEY);
    } catch {
      // nothing to do — the in-memory reset below is what matters this session
    }
  }
  cached = defaultConsent();
  emit();
  return cached;
}

function emit() {
  for (const listener of listeners) {
    try {
      listener(cached);
    } catch {
      // one bad subscriber must not stop the others being told
    }
  }
}

/** Subscribe to consent changes. Returns the unsubscribe function. */
export function onConsentChange(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

/** True when the named category is granted. */
export function hasConsent(category) {
  if (category === 'necessary') return true;
  return getConsent().categories[category] === true;
}

/** The gate every tag in this feature checks before doing anything. */
export function hasAnalyticsConsent() {
  return hasConsent('analytics');
}

/** True once the customer has actively chosen — drives the banner's visibility. */
export function hasDecided() {
  return getConsent().decided === true;
}

/**
 * Google Consent Mode v2 signals derived from our categories.
 *
 * `security_storage` is always granted: it covers fraud prevention and is
 * `necessary` by another name.
 */
export function consentModeSignals(consent = getConsent()) {
  const { categories } = normalizeConsent(consent);
  const analytics = categories.analytics ? 'granted' : 'denied';
  const marketing = categories.marketing ? 'granted' : 'denied';
  const personalization = categories.personalization ? 'granted' : 'denied';
  return {
    ad_storage: marketing,
    ad_user_data: marketing,
    ad_personalization: marketing,
    analytics_storage: analytics,
    functionality_storage: 'granted',
    personalization_storage: personalization,
    security_storage: 'granted',
  };
}

/** Drops the in-memory cache. Test-only; storage is untouched. */
export function resetConsentCacheForTests() {
  cached = null;
  listeners.clear();
}
