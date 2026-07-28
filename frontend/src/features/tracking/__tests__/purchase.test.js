/**
 * The purchase boundary.
 *
 * In `server_only` — the default — the browser must not merely fail to *send*
 * a purchase; it must not produce one. An event sitting in `window.dataLayer`
 * is one container rule away from being sent, and then GA4 has two purchases
 * for one order.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { resetConsent, resetConsentCacheForTests, setConsent } from '@/features/tracking/consent.js';
import {
  DEFAULT_PURCHASE_DELIVERY,
  PURCHASE_DELIVERY,
  browserPurchaseAllowed,
  buildPurchaseParams,
  purchaseDeliveryMode,
  pushPurchase,
} from '@/features/tracking/purchase.js';
import { dataLayerEntries, eventsNamed, installFakeDom, uninstallFakeDom } from './fakeDom.js';

const ORDER = {
  id: 42,
  order_number: null,
  total_amount: '1299.00',
  tax_amount: '99.00',
  shipping_amount: '0.00',
  currency: 'INR',
  coupon_code: 'WELCOME10',
  // Present on a real OrderRead and deliberately never read:
  shipping_address: '12 MG Road, Bengaluru 560001',
  shipping_pincode: '560001',
  shipping_address_snapshot: { line1: '12 MG Road', phone: '9876543210' },
  items: [
    { product_id: 7, sku: 'WV-ASH-60', name: 'Ashwagandha', quantity: 2, unit_price: '499.00' },
    { product_id: 9, sku: 'WV-TRI-30', name: 'Triphala', quantity: 1, unit_price: '301.00' },
  ],
};

beforeEach(() => {
  installFakeDom({ pathname: '/payments/return' });
  resetConsent();
  resetConsentCacheForTests();
  setConsent({ analytics: true });
  globalThis.sessionStorage.clear();
});

afterEach(() => {
  uninstallFakeDom();
});

describe('delivery mode', () => {
  it('defaults to server_only when the setting is absent or unrecognised', () => {
    expect(DEFAULT_PURCHASE_DELIVERY).toBe(PURCHASE_DELIVERY.SERVER_ONLY);
    expect(purchaseDeliveryMode(undefined)).toBe('server_only');
    expect(purchaseDeliveryMode({})).toBe('server_only');
    expect(purchaseDeliveryMode({ 'analytics.ga4_purchase_delivery': 'nonsense' })).toBe(
      'server_only'
    );
  });

  it('reads the configured mode, flat key or nested', () => {
    expect(purchaseDeliveryMode({ 'analytics.ga4_purchase_delivery': 'both' })).toBe('both');
    expect(purchaseDeliveryMode({ analytics: { ga4_purchase_delivery: 'browser_only' } })).toBe(
      'browser_only'
    );
  });

  it('allows a browser purchase only in browser_only and both', () => {
    expect(browserPurchaseAllowed('server_only')).toBe(false);
    expect(browserPurchaseAllowed('browser_only')).toBe(true);
    expect(browserPurchaseAllowed('both')).toBe(true);
  });
});

describe('server_only suppression', () => {
  it('pushes nothing at all — not even a suppressed event object', () => {
    const result = pushPurchase(ORDER, { settings: {} });

    expect(result).toEqual({
      delivered: false,
      reason: 'suppressed:server_only',
      payload: null,
    });
    expect(eventsNamed('purchase')).toHaveLength(0);
    expect(dataLayerEntries()).toHaveLength(0);
  });

  it('suppresses explicitly too, not just by default', () => {
    const settings = { 'analytics.ga4_purchase_delivery': 'server_only' };
    expect(pushPurchase(ORDER, { settings }).delivered).toBe(false);
    expect(dataLayerEntries()).toHaveLength(0);
  });
});

describe('browser delivery', () => {
  const settings = { 'analytics.ga4_purchase_delivery': 'browser_only' };

  it('pushes one purchase with the shared transaction id', () => {
    const result = pushPurchase(ORDER, { settings });

    expect(result.delivered).toBe(true);
    const [event] = eventsNamed('purchase');
    expect(event.transaction_id).toBe('ORD42'); // same rule the server applies
    expect(event.value).toBe(1299);
    expect(event.currency).toBe('INR');
    expect(event.items).toHaveLength(2);
    expect(event.items[0]).toEqual({
      item_id: 'WV-ASH-60',
      item_name: 'Ashwagandha',
      index: 0,
      price: 499,
      quantity: 2,
    });
  });

  it('never carries the address fields the order object also holds', () => {
    pushPurchase(ORDER, { settings });
    const [event] = eventsNamed('purchase');
    const flat = JSON.stringify(event);

    expect(flat).not.toContain('MG Road');
    expect(flat).not.toContain('560001');
    expect(flat).not.toContain('9876543210');
    expect(Object.keys(event)).not.toContain('shipping_address');
  });

  it('does not send a second purchase when the confirmation page is reloaded', () => {
    expect(pushPurchase(ORDER, { settings }).delivered).toBe(true);
    expect(pushPurchase(ORDER, { settings })).toEqual({
      delivered: false,
      reason: 'already-sent',
      payload: null,
    });
    expect(eventsNamed('purchase')).toHaveLength(1);
  });

  it('stays silent without analytics consent, even in browser_only', () => {
    resetConsent();
    expect(pushPurchase(ORDER, { settings }).delivered).toBe(false);
    expect(eventsNamed('purchase')).toHaveLength(0);
  });
});

describe('buildPurchaseParams', () => {
  it('is pure and reads only the allowlisted order fields', () => {
    const params = buildPurchaseParams(ORDER);
    expect(Object.keys(params).sort()).toEqual([
      'coupon',
      'currency',
      'items',
      'shipping',
      'tax',
      'transaction_id',
      'value',
    ]);
  });

  it('prefers the allocated order number over the fallback', () => {
    const params = buildPurchaseParams({ ...ORDER, order_number: 'WV-2026-0042' });
    expect(params.transaction_id).toBe('WV-2026-0042');
  });
});
