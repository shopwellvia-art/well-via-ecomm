import { describe, it, expect } from 'vitest';
import { matchesQuery } from '@/pages/ProductListPage.jsx';

/**
 * `matchesQuery` is the client-side twin of the server's free-text match. It
 * only runs in the bestsellers / new-arrivals views, which fetch a plain list
 * and filter locally — but it must agree with what the API considers a match,
 * or those views drop hits the backend just returned.
 */
const product = {
  name: 'Beauty Sleep Gummies',
  sku: 'WV-SLEEP-30',
  brand: 'Wellvia',
  flavour: 'Mixed Berry',
  badge: 'Bestseller',
  short_description: 'Melatonin-free rest support',
  description: 'Formulated with chamomile to help you wind down at night.',
  stock: 12,
};

describe('matchesQuery', () => {
  it('matches on name', () => {
    expect(matchesQuery(product, 'beauty')).toBe(true);
  });

  it.each([
    ['sku', 'wv-sleep'],
    ['brand', 'wellvia'],
    ['flavour', 'berry'],
    ['badge', 'bestseller'],
    ['short_description', 'melatonin'],
    ['description', 'chamomile'],
  ])('matches on %s ("%s") — the name-only check used to drop these', (_field, term) => {
    expect(matchesQuery(product, term)).toBe(true);
  });

  it('is case-insensitive and ignores surrounding whitespace', () => {
    expect(matchesQuery(product, '  CHAMOMILE  ')).toBe(true);
  });

  it('rejects a term that appears nowhere', () => {
    expect(matchesQuery(product, 'ashwagandha')).toBe(false);
  });

  it('treats an empty query as "no filter" rather than "no matches"', () => {
    expect(matchesQuery(product, '')).toBe(true);
    expect(matchesQuery(product, '   ')).toBe(true);
  });

  // Every one of these fields is nullable on the product payload.
  it('survives null/absent fields instead of throwing', () => {
    const sparse = { name: 'Plain', sku: 'X1', brand: null, description: undefined };
    expect(matchesQuery(sparse, 'plain')).toBe(true);
    expect(matchesQuery(sparse, 'berry')).toBe(false);
  });

  // Numeric-looking values must not be coerced — `stock: 12` matching "12"
  // would make a search for a quantity return unrelated products.
  it('only searches string fields', () => {
    expect(matchesQuery(product, '12')).toBe(false);
  });
});
