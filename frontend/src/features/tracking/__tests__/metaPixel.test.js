/**
 * The four gates on the Meta Pixel, asserted one at a time, plus the properties
 * the rest of the system depends on: the bootstrap is a `src` script (never
 * inline, or the CSP would need 'unsafe-inline'), it is appended exactly once
 * however many times the route changes, event payloads carry the shapes Meta
 * needs to compute ROAS, and one order can never be counted twice.
 *
 * `production: true` is passed explicitly throughout. The pixel's first gate is
 * `import.meta.env.PROD`, which is false under vitest — that gate has its own
 * test below, and every other test has to step over it to reach what it is
 * actually asserting.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  resetConsent,
  resetConsentCacheForTests,
  setConsent,
} from '@/features/tracking/consent.js';
import {
  META_PIXEL_ID,
  META_SCRIPT_ID,
  META_SCRIPT_SRC,
  MetaEv,
  isMetaPixelLoaded,
  loadMetaPixel,
  metaPageView,
  metaTrack,
  resetMetaPixelForTests,
  shouldLoadMetaPixel,
  trackAddToCart,
  trackInitiateCheckout,
  trackLead,
  trackPurchase,
  trackRegistration,
  trackViewContent,
} from '@/features/tracking/metaPixel.js';
import { installFakeDom, uninstallFakeDom } from './fakeDom.js';

let dom;

/** Every `fbq(...)` call made since the last reset. */
function fbqCalls() {
  return globalThis.window?.fbq?.mock?.calls ?? [];
}

/** `fbq('track', ...)` calls only, as `[event, params, opts]`. */
function trackCalls() {
  return fbqCalls()
    .filter((args) => args[0] === 'track')
    .map((args) => args.slice(1));
}

/** Load the pixel with every gate satisfied, then stub `fbq` so sends are visible. */
function loadWithConsent(pathname = '/') {
  setConsent({ marketing: true });
  const result = loadMetaPixel({ pathname, production: true });
  globalThis.window.fbq = vi.fn();
  return result;
}

beforeEach(() => {
  dom = installFakeDom({ pathname: '/' });
  // sessionStorage is what makes a purchase fire once; the node env has none.
  const store = new Map();
  globalThis.sessionStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  };
  resetMetaPixelForTests();
  resetConsent();
  resetConsentCacheForTests();
});

afterEach(() => {
  uninstallFakeDom();
  delete globalThis.sessionStorage;
});

describe('the pixel id', () => {
  it('is the configured Wellvia pixel', () => {
    expect(META_PIXEL_ID).toBe('1335939612023667');
  });

  // The id is interpolated into a foreign script's account slot. A typo'd or
  // injected value would report our whole funnel into someone else's account,
  // and no error would ever surface.
  it('rejects anything that is not a numeric id', () => {
    for (const bad of ['GTM-ABCD', 'https://evil.example/x.js', '123', '', '12345678901x']) {
      expect(
        shouldLoadMetaPixel({
          pixelId: bad,
          marketingConsent: true,
          pathname: '/',
          production: true,
        }).ok,
      ).toBe(false);
    }
  });
});

describe('gate 1: production builds only', () => {
  // The id is compiled in, so unlike the GTM container there is no "unset in
  // dev" state. Without this gate every dev session and local test order would
  // train the live pixel on developer behaviour.
  it('does not load in a dev build', () => {
    setConsent({ marketing: true });
    expect(loadMetaPixel({ pathname: '/', production: false })).toEqual({
      loaded: false,
      reason: 'not-production',
    });
    expect(dom.appended).toHaveLength(0);
    expect(isMetaPixelLoaded()).toBe(false);
  });

  it('is the gate that vitest itself trips, with no override', () => {
    setConsent({ marketing: true });
    expect(loadMetaPixel({ pathname: '/' }).reason).toBe('not-production');
  });
});

