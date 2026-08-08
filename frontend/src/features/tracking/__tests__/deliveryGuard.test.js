/**
 * The client half of the GA4 purchase-delivery guard.
 *
 * The backend refuses to SAVE the undeliverable combination (server-mode
 * delivery with no Measurement Protocol API secret); this module is what lets
 * the settings form refuse it INLINE, before the request is made, and banner a
 * deployment whose stored rows are already broken. Being pure, the rules are
 * asserted here without a DOM: the inputs are the API's `has_value` flag and
 * the form's draft values — never the secret itself, which the browser never
 * receives and never needs.
 */
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_DELIVERY_MODE,
  DELIVERY_GUARD_ERROR_CODE,
  DELIVERY_GUARD_FIXES,
  REDACTED,
  SERVER_DELIVERY_MODES,
  deliverySelectionError,
  requiresApiSecret,
  secretAfterSave,
  storedStateUndeliverable,
} from '@/features/tracking/deliveryGuard.js';

describe('requiresApiSecret', () => {
  it('needs the secret for both server-involving modes', () => {
    // Established from the backend: the outbox is the only sender in `server`
    // mode and one of the two senders in `both`; either way `outbox.drain`
    // idles with reason no_api_secret when the secret is missing.
    expect(requiresApiSecret('server')).toBe(true);
    expect(requiresApiSecret('both')).toBe(true);
    expect(SERVER_DELIVERY_MODES).toEqual(['server', 'both']);
  });

  it('never needs it for browser-only', () => {
    expect(requiresApiSecret('browser')).toBe(false);
  });

  it('treats empty/unset as the backend default — server — not as a loophole', () => {
    expect(DEFAULT_DELIVERY_MODE).toBe('server');
    expect(requiresApiSecret('')).toBe(true);
    expect(requiresApiSecret('   ')).toBe(true);
    expect(requiresApiSecret(undefined)).toBe(true);
    expect(requiresApiSecret(null)).toBe(true);
  });
});

describe('secretAfterSave', () => {
  it('an untouched draft keeps whatever is stored', () => {
    expect(secretAfterSave({ hasStoredSecret: true, secretDraft: undefined })).toBe(true);
    expect(secretAfterSave({ hasStoredSecret: false, secretDraft: undefined })).toBe(false);
  });

  it('the mask means "unchanged", exactly as the API treats it', () => {
    expect(secretAfterSave({ hasStoredSecret: true, secretDraft: REDACTED })).toBe(true);
    expect(secretAfterSave({ hasStoredSecret: false, secretDraft: REDACTED })).toBe(false);
  });

  it('a typed value counts as arriving with this save', () => {
    expect(secretAfterSave({ hasStoredSecret: false, secretDraft: 'AbCdEfGh12345678' })).toBe(
      true,
    );
  });

  it('an explicit empty is a clear, whatever was stored', () => {
    expect(secretAfterSave({ hasStoredSecret: true, secretDraft: '' })).toBe(false);
    expect(secretAfterSave({ hasStoredSecret: true, secretDraft: '   ' })).toBe(false);
  });
});

describe('deliverySelectionError — the inline mirror of the 422', () => {
  it('flags a server mode with no secret on file and none typed', () => {
    const error = deliverySelectionError({
      deliveryDraft: 'server',
      hasStoredSecret: false,
      secretDraft: undefined,
    });
    expect(error).toMatch(/Measurement Protocol API secret/);
    expect(error).toMatch(/browser-only/);
  });

  it('flags clearing the secret while a server mode is selected — the mirrored direction', () => {
    const error = deliverySelectionError({
      deliveryDraft: 'both',
      hasStoredSecret: true,
      secretDraft: '',
    });
    expect(error).toMatch(/clears the Measurement Protocol API secret/);
  });

  it('clears the moment either fix is applied', () => {
    // Fix one: the secret arrives with the same save — no forced ordering.
    expect(
      deliverySelectionError({
        deliveryDraft: 'server',
        hasStoredSecret: false,
        secretDraft: 'AbCdEfGh12345678',
      }),
    ).toBeNull();
    // Fix two: browser-only never needs the secret.
    expect(
      deliverySelectionError({
        deliveryDraft: 'browser',
        hasStoredSecret: false,
        secretDraft: undefined,
      }),
    ).toBeNull();
  });

  it('is satisfied by a stored secret riding along as the mask', () => {
    expect(
      deliverySelectionError({
        deliveryDraft: 'server',
        hasStoredSecret: true,
        secretDraft: REDACTED,
      }),
    ).toBeNull();
  });
});

describe('storedStateUndeliverable — the banner condition', () => {
  it('fires for the production defect: GA4 on, server delivery, no secret', () => {
    expect(
      storedStateUndeliverable({ enabled: 'true', delivery: 'server', hasStoredSecret: false }),
    ).toBe(true);
    expect(
      storedStateUndeliverable({ enabled: 'true', delivery: 'both', hasStoredSecret: false }),
    ).toBe(true);
  });

  it('stays quiet when the state is deliverable', () => {
    expect(
      storedStateUndeliverable({ enabled: 'true', delivery: 'server', hasStoredSecret: true }),
    ).toBe(false);
    expect(
      storedStateUndeliverable({ enabled: 'true', delivery: 'browser', hasStoredSecret: false }),
    ).toBe(false);
  });

  it('stays quiet while GA4 is off — nothing is sent from anywhere', () => {
    expect(
      storedStateUndeliverable({ enabled: 'false', delivery: 'server', hasStoredSecret: false }),
    ).toBe(false);
    expect(
      storedStateUndeliverable({ enabled: '', delivery: 'server', hasStoredSecret: false }),
    ).toBe(false);
  });
});

describe('cross-surface contract', () => {
  it('shares the backend error/warning code so one grep finds every surface', () => {
    expect(DELIVERY_GUARD_ERROR_CODE).toBe('ga4_server_delivery_without_secret');
  });

  it('always offers exactly the two documented ways out', () => {
    expect(DELIVERY_GUARD_FIXES).toHaveLength(2);
    expect(DELIVERY_GUARD_FIXES[0]).toMatch(/Measurement Protocol API secrets/);
    expect(DELIVERY_GUARD_FIXES[1]).toMatch(/browser-only/);
  });
});
