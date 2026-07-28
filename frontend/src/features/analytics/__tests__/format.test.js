import { describe, it, expect } from 'vitest';
import { formatPrice } from '@/lib/utils.js';
import {
  FORMAT_IDS,
  MISSING_DISPLAY,
  formatValue,
  getStoreCurrency,
  isMissingDisplay,
  setStoreCurrency,
} from '@/features/analytics/format.js';

// en-IN/INR emits a non-breaking space in some ICU builds — normalise so the
// assertions do not depend on the node build's whitespace choice.
const f = (...args) => formatValue(...args).replace(/\u00a0/g, ' ').trim();

describe('the missing/zero distinction', () => {
  // The anti-fabrication test. The backend sends `null` when a number could not
  // be computed and `0` when it computed to zero; if both render the same, the
  // whole analytics contract is decorative.
  it('formats null money as an em-dash and zero money as a real amount', () => {
    expect(f(null, 'money')).toBe(MISSING_DISPLAY);
    expect(f(0, 'money')).toBe('₹0.00');
    expect(f(null, 'money')).not.toBe(f(0, 'money'));
  });

  it('never launders a missing value into a number, for any format', () => {
    for (const id of FORMAT_IDS) {
      expect(formatValue(null, id), id).toBe(MISSING_DISPLAY);
      expect(formatValue(undefined, id), id).toBe(MISSING_DISPLAY);
      expect(formatValue(null, id), id).not.toBe(formatValue(0, id));
    }
  });

  // `formatPrice` deliberately falls back to zero — right for a product price,
  // which always exists. Analytics must not inherit that.
  it('diverges from formatPrice, which does coerce garbage to zero', () => {
    expect(formatPrice('not-a-number').replace(/\u00a0/g, ' ').trim()).toBe('₹0.00');
    expect(f('not-a-number', 'money')).toBe(MISSING_DISPLAY);
    expect(f(Number.NaN, 'money')).toBe(MISSING_DISPLAY);
    expect(f(Infinity, 'money')).toBe(MISSING_DISPLAY);
  });

  it('exposes the sentinel so callers can branch on it', () => {
    expect(MISSING_DISPLAY).toBe('—');
    expect(isMissingDisplay(formatValue(null, 'int'))).toBe(true);
    expect(isMissingDisplay(formatValue(0, 'int'))).toBe(false);
  });
});

describe('formatValue', () => {
  it('handles every FormatId without throwing, on plausible and hostile input', () => {
    const samples = [0, 1, -1, 1234.567, '249.00', '', null, undefined, Number.NaN, Infinity, {}, []];
    for (const id of [...FORMAT_IDS, 'not-a-format']) {
      for (const value of samples) {
        expect(() => formatValue(value, id), `${id}:${String(value)}`).not.toThrow();
        expect(typeof formatValue(value, id)).toBe('string');
      }
    }
  });

  it('groups integers the Indian way and never shows decimals', () => {
    expect(f(1234567, 'int')).toBe('12,34,567');
    expect(f(1234.6, 'int')).toBe('1,235');
  });

  // Every `pct` KPI formula in kpis.py already ends in `* 100`.
  it('does not rescale percentages', () => {
    expect(f(12.5, 'pct')).toBe('12.5%');
    expect(f(0, 'pct')).toBe('0%');
    expect(f(100, 'pct')).toBe('100%');
  });

  it('pluralises durations', () => {
    expect(f(1, 'days')).toBe('1 day');
    expect(f(2.5, 'days')).toBe('2.5 days');
    expect(f(1, 'hours')).toBe('1 hr');
    expect(f(0, 'hours')).toBe('0 hrs');
  });

  it('renders ratios as multipliers', () => {
    expect(f(2.4, 'ratio')).toBe('2.4×');
    expect(f(3, 'ratio')).toBe('3.0×');
  });

  it('compacts with Indian magnitudes', () => {
    expect(f(999, 'compact')).toBe('999');
    expect(f(1500, 'compact')).toContain('K');
    expect(f(1234567, 'compact')).toContain('L'); // lakh, not million
  });

  it('treats blank text as missing but keeps a literal zero', () => {
    expect(f('  ', 'text')).toBe(MISSING_DISPLAY);
    expect(f('UPI', 'text')).toBe('UPI');
    expect(f(0, 'text')).toBe('0');
  });

  it('falls back to text for a format id it has never heard of', () => {
    expect(f('bluedart', 'some_future_format')).toBe('bluedart');
  });

  it('accepts numeric strings, because money arrives as a Decimal string', () => {
    expect(f('249.00', 'money')).toBe('₹249.00');
    expect(f('1234.5', 'int')).toBe('1,235');
  });
});

describe('store currency', () => {
  it('defaults to INR and can be reset once at boot', () => {
    expect(getStoreCurrency()).toBe('INR');
    setStoreCurrency('usd');
    expect(getStoreCurrency()).toBe('USD');
    expect(f(0, 'money')).toContain('$');
    setStoreCurrency(null); // garbage falls back rather than breaking money
    expect(getStoreCurrency()).toBe('INR');
    expect(f(0, 'money')).toBe('₹0.00');
  });
});
