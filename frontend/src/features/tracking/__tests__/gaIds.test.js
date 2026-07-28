/**
 * `captureGaIds` hands the browser's GA4 client and session ids to checkout so
 * the server-side `purchase` lands on the same session as the journey that
 * produced it. Those ids *are* the analytics cookie, so reading them is the
 * processing a customer declines when they decline analytics.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { resetConsent, resetConsentCacheForTests, setConsent } from '@/features/tracking/consent.js';
import {
  captureGaIds,
  parseGaClientId,
  parseGaSession,
  readCookieJar,
} from '@/features/tracking/index.js';
import { installFakeDom, uninstallFakeDom } from './fakeDom.js';

const COOKIE =
  '_ga=GA1.1.1234567890.1753600000; _ga_ABCD1234=GS1.1.1753699999.3.1.1753700123.0.0.0; ' +
  'cart_id=xyz';

const SETTINGS = { 'analytics.ga4_measurement_id': 'G-ABCD1234' };

beforeEach(() => {
  installFakeDom({ pathname: '/checkout', cookie: COOKIE });
  resetConsent();
  resetConsentCacheForTests();
});

afterEach(() => {
  uninstallFakeDom();
});

describe('consent gate', () => {
  it('returns nothing when consent has not been given', () => {
    expect(captureGaIds({ settings: SETTINGS })).toBeNull();
  });

  it('returns nothing when analytics was actively declined', () => {
    setConsent({ analytics: false, marketing: true });
    expect(captureGaIds({ settings: SETTINGS })).toBeNull();
  });

  it('returns null rather than an empty object, so nothing spreads into a body', () => {
    const captured = captureGaIds({ settings: SETTINGS });
    expect(captured).toBeNull();
    expect({ ...captured }).toEqual({});
  });
});

describe('with consent granted', () => {
  beforeEach(() => {
    setConsent({ analytics: true });
  });

  it('captures the client id and the configured stream session', () => {
    expect(captureGaIds({ settings: SETTINGS })).toEqual({
      client_id: '1234567890.1753600000',
      session_id: '1753699999',
      session_number: 3,
    });
  });

  it('falls back to whichever _ga_* cookie is present when unconfigured', () => {
    expect(captureGaIds({ settings: {} })).toEqual({
      client_id: '1234567890.1753600000',
      session_id: '1753699999',
      session_number: 3,
    });
  });

  it('returns null when GA has not written its cookies yet', () => {
    uninstallFakeDom();
    installFakeDom({ pathname: '/checkout', cookie: 'cart_id=xyz' });
    expect(captureGaIds({ settings: SETTINGS })).toBeNull();
  });

  it('never returns a key the PII guard would reject', () => {
    const captured = captureGaIds({ settings: SETTINGS });
    expect(Object.keys(captured).sort()).toEqual(['client_id', 'session_id', 'session_number']);
  });
});

describe('cookie parsing', () => {
  it('reads a cookie jar without tripping on values containing "="', () => {
    expect(readCookieJar('a=1; b=x=y; c=')).toEqual({ a: '1', b: 'x=y', c: '' });
    expect(readCookieJar('')).toEqual({});
    expect(readCookieJar(undefined)).toEqual({});
  });

  it('parses the GA1 client id', () => {
    expect(parseGaClientId('GA1.1.1234567890.1753600000')).toBe('1234567890.1753600000');
    expect(parseGaClientId('nonsense')).toBeNull();
    expect(parseGaClientId(undefined)).toBeNull();
  });

  it('parses both session cookie shapes Google ships', () => {
    expect(parseGaSession('GS1.1.1753699999.3.1.1753700123.0.0.0')).toEqual({
      session_id: '1753699999',
      session_number: 3,
    });
    expect(parseGaSession('GS2.1.s1753699999$o7$g1$t1753700123$j0$l0$h0')).toEqual({
      session_id: '1753699999',
      session_number: 7,
    });
    expect(parseGaSession('junk')).toBeNull();
  });
});
