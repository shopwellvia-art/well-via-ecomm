/**
 * Regression: ISSUE-001 — payments/return polled forever when status checks fail.
 * Found by /qa on 2026-08-03
 * Report: .gstack/qa-reports/qa-report-wellvia-payment-flow-2026-08-03.md
 *
 * A 401 (session expired after the gateway redirect) or 404 (unknown mtid)
 * errored the query but the old interval callback only inspected
 * order_status, so it returned 1500 forever — an endless "Confirming your
 * payment" spinner whose every tick also wiped the stored session via the
 * apiClient 401 interceptor.
 */
import { describe, it, expect } from 'vitest';
import { pollIntervalFor } from '../PaymentReturnPage.jsx';

const query = (status, orderStatus) => ({
  state: { status, data: orderStatus === undefined ? undefined : { order_status: orderStatus } },
});

describe('pollIntervalFor', () => {
  it('stops polling once the query errors — THE regression', () => {
    expect(pollIntervalFor(query('error'))).toBe(false);
  });

  it('stops polling even if stale data accompanies the error state', () => {
    expect(pollIntervalFor(query('error', 'pending'))).toBe(false);
  });

  it.each(['paid', 'cancelled', 'refunded'])(
    'stops polling on terminal order status %s',
    (s) => {
      expect(pollIntervalFor(query('success', s))).toBe(false);
    },
  );

  it('keeps polling at 1.5s while the order is pending', () => {
    expect(pollIntervalFor(query('success', 'pending'))).toBe(1500);
  });

  it('keeps polling before any data has arrived', () => {
    expect(pollIntervalFor(query('pending'))).toBe(1500);
  });

  it('keeps polling on an unrecognized order status rather than freezing', () => {
    expect(pollIntervalFor(query('success', 'weird_new_state'))).toBe(1500);
  });
});