describe('gate 2: marketing consent', () => {
  it('does not load before the customer has decided', () => {
    expect(loadMetaPixel({ pathname: '/', production: true })).toEqual({
      loaded: false,
      reason: 'no-consent',
    });
    expect(dom.appended).toHaveLength(0);
  });

  it('does not load when marketing is refused', () => {
    setConsent({ marketing: false, analytics: true });
    expect(loadMetaPixel({ pathname: '/', production: true }).reason).toBe('no-consent');
  });

  // The pixel is an advertising tag, so analytics consent is the wrong
  // permission — granting it must not switch on ad tracking.
  it('is NOT satisfied by analytics consent alone', () => {
    setConsent({ analytics: true });
    expect(loadMetaPixel({ pathname: '/', production: true }).reason).toBe('no-consent');
    expect(dom.appended).toHaveLength(0);
  });

  it('loads once marketing is granted', () => {
    setConsent({ marketing: true });
    expect(loadMetaPixel({ pathname: '/', production: true })).toEqual({
      loaded: true,
      reason: 'ok',
    });
    expect(isMetaPixelLoaded()).toBe(true);
  });
});

describe('gate 3: internal routes', () => {
  // The admin shell renders orders, addresses and customer records. A foreign
  // ad script has no business on those pages.
  it.each(['/admin', '/admin/orders/12', '/staff', '/internal', '/payments/mock/abc'])(
    'does not load on %s',
    (pathname) => {
      setConsent({ marketing: true });
      expect(loadMetaPixel({ pathname, production: true })).toEqual({
        loaded: false,
        reason: 'internal-route',
      });
      expect(dom.appended).toHaveLength(0);
    },
  );

  it('loads on the payment RETURN page, which is not internal', () => {
    setConsent({ marketing: true });
    expect(loadMetaPixel({ pathname: '/payments/return', production: true }).loaded).toBe(true);
  });
});

describe('the bootstrap', () => {
  // An inline script would force 'unsafe-inline' into script-src and retire the
  // CSP as an XSS defence for every other script on the page.
  it('is a src script, never inline code', () => {
    loadWithConsent();
    expect(dom.appended).toHaveLength(1);
    const [script] = dom.appended;
    expect(script.tagName).toBe('SCRIPT');
    expect(script.src).toBe(META_SCRIPT_SRC);
    expect(script.async).toBe(true);
    expect(script.id).toBe(META_SCRIPT_ID);
    expect(script.text).toBeUndefined();
    expect(script.innerHTML).toBeUndefined();
  });

  it('requests only connect.facebook.net — the one host in the CSP', () => {
    loadWithConsent();
    expect(dom.appended[0].src.startsWith('https://connect.facebook.net/')).toBe(true);
  });

  it('initialises the pixel exactly once, however many routes are visited', () => {
    setConsent({ marketing: true });
    loadMetaPixel({ pathname: '/', production: true });
    // Read the shim's queue, not a spy: `init` happens inside loadMetaPixel,
    // before any test could have stubbed `fbq`, and the real fbevents.js is what
    // would normally drain this.
    const queued = globalThis.window.fbq.queue;
    expect(queued.filter((args) => args[0] === 'init')).toEqual([['init', META_PIXEL_ID]]);

    for (const pathname of ['/products', '/products/7', '/cart', '/checkout']) {
      expect(loadMetaPixel({ pathname, production: true })).toEqual({
        loaded: true,
        reason: 'already-loaded',
      });
    }
    expect(dom.appended).toHaveLength(1);
    expect(globalThis.window.fbq.queue.filter((args) => args[0] === 'init')).toHaveLength(1);
  });

  it('queues events fired before fbevents.js has replaced the shim', () => {
    setConsent({ marketing: true });
    loadMetaPixel({ pathname: '/', production: true });
    // No `callMethod` yet — that is what the real script installs on load — so
    // the call lands in the queue for it to replay, behind the init.
    metaTrack(MetaEv.PAGE_VIEW);
    expect(globalThis.window.fbq.queue).toEqual([
      ['init', META_PIXEL_ID],
      ['track', 'PageView'],
    ]);
  });
});

