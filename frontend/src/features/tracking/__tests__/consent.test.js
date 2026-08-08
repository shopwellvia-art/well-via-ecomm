/**
 * Consent: what the defaults are, what cannot be changed, and what is written
 * down when a customer decides.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  CONSENT_POLICY_VERSION,
  CONSENT_STORAGE_KEY,
  consentModeSignals,
  getConsent,
  hasAnalyticsConsent,
  hasConsent,
  mergeConsent,
  normalizeConsent,
  onConsentChange,
  resetConsent,
  resetConsentCacheForTests,
  setConsent,
} from '@/features/tracking/consent.js';

function stored() {
  const raw = globalThis.localStorage.getItem(CONSENT_STORAGE_KEY);
  return raw ? JSON.parse(raw) : null;
}

beforeEach(() => {
  resetConsent();
  resetConsentCacheForTests();
});

describe('defaults', () => {
  it('denies analytics, marketing and personalization until the customer chooses', () => {
    const consent = getConsent();
    expect(consent.categories.analytics).toBe(false);
    expect(consent.categories.marketing).toBe(false);
    expect(consent.categories.personalization).toBe(false);
    expect(consent.decided).toBe(false);
    expect(hasAnalyticsConsent()).toBe(false);
  });

  it('grants necessary, which is what makes the site usable at all', () => {
    expect(getConsent().categories.necessary).toBe(true);
    expect(hasConsent('necessary')).toBe(true);
  });

  it('reports denied Consent Mode signals before a decision', () => {
    const signals = consentModeSignals(getConsent());
    expect(signals.analytics_storage).toBe('denied');
    expect(signals.ad_storage).toBe('denied');
    expect(signals.ad_user_data).toBe('denied');
    expect(signals.ad_personalization).toBe('denied');
    expect(signals.security_storage).toBe('granted');
  });
});

describe('necessary cannot be revoked', () => {
  it('ignores an explicit attempt to deny it', () => {
    setConsent({ necessary: false, analytics: true });
    expect(getConsent().categories.necessary).toBe(true);
    expect(hasConsent('necessary')).toBe(true);
  });

  it('stays granted through a full deny-everything decision', () => {
    setConsent({
      necessary: false,
      analytics: false,
      marketing: false,
      personalization: false,
    });
    expect(getConsent().categories.necessary).toBe(true);
  });

  it('stays granted when a tampered record claims otherwise', () => {
    globalThis.localStorage.setItem(
      CONSENT_STORAGE_KEY,
      JSON.stringify({
        policyVersion: CONSENT_POLICY_VERSION,
        updatedAt: new Date().toISOString(),
        decided: true,
        categories: { necessary: false, analytics: true },
      })
    );
    resetConsentCacheForTests();
    expect(getConsent().categories.necessary).toBe(true);
  });

  it('rejects a category that is not one of the four', () => {
    expect(() => setConsent({ advertising: true })).toThrow(/unknown consent category/);
  });
});

describe('persistence', () => {
  it('records the policy version and a timestamp with the decision', () => {
    const before = Date.now();
    setConsent({ analytics: true });
    const record = stored();

    expect(record.policyVersion).toBe(CONSENT_POLICY_VERSION);
    expect(typeof record.updatedAt).toBe('string');
    expect(Date.parse(record.updatedAt)).toBeGreaterThanOrEqual(before - 1000);
    expect(record.decided).toBe(true);
    expect(record.categories.analytics).toBe(true);
  });

  it('survives a reload — a decision is not re-asked for on the next page', () => {
    setConsent({ analytics: true, marketing: false });
    resetConsentCacheForTests();
    expect(hasAnalyticsConsent()).toBe(true);
    expect(getConsent().categories.marketing).toBe(false);
  });

  it('discards a record written against a superseded policy version', () => {
    globalThis.localStorage.setItem(
      CONSENT_STORAGE_KEY,
      JSON.stringify({
        policyVersion: CONSENT_POLICY_VERSION - 1,
        updatedAt: '2020-01-01T00:00:00.000Z',
        decided: true,
        categories: { necessary: true, analytics: true, marketing: true },
      })
    );
    resetConsentCacheForTests();
    const consent = getConsent();
    expect(consent.decided).toBe(false);
    expect(consent.categories.analytics).toBe(false);
  });

  it('treats corrupt storage as no decision rather than as consent', () => {
    globalThis.localStorage.setItem(CONSENT_STORAGE_KEY, '{not json');
    resetConsentCacheForTests();
    expect(getConsent().categories.analytics).toBe(false);
  });

  it('normalizes an unknown stored category away instead of honouring it', () => {
    const record = normalizeConsent({
      policyVersion: CONSENT_POLICY_VERSION,
      categories: { analytics: 'yes', advertising: true },
    });
    expect(record.categories.analytics).toBe(false); // only `true` is granted
    expect(record.categories.advertising).toBeUndefined();
  });
});

describe('subscriptions', () => {
  it('notifies subscribers, and stops after unsubscribe', () => {
    const seen = [];
    const off = onConsentChange((consent) => seen.push(consent.categories.analytics));

    setConsent({ analytics: true });
    off();
    setConsent({ analytics: false });

    expect(seen).toEqual([true]);
  });
});

describe('mergeConsent', () => {
  it('is pure — the clock is injected and the input is untouched', () => {
    const before = { policyVersion: CONSENT_POLICY_VERSION, categories: { analytics: false } };
    const after = mergeConsent(before, { analytics: true }, new Date('2026-07-28T10:00:00Z'));
    expect(after.updatedAt).toBe('2026-07-28T10:00:00.000Z');
    expect(before.categories.analytics).toBe(false);
  });
});
