/**
 * The tracking layer's front door: one hook to wire into the app, one function
 * for callers outside React, and the GA id capture that lets the server-side
 * events join the browser's session.
 *
 * Mount `useTracking()` once, inside the router. Every route change re-runs the
 * gates rather than assuming the decision made on the first page still holds —
 * a customer who navigates from a product page into `/admin` must stop being
 * recorded at that moment, not on the next full page load.
 *
 * Nothing here is imported for its side effects: importing this module loads no
 * tag, sets no cookie and contacts no third party. That only happens when a
 * caller invokes `useTracking`/`initTracking`, and only then if the gates pass.
 */
import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';

import { usePublicSettings } from '@/features/settings/public.js';

import { Ev, pushEvent } from './dataLayer.js';
import { getConsent, hasAnalyticsConsent, onConsentChange } from './consent.js';
import { loadGtm, pushConsentDefaults, pushConsentUpdate } from './gtm.js';
import { applyMaskingHints, loadClarity, setClarityTag } from './clarity.js';
import {
  SETTING_KEYS,
  isInternalRoute,
  pageTypeFor,
  readString,
  resolvePath,
} from './config.js';

export * from './dataLayer.js';
export * from './consent.js';
export * from './gtm.js';
export * from './clarity.js';
export * from './purchase.js';
export {
  SETTING_KEYS,
  isAdminRoute,
  isInternalRoute,
  isSensitiveFormRoute,
  pageTypeFor,
} from './config.js';

/** Coarse enough to be a dimension rather than a fingerprint. */
export function deviceCategory() {
  if (typeof window === 'undefined') return 'unknown';
  const width = Number(window.innerWidth) || 0;
  if (!width) return 'unknown';
  if (width < 768) return 'mobile';
  if (width < 1024) return 'tablet';
  return 'desktop';
}

/**
 * `page_view` for the current route.
 *
 * Sends the **path only**, never `page_location`. A full URL carries the query
 * string, and query strings on this site carry password-reset tokens, OAuth
 * codes and whatever a customer pasted into a search box. The path alone
 * answers every question a page-view report is actually asked.
 *
 * (GTM's own configuration tag collects `page_location` independently, reading
 * `document.location` inside the container. Redacting the query string there is
 * a container-side setting, and nothing in this module can enforce it.)
 */
export function trackPageView({ pathname, params } = {}) {
  const path = resolvePath(pathname);
  if (path === null || isInternalRoute(path)) return null;
  return pushEvent(Ev.PAGE_VIEW, {
    page_path: path,
    page_type: pageTypeFor(path),
    ...params,
  });
}

/**
 * Run every gate for one route. Idempotent — the loaders no-op once loaded, the
 * masking pass is additive, and the page view is the only thing that fires
 * per call.
 */
export function initTracking({ settings, pathname, consent = getConsent() } = {}) {
  const path = resolvePath(pathname);

  pushConsentDefaults(consent);

  const gtm = loadGtm({ settings, pathname: path, consent });
  const clarity = loadClarity({ settings, pathname: path, consent });

  // Re-run on every route: the fields on the page a moment ago are not the
  // fields on it now, and an unmasked checkout input is unmasked in the replay.
  applyMaskingHints(path);

  if (clarity.loaded) {
    setClarityTag('page_type', pageTypeFor(path));
    setClarityTag('device_category', deviceCategory());
  }

  return { gtm, clarity, page: trackPageView({ pathname: path }) };
}

/**
 * Mount once, inside the router. Re-evaluates on route change and whenever the
 * customer changes their consent — granting it mid-session loads the tags
 * immediately, without a reload.
 */
export function useTracking() {
  const location = useLocation();
  const { data: settings } = usePublicSettings();
  const [consent, setConsentState] = useState(getConsent);

  useEffect(() => onConsentChange(setConsentState), []);

  useEffect(() => {
    // Defaults first and once; the update only after an actual decision, so a
    // customer who has not chosen is never reported as having chosen "denied".
    pushConsentDefaults(consent);
    if (consent.decided) pushConsentUpdate(consent);
  }, [consent]);

  useEffect(() => {
    initTracking({ settings, pathname: location.pathname, consent });
  }, [location.pathname, settings, consent]);

  return { consent, hasAnalytics: consent.categories.analytics === true };
}

