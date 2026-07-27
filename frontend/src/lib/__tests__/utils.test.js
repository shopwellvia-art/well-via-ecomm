import { describe, it, expect } from 'vitest';
import { cn, formatPrice, mediaUrl, stockLabel } from '@/lib/utils.js';

// Intl for en-IN/INR emits a non-breaking space in some ICU builds — normalise
// so the assertions don't depend on the node build's ICU whitespace choice.
const fmt = (...args) => formatPrice(...args).replace(/\u00a0/g, ' ').trim();

describe('formatPrice', () => {
  it('formats INR with Indian digit grouping', () => {
    expect(fmt(1234.5)).toBe('₹1,234.50');
    expect(fmt(123456.789)).toBe('₹1,23,456.79'); // lakh grouping + rounding
  });

  it('accepts numeric strings (API sends decimals as strings)', () => {
    expect(fmt('249.00')).toBe('₹249.00');
  });

  it('falls back to zero for non-finite input instead of NaN-ing the UI', () => {
    expect(fmt('not-a-price')).toBe('₹0.00');
    expect(fmt(undefined)).toBe('₹0.00');
    expect(fmt(Infinity)).toBe('₹0.00');
  });
});

describe('mediaUrl', () => {
  it('rewrites absolute backend /media URLs to same-origin relative paths', () => {
    expect(mediaUrl('http://localhost:8000/media/products/a.png')).toBe(
      '/media/products/a.png',
    );
  });

  it('keeps the query string when rewriting', () => {
    expect(mediaUrl('https://api.example.com/media/a/b.jpg?v=2')).toBe(
      '/media/a/b.jpg?v=2',
    );
  });

  it('leaves external (non-/media) absolute URLs untouched', () => {
    expect(mediaUrl('https://cdn.example.com/assets/x.png')).toBe(
      'https://cdn.example.com/assets/x.png',
    );
  });

  it('passes through already-relative paths', () => {
    expect(mediaUrl('/media/x.png')).toBe('/media/x.png');
  });

  it('passes through unparseable or non-string values unchanged', () => {
    expect(mediaUrl('not a url')).toBe('not a url');
    expect(mediaUrl(null)).toBe(null);
    expect(mediaUrl(undefined)).toBe(undefined);
  });
});

describe('stockLabel', () => {
  it('is out-of-stock at zero or below', () => {
    expect(stockLabel(0)).toEqual({ text: 'Out of stock', tone: 'danger' });
    expect(stockLabel(-1).tone).toBe('danger');
  });

  it('warns with the exact remaining count at 5 or fewer', () => {
    expect(stockLabel(3)).toEqual({ text: 'Only 3 left', tone: 'warning' });
    expect(stockLabel(5).tone).toBe('warning');
  });

  it('is a plain in-stock label above the low-stock threshold', () => {
    expect(stockLabel(6)).toEqual({ text: 'In stock', tone: 'success' });
  });
});

describe('cn', () => {
  it('merges conditional classes and resolves Tailwind conflicts', () => {
    expect(cn('p-2', 'p-4')).toBe('p-4'); // later utility wins
    expect(cn('a', undefined, null, ['c', { d: true, e: false }])).toBe('a c d');
  });
});
