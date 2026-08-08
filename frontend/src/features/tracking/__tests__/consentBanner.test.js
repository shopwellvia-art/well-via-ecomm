/**
 * The banner's rules: when it is asked, what the switches start at, what cannot
 * be switched, and what happens after the customer has answered.
 *
 * These are the claims that would have to be defended to a regulator, so they
 * are tested against the real store — `setConsent` writing to real storage —
 * rather than against a mock that agrees with us by construction. Nothing here
 * renders a component; `visibility.js` exists so that these rules can be
 * checked without one.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  getConsent,
  hasAnalyticsConsent,
  hasConsent,
  resetConsent,
  resetConsentCacheForTests,
  setConsent,
} from '@/features/tracking/consent.js';
import {
  CONSENT_UI_CATEGORIES,
  OPTIONAL_CATEGORY_IDS,
  acceptAllPatch,
  draftFromConsent,
  isCategoryLocked,
  patchFromDraft,
  rejectAllPatch,
  setDraftCategory,
  shouldShowBanner,
} from '@/components/consent/visibility.js';

beforeEach(() => {
  resetConsent();
  resetConsentCacheForTests();
});

describe('the switches a customer is first shown', () => {
  it('starts analytics, marketing and personalization off', () => {
    const draft = draftFromConsent(getConsent());
    expect(draft.analytics).toBe(false);
    expect(draft.marketing).toBe(false);
    expect(draft.personalization).toBe(false);
  });

  it('shows necessary as granted, because the site does not work without it', () => {
    expect(draftFromConsent(getConsent()).necessary).toBe(true);
    expect(hasConsent('necessary')).toBe(true);
  });

  it('offers exactly the four categories, one of them locked', () => {
    expect(CONSENT_UI_CATEGORIES.map((c) => c.id)).toEqual([
      'necessary',
      'analytics',
      'marketing',
      'personalization',
    ]);
    expect(CONSENT_UI_CATEGORIES.filter((c) => c.locked).map((c) => c.id)).toEqual(['necessary']);
    expect(OPTIONAL_CATEGORY_IDS).toEqual(['analytics', 'marketing', 'personalization']);
  });

  it('describes every category in words, not in a category name', () => {
    for (const category of CONSENT_UI_CATEGORIES) {
      expect(category.description.length).toBeGreaterThan(40);
      expect(category.label).not.toBe('');
    }
  });

  it('gives "reject all" and "accept all" the same reach — every optional category', () => {
    expect(Object.keys(rejectAllPatch()).sort()).toEqual(Object.keys(acceptAllPatch()).sort());
    expect(Object.values(rejectAllPatch())).toEqual([false, false, false]);
    expect(Object.values(acceptAllPatch())).toEqual([true, true, true]);
  });
});

describe('necessary cannot be revoked', () => {
  it('is marked locked, so no switch is rendered for it', () => {
    expect(isCategoryLocked('necessary')).toBe(true);
    expect(isCategoryLocked('analytics')).toBe(false);
  });

  it('stays on when the draft is asked to turn it off', () => {
    const draft = setDraftCategory(draftFromConsent(getConsent()), 'necessary', false);
    expect(draft.necessary).toBe(true);
  });

  it('is never carried in the patch, so it cannot be denied downstream either', () => {
    const patch = patchFromDraft({ necessary: false, analytics: true });
    expect(patch).not.toHaveProperty('necessary');
    setConsent(patch);
    expect(getConsent().categories.necessary).toBe(true);
  });

  it('refuses a category that is not one of the three switchable ones', () => {
    expect(() => setDraftCategory(draftFromConsent(getConsent()), 'advertising', true)).toThrow(
      /unknown consent category/,
    );
  });
});

describe('when the banner is shown', () => {
  it('asks a customer who has not decided', () => {
    expect(shouldShowBanner({ pathname: '/', consent: getConsent() })).toBe(true);
    expect(shouldShowBanner({ pathname: '/products/42', consent: getConsent() })).toBe(true);
  });

  it('does not ask again once a choice is recorded — either choice', () => {
    setConsent(rejectAllPatch());
    expect(shouldShowBanner({ pathname: '/', consent: getConsent() })).toBe(false);

    setConsent(acceptAllPatch());
    expect(shouldShowBanner({ pathname: '/', consent: getConsent() })).toBe(false);
  });

  it('does not ask again after a reload, because the decision was written down', () => {
    setConsent(rejectAllPatch());
    resetConsentCacheForTests(); // next page load, fresh module state
    expect(shouldShowBanner({ pathname: '/', consent: getConsent() })).toBe(false);
  });

  it('never appears on /admin — staff are not the data subject', () => {
    const undecided = getConsent();
    for (const path of [
      '/admin',
      '/admin/',
      '/admin/orders',
      '/admin/analytics/settings',
      '/admin/analytics/traffic/overview',
    ]) {
      expect(shouldShowBanner({ pathname: path, consent: undecided })).toBe(false);
    }
    // …and the storefront route that merely starts with the same letters does.
    expect(shouldShowBanner({ pathname: '/administrators', consent: undecided })).toBe(true);
  });

  it('stays hidden on /admin even when the footer link asks for it', () => {
    expect(
      shouldShowBanner({ pathname: '/admin/analytics/settings', consent: getConsent(), reopened: true }),
    ).toBe(false);
  });

  it('comes back when the footer link asks, decision or no decision', () => {
    setConsent(acceptAllPatch());
    expect(shouldShowBanner({ pathname: '/', consent: getConsent(), reopened: true })).toBe(true);
  });

  it('hides for the session on dismissal, but only while nothing was decided', () => {
    expect(
      shouldShowBanner({ pathname: '/', consent: getConsent(), dismissedForSession: true }),
    ).toBe(false);
    // Dismissal records nothing, so the gates stay shut.
    expect(hasAnalyticsConsent()).toBe(false);
    expect(getConsent().decided).toBe(false);
  });

  it('treats an unknown route as somewhere it must not appear', () => {
    expect(shouldShowBanner({ consent: getConsent() })).toBe(false);
    expect(shouldShowBanner({ pathname: null, consent: getConsent() })).toBe(false);
  });
});

describe('withdrawal', () => {
  it('turns analytics back off after it was granted, and keeps it off', () => {
    setConsent(acceptAllPatch());
    expect(hasAnalyticsConsent()).toBe(true);

    setConsent(rejectAllPatch());
    expect(hasAnalyticsConsent()).toBe(false);
    expect(getConsent().categories.marketing).toBe(false);
    expect(getConsent().categories.personalization).toBe(false);

    resetConsentCacheForTests(); // reload
    expect(hasAnalyticsConsent()).toBe(false);
  });

  it('records the withdrawal as a decision, so it is not re-asked as if undecided', () => {
    setConsent(acceptAllPatch());
    setConsent(rejectAllPatch());
    resetConsentCacheForTests();

    const consent = getConsent();
    expect(consent.decided).toBe(true);
    expect(typeof consent.updatedAt).toBe('string');
    expect(shouldShowBanner({ pathname: '/', consent })).toBe(false);
  });

  it('supports withdrawing one category and keeping another', () => {
    setConsent(acceptAllPatch());
    const draft = setDraftCategory(draftFromConsent(getConsent()), 'marketing', false);
    setConsent(patchFromDraft(draft));

    resetConsentCacheForTests();
    const { categories } = getConsent();
    expect(categories.analytics).toBe(true);
    expect(categories.marketing).toBe(false);
    expect(categories.personalization).toBe(true);
  });

  it('keeps the switches in step with what was actually saved', () => {
    setConsent({ analytics: true, marketing: false, personalization: false });
    expect(draftFromConsent(getConsent())).toEqual({
      necessary: true,
      analytics: true,
      marketing: false,
      personalization: false,
    });
  });
});

describe('purity', () => {
  it('does not mutate the draft it is handed', () => {
    const before = draftFromConsent(getConsent());
    const after = setDraftCategory(before, 'analytics', true);
    expect(before.analytics).toBe(false);
    expect(after.analytics).toBe(true);
    expect(after).not.toBe(before);
  });

  it('reads only `true` as a grant — a stray string is not agreement', () => {
    expect(patchFromDraft({ analytics: 'yes', marketing: 1 })).toEqual({
      analytics: false,
      marketing: false,
      personalization: false,
    });
  });
});
