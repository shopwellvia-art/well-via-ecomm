/**
 * The event contract, and the PII guard that stands at its exit.
 *
 * The expectations below are transcribed from
 * `backend/app/services/analytics/tracking_events.py`. They are asserted
 * unconditionally — if a peer later dumps the contract to JSON, or if the
 * Python source can be read from here, the same values are checked against
 * that too (see `backendContract.js`), and the transcription becomes a second
 * witness rather than the only one.
 */
import { describe, expect, it } from 'vitest';

import {
  BUSINESS_EVENT_NAMES,
  EVENT_NAMES,
  ITEM_FIELDS,
  PII_ALLOWED_EXACT,
  PII_DENYLIST,
  PiiLeak,
  REQUIRED_PARAMS,
  SCHEMA_VERSION,
  assertNoPii,
  buildEvent,
  toItem,
  transactionIdFor,
} from '@/features/tracking/dataLayer.js';
import { dumpedContract, pythonContract } from './backendContract.js';

// Transcribed from tracking_events.py. Sorted, because both sides sort.
const BACKEND_EVENTS = [
  'add_payment_info',
  'add_shipping_info',
  'add_to_cart',
  'add_to_wishlist',
  'begin_checkout',
  'login',
  'page_view',
  'purchase',
  'refund',
  'remove_from_cart',
  'search',
  'select_item',
  'select_promotion',
  'sign_up',
  'view_cart',
  'view_item',
  'view_item_list',
  'view_promotion',
];

const BACKEND_BUSINESS_EVENTS = [
  'coupon_applied',
  'coupon_failed',
  'order_cancelled',
  'payment_attempt',
  'payment_failed',
  'review_submitted',
  'search_no_results',
  'support_started',
];

const BACKEND_ITEM_FIELDS = [
  'item_id',
  'item_name',
  'item_brand',
  'item_category',
  'item_category2',
  'item_category3',
  'item_variant',
  'item_list_id',
  'item_list_name',
  'index',
  'price',
  'quantity',
  'discount',
  'coupon',
];

const BACKEND_REQUIRED_PARAMS = {
  add_to_cart: ['currency', 'value', 'items'],
  begin_checkout: ['currency', 'value', 'items'],
  purchase: ['transaction_id', 'value', 'currency', 'items'],
  refund: ['transaction_id', 'currency'],
  search: ['search_term'],
  view_item: ['currency', 'value', 'items'],
};

const BACKEND_PII_DENYLIST = [
  'email',
  'phone',
  'mobile',
  'name',
  'address',
  'street',
  'pincode',
  'postcode',
  'zip',
  'password',
  'otp',
  'card',
  'cvv',
  'upi',
  'gstin',
  'pan',
  'dob',
  'birth',
];

const BACKEND_PII_ALLOWED_EXACT = [
  'creative_name',
  'currency',
  'item_list_name',
  'item_name',
  'promotion_name',
];

describe('event contract mirrors the backend', () => {
  it('ships the same 18 GA4 events, by name', () => {
    expect(EVENT_NAMES.slice()).toEqual(BACKEND_EVENTS);
  });

  it('ships the same 8 business events, by name', () => {
    expect(BUSINESS_EVENT_NAMES.slice()).toEqual(BACKEND_BUSINESS_EVENTS);
  });

  it('ships the same item fields, in the same order', () => {
    expect(ITEM_FIELDS.slice()).toEqual(BACKEND_ITEM_FIELDS);
  });

  it('requires the same parameters per event', () => {
    const mine = Object.fromEntries(
      Object.entries(REQUIRED_PARAMS).map(([event, params]) => [event, params.slice()])
    );
    expect(mine).toEqual(BACKEND_REQUIRED_PARAMS);
  });

  it('carries the same PII denylist and the same exact-match escapes', () => {
    expect(PII_DENYLIST.slice()).toEqual(BACKEND_PII_DENYLIST);
    expect(PII_ALLOWED_EXACT.slice().sort()).toEqual(BACKEND_PII_ALLOWED_EXACT);
  });

  it('agrees with the backend on the schema version', () => {
    expect(SCHEMA_VERSION).toBe(1);
  });

  // Drift detection against the other side, when it is reachable from here.
  // Skips rather than fails when it is not: a frontend-only checkout should not
  // report a contract mismatch it has no way to see.
  const dumped = dumpedContract();
  const python = pythonContract();
  const other = dumped ? dumped.contract : python;

  it.skipIf(!other)('matches the live backend definition, not just the transcript', () => {
    expect(other.events).toEqual(BACKEND_EVENTS);
    expect(other.business_events).toEqual(BACKEND_BUSINESS_EVENTS);
    expect(other.item_fields).toEqual(BACKEND_ITEM_FIELDS);
    expect(other.pii_denylist).toEqual(BACKEND_PII_DENYLIST);
    expect(other.pii_allowed_exact.slice().sort()).toEqual(BACKEND_PII_ALLOWED_EXACT);
    expect(other.required_params).toEqual(BACKEND_REQUIRED_PARAMS);
    expect(other.schema_version).toBe(SCHEMA_VERSION);
  });

  it.skipIf(!python)('derives the transaction id the same way the backend does', () => {
    expect(python.transaction_id_fallback).toBe(true);
  });
});

