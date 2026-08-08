import { describe, it, expect } from 'vitest';
import {
  hasInvoice,
  isOrderCancellable,
  readApiErrorMessage,
} from '@/features/orders/api.js';

describe('isOrderCancellable (mirrors OrderService.cancel_for_customer)', () => {
  it('is false for a missing order', () => {
    expect(isOrderCancellable(null)).toBe(false);
    expect(isOrderCancellable(undefined)).toBe(false);
  });

  it('pending orders are always cancellable', () => {
    expect(isOrderCancellable({ status: 'pending' })).toBe(true);
  });

  it('paid orders are cancellable only before any carrier hand-off', () => {
    expect(isOrderCancellable({ status: 'paid' })).toBe(true);
    expect(isOrderCancellable({ status: 'paid', shipping_awb: 'AWB1' })).toBe(false);
    expect(isOrderCancellable({ status: 'paid', tracking_number: 'T1' })).toBe(false);
    expect(isOrderCancellable({ status: 'paid', pickup_id: 'P1' })).toBe(false);
    expect(
      isOrderCancellable({ status: 'paid', shipment_created_at: '2026-07-01T00:00:00Z' }),
    ).toBe(false);
  });

  it('pre-carrier shipment rows do not block cancellation', () => {
    expect(
      isOrderCancellable({
        status: 'paid',
        shipments: [
          { shipment_status: 'pending' },
          { shipment_status: 'ready_to_ship' },
          { shipment_status: 'cancelled' },
        ],
      }),
    ).toBe(true);
  });

  it('a shipment advanced past pre-carrier states, or carrying an AWB, blocks it', () => {
    expect(
      isOrderCancellable({ status: 'paid', shipments: [{ shipment_status: 'in_transit' }] }),
    ).toBe(false);
    expect(
      isOrderCancellable({
        status: 'paid',
        shipments: [{ shipment_status: 'pending', awb_number: 'AWB2' }],
      }),
    ).toBe(false);
    expect(
      isOrderCancellable({
        status: 'paid',
        shipments: [{ shipment_status: 'pending', tracking_number: 'T2' }],
      }),
    ).toBe(false);
  });

  it('terminal / post-shipment statuses are never customer-cancellable', () => {
    for (const status of ['shipped', 'delivered', 'cancelled', 'refunded']) {
      expect(isOrderCancellable({ status })).toBe(false);
    }
  });
});

describe('hasInvoice (backend 409s PENDING/CANCELLED — no invoice exists)', () => {
  it('matches the invoiceable status set exactly', () => {
    for (const status of ['paid', 'shipped', 'delivered', 'refunded']) {
      expect(hasInvoice({ status })).toBe(true);
    }
    for (const status of ['pending', 'cancelled']) {
      expect(hasInvoice({ status })).toBe(false);
    }
    expect(hasInvoice(null)).toBe(false);
  });
});

describe('readApiErrorMessage', () => {
  it('extracts the backend error-envelope message', async () => {
    const err = { response: { data: { error: { message: 'Order already shipped' } } } };
    await expect(readApiErrorMessage(err, 'fallback')).resolves.toBe(
      'Order already shipped',
    );
  });

  it('falls back when there is no envelope', async () => {
    await expect(readApiErrorMessage({}, 'fallback')).resolves.toBe('fallback');
    await expect(
      readApiErrorMessage({ response: { data: {} } }, 'fallback'),
    ).resolves.toBe('fallback');
  });

  it('parses a Blob body (invoice download errors arrive as blobs)', async () => {
    const blob = new Blob([JSON.stringify({ error: { message: 'No invoice yet' } })], {
      type: 'application/json',
    });
    await expect(
      readApiErrorMessage({ response: { data: blob } }, 'fallback'),
    ).resolves.toBe('No invoice yet');
  });

  it('falls back when the Blob body is not valid JSON', async () => {
    const blob = new Blob(['<html>gateway error</html>'], { type: 'text/html' });
    await expect(
      readApiErrorMessage({ response: { data: blob } }, 'fallback'),
    ).resolves.toBe('fallback');
  });
});
