import { describe, it, expect } from 'vitest';
import {
  validateAnalyticsFields,
  analyticsPayload,
} from '@/pages/admin/AdminProductFormPage.jsx';

/**
 * The four analytics/compliance fields on the admin product form.
 *
 * The one bug worth a whole test file: a blank box must serialise to `null`,
 * never `0` and never `''`. `reorder_point: 0` is a real answer ("reorder only
 * when the shelf is empty") and the inventory rollup's `reorder_gap` column is
 * built on telling it apart from "nobody configured one". Turning an untouched
 * input into 0 would report a confident wrong number rather than an honest gap.
 *
 * These mirror app/schemas/product.py — the server is authoritative, so any
 * rule that loosens here without loosening there just moves the 422 later.
 */

const blank = {
  brand: '',
  hsn_code: '',
  reorder_point: '',
  shelf_life_days: '',
};

describe('analyticsPayload — blank must serialise to null', () => {
  it('maps every empty field to null, not 0 and not empty string', () => {
    expect(analyticsPayload(blank)).toEqual({
      brand: null,
      hsn_code: null,
      reorder_point: null,
      shelf_life_days: null,
    });
  });

  it('treats a whitespace-only field as blank, not as zero', () => {
    // Number('  ') === 0, which is exactly the trap this guards.
    const payload = analyticsPayload({
      brand: '   ',
      hsn_code: '  ',
      reorder_point: '   ',
      shelf_life_days: '\t',
    });
    expect(payload.reorder_point).toBeNull();
    expect(payload.shelf_life_days).toBeNull();
    expect(payload.brand).toBeNull();
    expect(payload.hsn_code).toBeNull();
  });

  it('keeps an explicit reorder point of 0 as 0', () => {
    expect(analyticsPayload({ ...blank, reorder_point: '0' }).reorder_point).toBe(0);
    // The distinction the whole feature rests on.
    expect(analyticsPayload({ ...blank, reorder_point: '' }).reorder_point).toBeNull();
  });

  it('sends numbers as numbers and strings trimmed', () => {
    expect(
      analyticsPayload({
        brand: '  Wellvia  ',
        hsn_code: ' 21069099 ',
        reorder_point: '25',
        shelf_life_days: '540',
      }),
    ).toEqual({
      brand: 'Wellvia',
      hsn_code: '21069099',
      reorder_point: 25,
      shelf_life_days: 540,
    });
  });

  it('is total — a missing or undefined field is null, never NaN', () => {
    expect(analyticsPayload({})).toEqual({
      brand: null,
      hsn_code: null,
      reorder_point: null,
      shelf_life_days: null,
    });
    expect(analyticsPayload(undefined)).toEqual({
      brand: null,
      hsn_code: null,
      reorder_point: null,
      shelf_life_days: null,
    });
  });
});

describe('validateAnalyticsFields — HSN', () => {
  it('accepts 4, 6 and 8 digits', () => {
    for (const code of ['0904', '090411', '09041110', '2106', '21069099']) {
      expect(validateAnalyticsFields({ ...blank, hsn_code: code })).toEqual({});
    }
  });

  it('accepts a blank code — HSN is optional, not mandatory', () => {
    expect(validateAnalyticsFields({ ...blank, hsn_code: '' })).toEqual({});
    expect(validateAnalyticsFields({ ...blank, hsn_code: '   ' })).toEqual({});
  });

  it('rejects illegal lengths rather than padding them', () => {
    for (const code of ['904', '09041', '0904111', '090411101']) {
      expect(validateAnalyticsFields({ ...blank, hsn_code: code }).hsn_code).toMatch(
        /4, 6 or 8 digits/,
      );
    }
  });

  it('rejects anything that is not an ASCII digit', () => {
    for (const code of ['12A4', '09-04', '0904 1110', '٠٩٠٤', '²²²²', '+904']) {
      expect(
        validateAnalyticsFields({ ...blank, hsn_code: code }).hsn_code,
      ).toBeTruthy();
    }
  });

  it('ignores surrounding whitespace, matching the server trim', () => {
    expect(validateAnalyticsFields({ ...blank, hsn_code: '  0904  ' })).toEqual({});
  });
});

describe('validateAnalyticsFields — reorder point', () => {
  it('accepts 0 and positive whole numbers', () => {
    for (const value of ['0', '1', '25', '9999']) {
      expect(validateAnalyticsFields({ ...blank, reorder_point: value })).toEqual({});
    }
  });

  it('accepts blank — no reorder point configured is a valid state', () => {
    expect(validateAnalyticsFields(blank)).toEqual({});
  });

  it('rejects negatives and non-integers', () => {
    for (const value of ['-1', '2.5', 'abc']) {
      expect(
        validateAnalyticsFields({ ...blank, reorder_point: value }).reorder_point,
      ).toBeTruthy();
    }
  });
});

describe('validateAnalyticsFields — shelf life', () => {
  it('accepts positive whole numbers', () => {
    for (const value of ['1', '90', '730']) {
      expect(validateAnalyticsFields({ ...blank, shelf_life_days: value })).toEqual({});
    }
  });

  it('rejects 0 and negatives — a zero-day shelf life is a typo, not a product', () => {
    for (const value of ['0', '-1', '-365']) {
      expect(
        validateAnalyticsFields({ ...blank, shelf_life_days: value }).shelf_life_days,
      ).toBeTruthy();
    }
  });

  it('accepts blank — most products do not track shelf life', () => {
    expect(validateAnalyticsFields({ ...blank, shelf_life_days: '' })).toEqual({});
  });
});

describe('validateAnalyticsFields — brand', () => {
  it('accepts free text up to the 120-char snapshot width', () => {
    expect(validateAnalyticsFields({ ...blank, brand: 'Wellvia' })).toEqual({});
    expect(validateAnalyticsFields({ ...blank, brand: 'b'.repeat(120) })).toEqual({});
  });

  it('rejects a brand longer than the analytics snapshot column', () => {
    expect(
      validateAnalyticsFields({ ...blank, brand: 'b'.repeat(121) }).brand,
    ).toBeTruthy();
  });

  it('measures length after trimming, so padding is not an error', () => {
    expect(
      validateAnalyticsFields({ ...blank, brand: `  ${'b'.repeat(120)}  ` }),
    ).toEqual({});
  });
});

describe('validateAnalyticsFields — reporting', () => {
  it('reports every bad field at once, not just the first', () => {
    const errors = validateAnalyticsFields({
      brand: 'b'.repeat(200),
      hsn_code: '12345',
      reorder_point: '-3',
      shelf_life_days: '0',
    });
    expect(Object.keys(errors).sort()).toEqual([
      'brand',
      'hsn_code',
      'reorder_point',
      'shelf_life_days',
    ]);
  });

  it('returns an empty object when everything is valid', () => {
    expect(
      validateAnalyticsFields({
        brand: 'Wellvia',
        hsn_code: '21069099',
        reorder_point: '0',
        shelf_life_days: '540',
      }),
    ).toEqual({});
  });
});
