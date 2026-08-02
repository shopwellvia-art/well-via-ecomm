import { describe, it, expect } from 'vitest';

import { cartUnitCount, lineDisplayAmount } from '@/features/cart/summary.js';

// Regression: ISSUE-003 / ISSUE-005 — checkout summary lines did not reconcile
// with Subtotal, and the cart counted lines while the header counted units.
// Found by /qa on 2026-08-02 against http://localhost:8090
// Report: .gstack/qa-reports/qa-report-localhost-8090-2026-08-02.md

describe('cartUnitCount (cart heading + "Price (N items)" semantics)', () => {
  it('sums quantities across lines rather than counting lines', () => {
    // The exact cart that shipped the bug: 2 lines, 5 units.
    const items = [
      { product_id: 13, quantity: 2 },
      { product_id: 21, quantity: 3 },
    ];
    expect(cartUnitCount(items)).toBe(5);
    expect(cartUnitCount(items)).not.toBe(items.length);
  });

  it('matches the guest-store header badge for a single multi-unit line', () => {
    // Header showed 3, cart page showed "(1)" for this same cart.
    expect(cartUnitCount([{ product_id: 13, quantity: 3 }])).toBe(3);
  });

  it('treats string quantities as numbers, not concatenated text', () => {
    expect(cartUnitCount([{ quantity: '2' }, { quantity: '3' }])).toBe(5);
  });

  it('returns 0 for an empty, missing, or non-array cart', () => {
    expect(cartUnitCount([])).toBe(0);
    expect(cartUnitCount(undefined)).toBe(0);
    expect(cartUnitCount(null)).toBe(0);
  });

  it('ignores lines with a missing or unparseable quantity', () => {
    expect(cartUnitCount([{ quantity: 2 }, {}, { quantity: 'abc' }])).toBe(2);
  });
});

describe('lineDisplayAmount (item row when Tax is its own summary row)', () => {
  it('prefers the pre-tax line_subtotal so rows sum to Subtotal', () => {
    // Server payload for the taxed product that exposed the bug: the row was
    // printing 352.82 (tax-inclusive) above a "Subtotal ₹499.00" that excluded
    // tax, so 352.82 + 200.00 did not reconcile with 499.00.
    const taxed = { unit_price: '299.00', line_subtotal: '299.00', line_tax: '53.82', line_total: '352.82' };
    const untaxed = { unit_price: '100.00', line_subtotal: '200.00', line_tax: '0.00', line_total: '200.00' };

    expect(lineDisplayAmount(taxed)).toBe('299.00');
    expect(lineDisplayAmount(untaxed)).toBe('200.00');

    const summed = Number(lineDisplayAmount(taxed)) + Number(lineDisplayAmount(untaxed));
    expect(summed).toBe(499);
  });

  it('falls back to line_total when the server omits line_subtotal', () => {
    expect(lineDisplayAmount({ line_total: '250.00', unit_price: '125.00' })).toBe('250.00');
  });

  it('falls back to unit_price when only that is present', () => {
    expect(lineDisplayAmount({ unit_price: '125.00' })).toBe('125.00');
  });

  it('keeps a zero line_subtotal instead of falling through to line_total', () => {
    // A fully discounted line is 0, not "missing" — ?? must not treat it as absent.
    expect(lineDisplayAmount({ line_subtotal: 0, line_total: '99.00' })).toBe(0);
  });

  it('returns undefined for a missing item rather than throwing', () => {
    expect(lineDisplayAmount(undefined)).toBeUndefined();
    expect(lineDisplayAmount(null)).toBeUndefined();
  });
});
