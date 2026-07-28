/**
 * Clarity records the screen, so its gates are wider than GTM's: admin, staff
 * and internal routes are excluded outright, and the only things that may be
 * attached to a session are five bounded tags.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { resetConsent, resetConsentCacheForTests, setConsent } from '@/features/tracking/consent.js';
import {
  CLARITY_ALLOWED_TAGS,
  ClarityTagRejected,
  identifyClarity,
  isClarityLoaded,
  loadClarity,
  resetClarityForTests,
  setClarityTag,
  shouldLoadClarity,
} from '@/features/tracking/clarity.js';
import { isSensitiveFormRoute } from '@/features/tracking/config.js';
import { installFakeDom, uninstallFakeDom } from './fakeDom.js';

const SETTINGS = { 'analytics.clarity_project_id': 'abc123xyz' };

let dom;

beforeEach(() => {
  dom = installFakeDom({ pathname: '/' });
  resetClarityForTests();
  resetConsent();
  resetConsentCacheForTests();
});

afterEach(() => {
  uninstallFakeDom();
});

describe('route exclusions', () => {
  it('is excluded on /admin', () => {
    setConsent({ analytics: true });
    expect(loadClarity({ settings: SETTINGS, pathname: '/admin' })).toEqual({
      loaded: false,
      reason: 'internal-route',
    });
    expect(dom.appended).toHaveLength(0);
    expect(isClarityLoaded()).toBe(false);
  });

  it('is excluded everywhere under /admin/*', () => {
    setConsent({ analytics: true });
    const paths = [
      '/admin/orders',
      '/admin/orders/42',
      '/admin/users',
      '/admin/analytics/customers/cohorts',
      '/admin/settings',
    ];
    for (const path of paths) {
      resetClarityForTests();
      expect(loadClarity({ settings: SETTINGS, pathname: path }), path).toEqual({
        loaded: false,
        reason: 'internal-route',
      });
    }
    expect(dom.appended).toHaveLength(0);
  });

  it('is excluded on staff, internal and payment-mock routes', () => {
    setConsent({ analytics: true });
    for (const path of ['/staff/queue', '/internal/tools', '/payments/mock/txn_1']) {
      resetClarityForTests();
      expect(loadClarity({ settings: SETTINGS, pathname: path }).reason, path).toBe(
        'internal-route'
      );
    }
    expect(dom.appended).toHaveLength(0);
  });

  it('records an ordinary storefront route', () => {
    setConsent({ analytics: true });
    expect(loadClarity({ settings: SETTINGS, pathname: '/products/12' })).toEqual({
      loaded: true,
      reason: 'ok',
    });
    expect(dom.appended).toHaveLength(1);
    expect(dom.appended[0].src).toBe('https://www.clarity.ms/tag/abc123xyz');
  });
});

describe('the other two gates', () => {
  it('does not load without a project id', () => {
    setConsent({ analytics: true });
    expect(loadClarity({ settings: {}, pathname: '/' })).toEqual({
      loaded: false,
      reason: 'no-project-id',
    });
    expect(dom.appended).toHaveLength(0);
  });

  it('does not load without analytics consent', () => {
    expect(loadClarity({ settings: SETTINGS, pathname: '/' })).toEqual({
      loaded: false,
      reason: 'no-consent',
    });
    expect(dom.appended).toHaveLength(0);
  });

  it('is idempotent', () => {
    setConsent({ analytics: true });
    loadClarity({ settings: SETTINGS, pathname: '/' });
    expect(loadClarity({ settings: SETTINGS, pathname: '/cart' }).reason).toBe('already-loaded');
    expect(dom.appended).toHaveLength(1);
  });

  it('fails closed on an undeterminable route', () => {
    expect(
      shouldLoadClarity({ projectId: 'abc123xyz', analyticsConsent: true, pathname: null })
    ).toEqual({ ok: false, reason: 'internal-route' });
  });
});

describe('field masking', () => {
  function field() {
    return {
      attributes: {},
      setAttribute(key, value) {
        this.attributes[key] = String(value);
      },
    };
  }

  it('masks sensitive fields before the recorder is on the page', () => {
    const input = field();
    dom.maskable.push(input);
    setConsent({ analytics: true });

    loadClarity({ settings: SETTINGS, pathname: '/checkout' });

    expect(input.attributes['data-clarity-mask']).toBe('true');
  });

  it('masks every field on checkout, account, auth and order routes', () => {
    expect(isSensitiveFormRoute('/checkout')).toBe(true);
    expect(isSensitiveFormRoute('/account/addresses')).toBe(true);
    expect(isSensitiveFormRoute('/account/security')).toBe(true);
    expect(isSensitiveFormRoute('/orders/42')).toBe(true);
    expect(isSensitiveFormRoute('/login')).toBe(true);
    expect(isSensitiveFormRoute('/products/12')).toBe(false);
  });
});

describe('custom tags', () => {
  beforeEach(() => {
    setConsent({ analytics: true });
    loadClarity({ settings: SETTINGS, pathname: '/' });
  });

  it('permits exactly the five documented dimensions', () => {
    expect(CLARITY_ALLOWED_TAGS.slice()).toEqual([
      'page_type',
      'device_category',
      'customer_type',
      'checkout_step',
      'experiment_id',
    ]);
    for (const tag of CLARITY_ALLOWED_TAGS) {
      expect(() => setClarityTag(tag, 'value')).not.toThrow();
    }
  });

  it('refuses a tag nobody reviewed', () => {
    for (const tag of ['customer_email', 'user_id', 'shipping_pincode', 'order_number']) {
      expect(() => setClarityTag(tag, 'x'), tag).toThrow(ClarityTagRejected);
    }
  });

  it('refuses an allowed tag whose value looks like a customer identifier', () => {
    expect(() => setClarityTag('customer_type', 'vinaya@shopwellvia.in')).toThrow(
      ClarityTagRejected
    );
    expect(() => setClarityTag('customer_type', '+91 98765 43210')).toThrow(ClarityTagRejected);
  });

  it('refuses an object value, which could carry anything', () => {
    expect(() => setClarityTag('page_type', { email: 'a@b.com' })).toThrow(ClarityTagRejected);
  });
});

describe('pseudonymous identification', () => {
  beforeEach(() => {
    setConsent({ analytics: true });
    loadClarity({ settings: SETTINGS, pathname: '/' });
  });

  it('does nothing unless an admin has explicitly enabled it', () => {
    expect(identifyClarity('a1b2c3d4e5f6', { settings: SETTINGS })).toBe(false);
  });

  it('refuses a raw customer identifier even when enabled', () => {
    const settings = { ...SETTINGS, 'analytics.clarity_pseudonymous_id_enabled': 'true' };
    expect(() => identifyClarity('vinaya@shopwellvia.in', { settings })).toThrow(
      ClarityTagRejected
    );
    expect(() => identifyClarity('9876543210', { settings })).toThrow(ClarityTagRejected);
    expect(() => identifyClarity('42', { settings })).toThrow(ClarityTagRejected);
  });

  it('accepts an opaque token when enabled', () => {
    const settings = { ...SETTINGS, 'analytics.clarity_pseudonymous_id_enabled': 'true' };
    expect(identifyClarity('7f3c9a1be4d24f0aa9', { settings })).toBe(true);
  });
});
