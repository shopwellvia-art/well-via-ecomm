/**
 * The three gates on GTM, asserted one at a time, plus the two properties the
 * CSP plan depends on: the bootstrap is a `src` script (never inline), and it
 * is appended exactly once however many times the route changes.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { resetConsent, resetConsentCacheForTests, setConsent } from '@/features/tracking/consent.js';
import {
  GTM_SCRIPT_ID,
  isGtmLoaded,
  loadGtm,
  resetGtmForTests,
  shouldLoadGtm,
} from '@/features/tracking/gtm.js';
import { dataLayerEntries, installFakeDom, uninstallFakeDom } from './fakeDom.js';

const SETTINGS = { 'analytics.gtm_container_id': 'GTM-ABCD123' };

let dom;

beforeEach(() => {
  dom = installFakeDom({ pathname: '/' });
  resetGtmForTests();
  resetConsent();
  resetConsentCacheForTests();
});

afterEach(() => {
  uninstallFakeDom();
});

describe('gate 1: a container id must be configured', () => {
  it('does not load when no container id is set', () => {
    setConsent({ analytics: true });
    const result = loadGtm({ settings: {}, pathname: '/' });

    expect(result).toEqual({ loaded: false, reason: 'no-container-id' });
    expect(dom.appended).toHaveLength(0);
    expect(isGtmLoaded()).toBe(false);
  });

  it('does not load when the id is blank or malformed', () => {
    setConsent({ analytics: true });
    expect(loadGtm({ settings: { 'analytics.gtm_container_id': '  ' }, pathname: '/' })).toEqual({
      loaded: false,
      reason: 'no-container-id',
    });
    expect(
      loadGtm({
        settings: { 'analytics.gtm_container_id': 'https://evil.example/x.js' },
        pathname: '/',
      })
    ).toEqual({ loaded: false, reason: 'invalid-container-id' });
    expect(dom.appended).toHaveLength(0);
  });
});

describe('gate 2: analytics consent must be granted', () => {
  it('does not load before the customer has chosen', () => {
    const result = loadGtm({ settings: SETTINGS, pathname: '/' });

    expect(result).toEqual({ loaded: false, reason: 'no-consent' });
    expect(dom.appended).toHaveLength(0);
  });

  it('does not load when the customer has actively declined', () => {
    setConsent({ analytics: false, marketing: false });
    expect(loadGtm({ settings: SETTINGS, pathname: '/' })).toEqual({
      loaded: false,
      reason: 'no-consent',
    });
    expect(dom.appended).toHaveLength(0);
  });
});

describe('gate 3: never on an admin route', () => {
  it('does not load on /admin', () => {
    setConsent({ analytics: true });
    expect(loadGtm({ settings: SETTINGS, pathname: '/admin' })).toEqual({
      loaded: false,
      reason: 'admin-route',
    });
    expect(dom.appended).toHaveLength(0);
  });

  it('does not load anywhere under /admin', () => {
    setConsent({ analytics: true });
    for (const path of ['/admin/orders', '/admin/orders/42', '/admin/analytics/sales']) {
      resetGtmForTests();
      expect(loadGtm({ settings: SETTINGS, pathname: path }).reason).toBe('admin-route');
    }
    expect(dom.appended).toHaveLength(0);
  });

  it('does load on a storefront route with the other two gates open', () => {
    setConsent({ analytics: true });
    expect(loadGtm({ settings: SETTINGS, pathname: '/products/12' })).toEqual({
      loaded: true,
      reason: 'ok',
    });
    expect(dom.appended).toHaveLength(1);
  });

  it('fails closed when the route cannot be determined', () => {
    expect(shouldLoadGtm({ containerId: 'GTM-ABCD123', analyticsConsent: true, pathname: null })).toEqual(
      { ok: false, reason: 'admin-route' }
    );
  });
});

describe('injection', () => {
  beforeEach(() => {
    setConsent({ analytics: true });
  });

  it('is idempotent — a second call appends nothing', () => {
    expect(loadGtm({ settings: SETTINGS, pathname: '/' })).toEqual({ loaded: true, reason: 'ok' });
    expect(loadGtm({ settings: SETTINGS, pathname: '/cart' })).toEqual({
      loaded: true,
      reason: 'already-loaded',
    });
    expect(loadGtm({ settings: SETTINGS, pathname: '/checkout' }).reason).toBe('already-loaded');

    expect(dom.appended).toHaveLength(1);
    expect(dom.appended.filter((node) => node.id === GTM_SCRIPT_ID)).toHaveLength(1);
  });

  it('does not append a second tag when one is already in the document', () => {
    loadGtm({ settings: SETTINGS, pathname: '/' });
    resetGtmForTests(); // as if a fresh module instance found a warm document
    expect(loadGtm({ settings: SETTINGS, pathname: '/' })).toEqual({
      loaded: true,
      reason: 'already-loaded',
    });
    expect(dom.appended).toHaveLength(1);
  });

  it('loads by src and never by inline code — this is what keeps the CSP strict', () => {
    loadGtm({ settings: SETTINGS, pathname: '/' });
    const [script] = dom.appended;

    expect(script.src).toBe('https://www.googletagmanager.com/gtm.js?id=GTM-ABCD123');
    expect(script.async).toBe(true);
    expect(script.text).toBeUndefined();
    expect(script.innerHTML).toBeUndefined();
    expect(script.textContent).toBeUndefined();
  });

  it('pushes Consent Mode defaults before the container is requested', () => {
    loadGtm({ settings: SETTINGS, pathname: '/' });
    const entries = dataLayerEntries();

    expect(Array.from(entries[0])).toEqual([
      'consent',
      'default',
      expect.objectContaining({ analytics_storage: 'granted' }),
    ]);
    expect(entries[1]).toMatchObject({ event: 'gtm.js' });
  });
});