describe('assertNoPii', () => {
  it('blocks a top-level customer_email', () => {
    expect(() => assertNoPii({ value: 100, customer_email: 'a@b.com' })).toThrow(PiiLeak);
  });

  it('blocks a shipping_address nested inside items', () => {
    const payload = {
      transaction_id: 'ORD42',
      items: [{ item_id: 'WV-1', item_name: 'Ashwagandha', shipping_address: '12 MG Road' }],
    };
    expect(() => assertNoPii(payload)).toThrow(PiiLeak);
    try {
      assertNoPii(payload);
    } catch (err) {
      expect(err.offenders).toContain('items[0].shipping_address');
    }
  });

  it('blocks a phone_number two levels deep', () => {
    expect(() =>
      assertNoPii({ checkout: { customer: { phone_number: '9999999999' } } })
    ).toThrow(PiiLeak);
  });

  it('allows item_name — the documented exact-match escape', () => {
    expect(() =>
      assertNoPii({ currency: 'INR', items: [{ item_name: 'Ashwagandha', item_id: 'WV-1' }] })
    ).not.toThrow();
  });

  it('throws rather than sanitising, so the caller cannot be silently fixed', () => {
    const payload = { value: 100, currency: 'INR', customer_email: 'a@b.com' };
    expect(() => assertNoPii(payload)).toThrow(PiiLeak);
    expect(payload.customer_email).toBe('a@b.com'); // untouched
  });

  it('guards toItem options, not just finished events', () => {
    expect(() => toItem({ sku: 'WV-1', name: 'X' }, { customer_email: 'a@b.com' })).toThrow(
      PiiLeak
    );
  });

  it('shapes an item to the contract fields and drops everything else', () => {
    const item = toItem(
      { sku: 'WV-1', name: 'Ashwagandha', price: '499.00', flavour: '60 caps', stock: 12 },
      { index: 3, quantity: 2 }
    );
    expect(item).toEqual({
      item_id: 'WV-1',
      item_name: 'Ashwagandha',
      item_variant: '60 caps',
      price: 499,
      index: 3,
      quantity: 2,
    });
  });
});

describe('transactionIdFor', () => {
  it('falls back to ORD<id> exactly as the backend does', () => {
    expect(transactionIdFor(null, 42)).toBe('ORD42');
  });

  it('prefers the allocated order number', () => {
    expect(transactionIdFor('WV-2026-0042', 42)).toBe('WV-2026-0042');
  });

  it('treats an empty order number as unallocated, like Python truthiness', () => {
    expect(transactionIdFor('', 42)).toBe('ORD42');
  });
});

describe('buildEvent', () => {
  it('stamps the schema version so a GA4 property can tell rollouts apart', () => {
    const payload = buildEvent('view_cart', { currency: 'INR' });
    expect(payload).toEqual({
      event: 'view_cart',
      event_schema_version: SCHEMA_VERSION,
      currency: 'INR',
    });
  });

  it('drops an event whose free-text parameter holds a customer email', () => {
    expect(buildEvent('search', { search_term: 'contact me at a@b.com' })).toBeNull();
    expect(buildEvent('search', { search_term: 'ashwagandha' })).not.toBeNull();
  });
});