describe('events are silent when the pixel is not loaded', () => {
  // Roughly 20–30% of visitors block fbevents.js outright, and an unconsented
  // visitor never loads it at all. Neither may break a page.
  it('no-ops every helper rather than throwing', () => {
    expect(metaTrack(MetaEv.PAGE_VIEW)).toEqual({ sent: false, reason: 'not-loaded' });
    expect(trackViewContent({ id: 1, price: 10 }).sent).toBe(false);
    expect(trackAddToCart({ productId: 1 }).sent).toBe(false);
    expect(trackInitiateCheckout({ items: [{ product_id: 1, quantity: 1 }] }).sent).toBe(false);
    expect(trackPurchase({ id: 1, total_amount: 10 }).sent).toBe(false);
    expect(trackRegistration().sent).toBe(false);
    expect(trackLead().sent).toBe(false);
  });
});

describe('PageView', () => {
  // This is a single-page app: fbevents.js reports a PageView when it loads and
  // never again, so without a per-route call one visit is one page view.
  it('fires per route', () => {
    loadWithConsent();
    metaPageView({ pathname: '/' });
    metaPageView({ pathname: '/products' });
    metaPageView({ pathname: '/products/7' });
    expect(trackCalls()).toEqual([['PageView'], ['PageView'], ['PageView']]);
  });

  it('stays silent on an internal route', () => {
    loadWithConsent();
    expect(metaPageView({ pathname: '/admin/orders' }).sent).toBe(false);
    expect(trackCalls()).toHaveLength(0);
  });
});

describe('event payloads', () => {
  beforeEach(() => loadWithConsent());

  it('ViewContent carries the id as a string and an INR value', () => {
    trackViewContent({ id: 42, price: '499.00', name: 'Sleep Gummies' });
    expect(trackCalls()[0]).toEqual([
      'ViewContent',
      { content_type: 'product', content_ids: ['42'], value: 499, currency: 'INR' },
    ]);
  });

  // Meta matches content_ids against the catalog as TEXT — 42 and '42' are not
  // the same key there.
  it('never sends a numeric content id', () => {
    trackViewContent({ id: 42, price: 1 });
    expect(trackCalls()[0][1].content_ids).toEqual(['42']);
  });

  // content_name would trip the shared PII denylist ("name" by substring), and
  // Meta resolves names from the catalog by id anyway.
  it('sends no name field that the PII denylist would reject', () => {
    trackViewContent({ id: 42, price: 1, name: 'Sleep Gummies' });
    expect(Object.keys(trackCalls()[0][1])).not.toContain('content_name');
  });

  it('AddToCart multiplies unit price by quantity', () => {
    trackAddToCart({ productId: 7, quantity: 3, price: '250.50' });
    expect(trackCalls()[0]).toEqual([
      'AddToCart',
      {
        content_type: 'product',
        content_ids: ['7'],
        contents: [{ id: '7', quantity: 3 }],
        num_items: 3,
        value: 751.5,
        currency: 'INR',
      },
    ]);
  });

  // A zero-value AddToCart is worse than a valueless one: value-based bidding
  // would learn that our cart adds are worth nothing.
  it('omits value entirely when the caller has no price', () => {
    trackAddToCart({ productId: 7, quantity: 2 });
    const params = trackCalls()[0][1];
    expect(params).not.toHaveProperty('value');
    expect(params).not.toHaveProperty('currency');
    expect(params.num_items).toBe(2);
  });

  it('InitiateCheckout sums the cart', () => {
    trackInitiateCheckout({
      items: [
        { product_id: 1, quantity: 2 },
        { product_id: 2, quantity: 1 },
      ],
      total: '1499.00',
    });
    expect(trackCalls()[0]).toEqual([
      'InitiateCheckout',
      {
        content_type: 'product',
        content_ids: ['1', '2'],
        contents: [
          { id: '1', quantity: 2 },
          { id: '2', quantity: 1 },
        ],
        num_items: 3,
        value: 1499,
        currency: 'INR',
      },
    ]);
  });

  it('InitiateCheckout stays silent for an empty cart', () => {
    expect(trackInitiateCheckout({ items: [] }).sent).toBe(false);
    expect(trackInitiateCheckout(undefined).sent).toBe(false);
    expect(trackCalls()).toHaveLength(0);
  });

  it('Lead carries a fixed category, never customer text', () => {
    trackLead('contact');
    expect(trackCalls()[0]).toEqual(['Lead', { content_category: 'contact' }]);
  });
});

