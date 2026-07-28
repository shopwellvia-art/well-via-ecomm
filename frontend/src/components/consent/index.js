/**
 * Consent UI — the customer-facing half of the tracking feature.
 *
 * The rules (`visibility.js`) are pure and tested; the components only render
 * them. Storage, the policy version and the timestamp belong to
 * `features/tracking/consent.js` and are not duplicated here.
 */
export { default as ConsentGate } from './ConsentGate.jsx';
export { default as ConsentBanner } from './ConsentBanner.jsx';
export { default as CookiePreferencesLink } from './CookiePreferencesLink.jsx';
export { openConsentPreferences, onOpenConsentPreferences } from './preferencesBus.js';
export * from './visibility.js';
