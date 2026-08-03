import { describe, it, expect } from 'vitest';

import {
  buildBreadcrumbSchema,
  buildProductSchema,
} from '@/lib/productSchema.js';
import { clampDescription } from '@/lib/pageMeta.js';

// Regression: catalogue seeded at price 0 while pricing is decided. Emitting an
// Offer with price "0" tells Google the product is free.
// Added by /qa follow-up on 2026-08-02.

const UNPRICED = {
  id: 7,
  sku: 'WV-ASHWA-01',
  name: 'Wellvia Ashwa-Ease Gummies',
  price: '0.00',
  stock: 0,
  flavour: 'Mixed Berry',
  short_description: 'KSM-66 Ashwagandha. Calm your mind, naturally.',
  rating_avg: '0.00',
  rating_count: 0,
};

const PRICED = { ...UNPRICED, price: '499.00', stock: 12 };

describe('buildProductSchema', () => {
  it('omits offers entirely when the product has no price', () => {
    const schema = buildProductSchema(UNPRICED, { canonical: 'https://x.test/products/7' });
    expect(schema.offers).toBeUndefined();
    // The rest of the product must still be described.
    expect(schema['@type']).toBe('Product');
    expect(schema.name).toBe('Wellvia Ashwa-Ease Gummies');
    expect(schema.sku).toBe('WV-ASHWA-01');
  });

  it('emits a priced offer once a real price exists', () => {
    const schema = buildProductSchema(PRICED, { canonical: 'https://x.test/products/7' });
    expect(schema.offers.price).toBe('499.00');
    expect(schema.offers.priceCurrency).toBe('INR');
    expect(schema.offers.availability).toBe('https://schema.org/InStock');
  });

  it('reports OutOfStock when priced but stock is zero', () => {
    const schema = buildProductSchema({ ...PRICED, stock: 0 }, {});
    expect(schema.offers.availability).toBe('https://schema.org/OutOfStock');
  });

  it('never emits aggregateRating without real reviews', () => {
    expect(buildProductSchema(UNPRICED, {}).aggregateRating).toBeUndefined();
    const rated = buildProductSchema(
      { ...PRICED, rating_avg: '4.5', rating_count: 12 },
      {},
    );
    expect(rated.aggregateRating.ratingValue).toBe('4.5');
    expect(rated.aggregateRating.reviewCount).toBe(12);
  });

  it('carries the flavour as a structured property when present', () => {
    expect(buildProductSchema(UNPRICED, {}).additionalProperty).toEqual([
      { '@type': 'PropertyValue', name: 'Flavour', value: 'Mixed Berry' },
    ]);
    expect(buildProductSchema({ ...UNPRICED, flavour: null }, {}).additionalProperty)
      .toBeUndefined();
  });

  it('returns null for a missing product rather than throwing', () => {
    expect(buildProductSchema(null, {})).toBeNull();
  });
});

describe('buildBreadcrumbSchema', () => {
  it('numbers positions from 1 and keeps trail order', () => {
    const schema = buildBreadcrumbSchema([
      { name: 'Home', path: '/' },
      { name: 'Shop', path: '/products' },
      { name: 'Sleep', path: '/products/9' },
    ]);
    expect(schema.itemListElement.map((i) => [i.position, i.name])).toEqual([
      [1, 'Home'],
      [2, 'Shop'],
      [3, 'Sleep'],
    ]);
  });

  it('drops entries with no name and returns null when nothing is left', () => {
    expect(buildBreadcrumbSchema([{ path: '/x' }, null])).toBeNull();
    expect(buildBreadcrumbSchema([])).toBeNull();
  });
});

describe('clampDescription', () => {
  it('leaves a short description untouched', () => {
    expect(clampDescription('Short and sweet.')).toBe('Short and sweet.');
  });

  it('collapses whitespace so snippets do not carry markup indentation', () => {
    expect(clampDescription('a\n\n  b   c')).toBe('a b c');
  });

  it('cuts on a word boundary and appends an ellipsis', () => {
    const out = clampDescription(`${'word '.repeat(60)}`, 40);
    expect(out.length).toBeLessThanOrEqual(40);
    expect(out.endsWith('…')).toBe(true);
    expect(out).not.toMatch(/wo…$/);
  });

  it('returns an empty string for missing input', () => {
    expect(clampDescription(undefined)).toBe('');
    expect(clampDescription(null)).toBe('');
  });
});
