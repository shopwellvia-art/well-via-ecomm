// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
  buildRazorpayOptions,
  loadRazorpayScript,
  RAZORPAY_THEME_COLOR,
} from '@/features/payments/razorpayCheckout.js';

// The backend contract for `response.checkout` on POST /checkout — Razorpay
// Standard Checkout replacing the Payment Links full-page redirect.
const CHECKOUT = {
  provider: 'razorpay',
  key_id: 'rzp_test_xxx',
  order_id: 'order_xxx',
  amount: 29900,
  currency: 'INR',
  name: 'Wellvia',
  description: 'Order ORD123',
  prefill: { email: 'customer@x.com' },
  notes: { mtid: 'ORD123' },
};

describe('buildRazorpayOptions (backend checkout contract → Razorpay options)', () => {
  it('maps every contract field onto the option name Razorpay expects', () => {
    const opts = buildRazorpayOptions(CHECKOUT);
    // key_id → key is the one rename in the contract; everything else is 1:1.
    expect(opts.key).toBe('rzp_test_xxx');
    expect(opts.order_id).toBe('order_xxx');
    expect(opts.amount).toBe(29900);
    expect(opts.currency).toBe('INR');
    expect(opts.name).toBe('Wellvia');
    expect(opts.description).toBe('Order ORD123');
    expect(opts.notes).toEqual({ mtid: 'ORD123' });
    expect(opts.prefill).toEqual({ email: 'customer@x.com' });
  });

  it('uses the brand green from tailwind.config.js as the modal theme', () => {
    // wgreen.DEFAULT — the modal header must match the Place Order button.
    expect(buildRazorpayOptions(CHECKOUT).theme).toEqual({
      color: RAZORPAY_THEME_COLOR,
    });
    expect(RAZORPAY_THEME_COLOR).toBe('#044D39');
  });

  it('merges form prefill overrides without dropping the server email', () => {
    const opts = buildRazorpayOptions(CHECKOUT, {
      prefillOverrides: { name: 'Asha Rao', contact: '+919876543210' },
    });
    expect(opts.prefill).toEqual({
      email: 'customer@x.com',
      name: 'Asha Rao',
      contact: '+919876543210',
    });
  });

  it('drops blank/null overrides so they never clobber server prefill', () => {
    // An untouched phone field is '' — sending it would blank the contact
    // Razorpay could otherwise derive; a missing name must not become null.
    const opts = buildRazorpayOptions(
      { ...CHECKOUT, prefill: { email: 'customer@x.com', contact: '+911111111111' } },
      { prefillOverrides: { name: undefined, contact: '   ' } },
    );
    expect(opts.prefill).toEqual({
      email: 'customer@x.com',
      contact: '+911111111111',
    });
  });

  it('wires handler → onSuccess and modal.ondismiss → onDismiss', () => {
    const onSuccess = vi.fn();
    const onDismiss = vi.fn();
    const opts = buildRazorpayOptions(CHECKOUT, { onSuccess, onDismiss });
    expect(opts.handler).toBe(onSuccess);
    expect(opts.modal.ondismiss).toBe(onDismiss);
  });

  it('tolerates a contract object with prefill/notes absent', () => {
    const opts = buildRazorpayOptions({
      key_id: 'k',
      order_id: 'o',
      amount: 100,
      currency: 'INR',
    });
    expect(opts.prefill).toEqual({});
    expect(opts.notes).toEqual({});
  });
});

describe('loadRazorpayScript (checkout.js injection + dedupe)', () => {
  beforeEach(() => {
    delete window.Razorpay;
    document
      .querySelectorAll('script[src^="https://checkout.razorpay.com"]')
      .forEach((s) => s.remove());
  });

  it('resolves with the existing constructor without injecting a tag', async () => {
    const fake = function Razorpay() {};
    window.Razorpay = fake;
    const [a, b] = await Promise.all([
      loadRazorpayScript(),
      loadRazorpayScript(),
    ]);
    expect(a).toBe(fake);
    expect(b).toBe(fake);
    expect(
      document.querySelector('script[src^="https://checkout.razorpay.com"]'),
    ).toBeNull();
  });

  it('injects one tag and dedupes concurrent callers onto it', async () => {
    // No window.Razorpay yet: both calls must share ONE pending <script>.
    const p1 = loadRazorpayScript();
    const p2 = loadRazorpayScript();
    const tags = document.querySelectorAll(
      'script[src^="https://checkout.razorpay.com"]',
    );
    expect(tags.length).toBe(1);

    // Simulate checkout.js arriving: it defines window.Razorpay, then the
    // browser fires the tag's load event.
    const fake = function Razorpay() {};
    window.Razorpay = fake;
    tags[0].dispatchEvent(new Event('load'));

    await expect(p1).resolves.toBe(fake);
    await expect(p2).resolves.toBe(fake);
  });
});
