import { describe, it, expect } from 'vitest';
import { sanitizeNext } from '@/pages/LoginPage.jsx';

describe('sanitizeNext (open-redirect guard on ?next=)', () => {
  it('honours internal paths', () => {
    expect(sanitizeNext('/checkout')).toBe('/checkout');
    expect(sanitizeNext('/account/orders?page=2')).toBe('/account/orders?page=2');
    expect(sanitizeNext('/')).toBe('/');
  });

  it('rejects protocol-relative external URLs (//host)', () => {
    expect(sanitizeNext('//evil.example.com')).toBe(null);
  });

  it('rejects the backslash variant browsers normalise to //host', () => {
    expect(sanitizeNext('/\\evil.example.com')).toBe(null);
  });

  it('rejects absolute and scheme-carrying URLs', () => {
    expect(sanitizeNext('https://evil.example.com/phish')).toBe(null);
    expect(sanitizeNext('javascript:alert(1)')).toBe(null);
  });

  it('rejects empty / missing / non-rooted values', () => {
    expect(sanitizeNext('')).toBe(null);
    expect(sanitizeNext(null)).toBe(null);
    expect(sanitizeNext(undefined)).toBe(null);
    expect(sanitizeNext('checkout')).toBe(null);
  });
});