// ---------------------------------------------------------------------------
// GA id capture
// ---------------------------------------------------------------------------

/** Parse a `document.cookie` string. Pure. */
export function readCookieJar(raw) {
  const jar = {};
  if (typeof raw !== 'string' || raw === '') return jar;
  for (const chunk of raw.split(';')) {
    const eq = chunk.indexOf('=');
    if (eq === -1) continue;
    const key = chunk.slice(0, eq).trim();
    if (!key) continue;
    try {
      jar[key] = decodeURIComponent(chunk.slice(eq + 1).trim());
    } catch {
      jar[key] = chunk.slice(eq + 1).trim();
    }
  }
  return jar;
}

/** `GA1.1.1234567890.1700000000` → `1234567890.1700000000`. */
export function parseGaClientId(value) {
  if (typeof value !== 'string') return null;
  const parts = value.split('.');
  if (parts.length < 4) return null;
  const clientId = parts.slice(-2).join('.');
  return /^\d+\.\d+$/.test(clientId) ? clientId : null;
}

/**
 * The per-stream session cookie, in both shapes Google ships:
 * `GS1.1.<session_id>.<session_number>...` and
 * `GS2.1.s<session_id>$o<session_number>$...`.
 */
export function parseGaSession(value) {
  if (typeof value !== 'string') return null;
  const parts = value.split('.');
  if (parts.length < 3) return null;

  // GS2 packs everything after the prefix into one dollar-delimited field, so
  // it is detected before the dot-count check that GS1 needs.
  const rest = parts.slice(2).join('.');
  let sessionId;
  let sessionNumber;

  if (rest.startsWith('s')) {
    const fields = rest.split('$');
    sessionId = fields[0].slice(1);
    const numberField = fields.find((field) => field.startsWith('o'));
    sessionNumber = numberField ? numberField.slice(1) : null;
  } else {
    if (parts.length < 4) return null;
    sessionId = parts[2];
    sessionNumber = parts[3];
  }

  if (!digitsOnly(sessionId)) return null;
  return {
    session_id: sessionId,
    session_number: digitsOnly(sessionNumber) ? Number(sessionNumber) : null,
  };
}

function digitsOnly(value) {
  return typeof value === 'string' && /^\d+$/.test(value);
}

/**
 * The GA4 client and session ids, for handing to checkout so the server's
 * `purchase` lands on the same session as the browser's journey. Without them
 * the server event arrives with a fresh client id and GA4 books the sale as
 * direct traffic, detaching every paid campaign from the revenue it produced.
 *
 * **Reads no cookie without analytics consent.** These identifiers are the
 * analytics cookie — reading and forwarding them is exactly the processing the
 * customer declined. Returns `null`, not an empty object, so a caller cannot
 * spread an absent result into a request body and send `{}` that looks like a
 * capture attempt that merely came back empty.
 */
export function captureGaIds({ settings } = {}) {
  if (!hasAnalyticsConsent()) return null;
  if (typeof document === 'undefined' || typeof document.cookie !== 'string') return null;

  const jar = readCookieJar(document.cookie);
  const clientId = parseGaClientId(jar._ga);

  // Prefer the configured stream's cookie; fall back to whichever `_ga_*` is
  // present, because a container can be swapped in settings without the old
  // cookie expiring, and the wrong session id is worse than a missing one.
  const measurementId = readString(settings, SETTING_KEYS.ga4MeasurementId);
  const streamKey = measurementId ? `_ga_${measurementId.replace(/^G-/i, '')}` : null;
  const fallbackKey = Object.keys(jar).find((key) => key.startsWith('_ga_'));
  const streamCookie = (streamKey && jar[streamKey]) || jar[fallbackKey] || null;
  const session = parseGaSession(streamCookie);

  if (!clientId && !session) return null;
  return {
    client_id: clientId,
    session_id: session ? session.session_id : null,
    session_number: session ? session.session_number : null,
  };
}