describe('Purchase', () => {
  beforeEach(() => loadWithConsent());

  it('carries a numeric value, INR, and an eventID', () => {
    trackPurchase(
      {
        id: 12,
        total_amount: '1499.00',
        currency: 'INR',
        items: [{ product_id: 5, quantity: 2 }],
      },
      { transactionId: 'MT240725XYZ' },
    );
    const [event, params, opts] = trackCalls()[0];
    expect(event).toBe('Purchase');
    // A plain number — Meta cannot compute ROAS from "₹1,499".
    expect(params.value).toBe(1499);
    expect(typeof params.value).toBe('number');
    expect(params.currency).toBe('INR');
    expect(params.contents).toEqual([{ id: '5', quantity: 2 }]);
    expect(params.num_items).toBe(2);
    expect(opts).toEqual({ eventID: 'MT240725XYZ' });
  });

  it('defaults the eventID to the shared order-id rule', () => {
    trackPurchase({ id: 12, order_number: 'WV-2026-0012', total_amount: 100 });
    expect(trackCalls()[0][2]).toEqual({ eventID: 'WV-2026-0012' });
  });

  it('falls back to ORD{id} when there is no order number', () => {
    trackPurchase({ id: 12, total_amount: 100 });
    expect(trackCalls()[0][2]).toEqual({ eventID: 'ORD12' });
  });

  // The payment-return page polls the server, so this component re-renders every
  // 1.5s until the status is terminal, and the page is reachable from history.
  it('fires once per transaction however many times it is called', () => {
    const order = { id: 12, total_amount: 1499 };
    expect(trackPurchase(order, { transactionId: 'MT1' }).sent).toBe(true);
    expect(trackPurchase(order, { transactionId: 'MT1' })).toEqual({
      sent: false,
      reason: 'already-sent',
    });
    expect(trackPurchase(order, { transactionId: 'MT1' }).sent).toBe(false);
    expect(trackCalls()).toHaveLength(1);
  });

  it('still reports a genuinely different transaction', () => {
    expect(trackPurchase({ id: 1, total_amount: 10 }, { transactionId: 'MT1' }).sent).toBe(true);
    expect(trackPurchase({ id: 2, total_amount: 20 }, { transactionId: 'MT2' }).sent).toBe(true);
    expect(trackCalls()).toHaveLength(2);
  });

  it('refuses an order it cannot identify', () => {
    expect(trackPurchase(undefined).sent).toBe(false);
    expect(trackPurchase({}).sent).toBe(false);
    expect(trackCalls()).toHaveLength(0);
  });

  // A suppressed event must not burn the one chance a later consented load has.
  it('does not mark a transaction sent when the pixel never fired', () => {
    resetMetaPixelForTests();
    expect(trackPurchase({ id: 9, total_amount: 5 }, { transactionId: 'MT9' }).sent).toBe(false);
    loadWithConsent();
    expect(trackPurchase({ id: 9, total_amount: 5 }, { transactionId: 'MT9' }).sent).toBe(true);
  });

  it('survives sessionStorage being unavailable', () => {
    delete globalThis.sessionStorage;
    expect(() => trackPurchase({ id: 3, total_amount: 30 })).not.toThrow();
    expect(trackCalls()).toHaveLength(1);
  });
});

describe('PII', () => {
  // One denylist governs everything that leaves the browser, GA4 and Meta alike.
  it('refuses to send a payload carrying a denylisted parameter', () => {
    loadWithConsent();
    expect(() => metaTrack('Lead', { customer_email: 'a@b.com' })).toThrow(/PII/i);
    expect(trackCalls()).toHaveLength(0);
  });
});
