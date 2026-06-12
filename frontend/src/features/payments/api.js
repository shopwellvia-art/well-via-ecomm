import { apiClient } from '@/services/apiClient.js';

export const paymentsApi = {
  // Create order + initiate payment in one round-trip. Server returns the
  // redirect URL we then send the browser to.
  //
  // Address routing (mutually exclusive — first match wins):
  //   address_id               → use a saved address by FK
  //   address + save_address   → inline one-off (optionally persisted)
  //   shipping_address (legacy)→ free-text; accepted this release for stale
  //                              SPA bundles; remove next release.
  //
  // Pre-existing bug fixed: the old destructuring silently dropped
  // `payment_instrument` and `customer_phone` — COD-OTP phone and
  // instrument discounts never reached the server. Now passed through.
  checkout: ({
    items,
    currency,
    payment_method,
    payment_instrument,
    customer_phone,
    coupon_code,
    // New structured address fields
    address_id,
    address,
    save_address,
    // Billing address — absent means backend copies shipping (default)
    billing_address_id,
    billing_address,
    // Legacy free-text fields — kept for backward compat (stale bundles)
    shipping_address,
    shipping_pincode,
  }) =>
    apiClient
      .post('/checkout', {
        items,
        // Only include when set — the backend's pydantic schema treats null
        // and absent the same, but cleaner request payloads ease debugging.
        ...(currency ? { currency } : {}),
        ...(payment_method ? { payment_method } : {}),
        ...(payment_instrument ? { payment_instrument } : {}),
        ...(customer_phone ? { customer_phone } : {}),
        ...(coupon_code ? { coupon_code } : {}),
        // Structured address: address_id takes precedence over inline address
        ...(address_id != null ? { address_id } : {}),
        ...(address_id == null && address ? { address } : {}),
        ...(address_id == null && address && save_address != null
          ? { save_address }
          : {}),
        // Billing address — only include when explicitly provided; absence
        // signals "billing = shipping" to the backend.
        ...(billing_address_id != null ? { billing_address_id } : {}),
        ...(billing_address_id == null && billing_address ? { billing_address } : {}),
        // Legacy passthrough — never sent by the new CheckoutPage path
        ...(address_id == null && !address && shipping_address
          ? { shipping_address }
          : {}),
        ...(address_id == null && !address && shipping_pincode
          ? { shipping_pincode }
          : {}),
      })
      .then((r) => r.data),

  // Polled by the return page; backend will fetch_status from the provider
  // if the order is still PENDING.
  status: (mtid) =>
    apiClient.get(`/payments/${mtid}/status`).then((r) => r.data),

  orderForPayment: (mtid) =>
    apiClient.get(`/payments/${mtid}/order`).then((r) => r.data),

  // Mock simulator only — equivalent to PhonePe's signed webhook.
  mockDecision: (mtid, action) =>
    apiClient
      .post('/payments/webhook/mock', {
        merchant_transaction_id: mtid,
        action,
      })
      .then((r) => r.data),
};
