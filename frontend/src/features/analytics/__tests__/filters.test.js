import { describe, it, expect } from 'vitest';
import {
  DEFAULT_FILTERS,
  FILTER_KEYS,
  MAX_LIMIT,
  activeFilterCount,
  filterKey,
  normaliseFilters,
  parseFilters,
  supportedFilterKeys,
  toQueryString,
  toSearchParams,
} from '@/features/analytics/filters.js';
import { getView } from '@/features/analytics/registry.js';

// Real registry entries, so the capability gating is tested against what the
// backend actually declares rather than against a hand-written fixture.
const courierView = getView('orders', 'courier-performance'); // declares `courier`
const productView = getView('products', 'product-performance'); // does not
const executiveView = getView('executive', 'executive-overview');

describe('round-trip stability', () => {
  const shapes = [
    {},
    { period: '7d' },
    { period: '90d', granularity: 'week' },
    { period: 'mtd', comparison: 'previous_year' },
    { period: 'qtd', comparison: 'none' },
    { period: 'ytd', granularity: 'month' },
    { period: '7d', granularity: 'hour' },
    { period: 'custom', date_from: '2026-01-01', date_to: '2026-01-31' },
    { period: 'custom', date_from: '2026-02-01', date_to: '2026-02-08', granularity: 'hour' },
    { limit: 50, dimension: 'courier' },
    { limit: 1, dimension: 'payment_method', comparison: 'none' },
    { period: '30d', category_id: 12, product_id: 4, customer_segment: 'vip' },
    { country: 'IN', state: 'Karnataka', city: 'Bengaluru' },
    { source: 'google', medium: 'cpc', campaign: 'diwali-2026', device: 'mobile' },
    { payment_method: 'upi', payment_gateway: 'razorpay', courier: 'bluedart' },
    { order_status: 'delivered', coupon: 'WELCOME10', new_or_returning: 'returning' },
  ];

  it.each(shapes)('parse(serialize(x)) === x for %j', (shape) => {
    const canonical = normaliseFilters(shape);
    expect(parseFilters(toSearchParams(canonical))).toEqual(canonical);
  });

  it('is idempotent through the query string as well', () => {
    for (const shape of shapes) {
      const canonical = normaliseFilters(shape);
      const once = toQueryString(canonical);
      expect(toQueryString(parseFilters(once))).toBe(once);
    }
  });

  it('always returns every key, so callers never need a fallback', () => {
    expect(Object.keys(parseFilters(new URLSearchParams())).sort()).toEqual([...FILTER_KEYS].sort());
  });
});

describe('defaults stay out of the URL', () => {
  it('serialises a clean view to an empty query string', () => {
    expect(toQueryString(DEFAULT_FILTERS)).toBe('');
    expect(toQueryString({})).toBe('');
    expect(toQueryString({ period: '30d', comparison: 'previous_period', granularity: 'day', limit: 20 })).toBe('');
    expect(activeFilterCount({})).toBe(0);
  });

  it('writes only what actually changed', () => {
    expect(toQueryString({ period: '7d' })).toBe('period=7d');
    expect(activeFilterCount({ period: '7d', limit: 50 })).toBe(2);
  });

  it('gives the same cache key to two spellings of the same filters', () => {
    expect(filterKey({ period: '30d', limit: 20, comparison: 'previous_period' })).toBe(filterKey({}));
    expect(filterKey({ period: '7d' })).not.toBe(filterKey({}));
  });
});

describe('a hand-edited URL degrades instead of throwing', () => {
  it('falls back to the default for every garbage scalar', () => {
    const params = new URLSearchParams({
      period: 'last-tuesday',
      comparison: 'vibes',
      granularity: 'fortnight',
      limit: '99999',
      dimension: '(SELECT 1)',
      product_id: '-4',
      country: 'India',
      new_or_returning: 'maybe',
    });
    expect(() => parseFilters(params)).not.toThrow();
    expect(parseFilters(params)).toEqual(DEFAULT_FILTERS);
  });

  it('rejects impossible and inverted windows rather than sending them', () => {
    const bad = (q) => parseFilters(new URLSearchParams(q));
    expect(bad('period=custom&date_from=2026-02-30&date_to=2026-03-05').period).toBe('30d');
    expect(bad('period=custom&date_from=2026-03-05&date_to=2026-03-01').period).toBe('30d');
    expect(bad('period=custom&date_from=2026-01-01').period).toBe('30d');
    // Over the backend's 400-day cap, which errors rather than truncating.
    expect(bad('period=custom&date_from=2020-01-01&date_to=2026-01-01').period).toBe('30d');
  });

  it('drops explicit dates that a preset period makes meaningless', () => {
    const f = parseFilters(new URLSearchParams('period=30d&date_from=2026-01-01&date_to=2026-01-31'));
    expect(f).toMatchObject({ period: '30d', date_from: null, date_to: null });
  });

  it('downgrades hourly granularity that would ask for thousands of buckets', () => {
    expect(parseFilters(new URLSearchParams('period=90d&granularity=hour')).granularity).toBe('day');
    expect(
      parseFilters(new URLSearchParams('period=custom&date_from=2026-01-01&date_to=2026-03-01&granularity=hour'))
        .granularity,
    ).toBe('day');
    // Inside the 14-day cap it survives.
    expect(parseFilters(new URLSearchParams('period=7d&granularity=hour')).granularity).toBe('hour');
  });

  it('clamps limit to the backend bound', () => {
    expect(parseFilters(new URLSearchParams(`limit=${MAX_LIMIT}`)).limit).toBe(MAX_LIMIT);
    expect(parseFilters(new URLSearchParams(`limit=${MAX_LIMIT + 1}`)).limit).toBe(DEFAULT_FILTERS.limit);
    expect(parseFilters(new URLSearchParams('limit=0')).limit).toBe(DEFAULT_FILTERS.limit);
  });

  it('accepts a query string or a plain object, not just URLSearchParams', () => {
    expect(parseFilters('period=7d').period).toBe('7d');
    expect(parseFilters({ period: '7d' }).period).toBe('7d');
  });
});

describe('view capability gating', () => {
  it('keeps a filter the view declares', () => {
    expect(courierView.filters).toContain('courier');
    expect(parseFilters(new URLSearchParams('courier=bluedart'), courierView).courier).toBe('bluedart');
    expect(toQueryString({ courier: 'bluedart' }, courierView)).toBe('courier=bluedart');
  });

  // Navigating Courier Performance -> Product Performance must not carry
  // `courier` along: the backend would ignore it, and a shared link showing an
  // unhonoured filter chip is a number somebody misreads.
  it('drops a filter carried over from another view', () => {
    expect(productView.filters).not.toContain('courier');
    expect(parseFilters(new URLSearchParams('courier=bluedart'), productView).courier).toBeNull();
    expect(toQueryString({ courier: 'bluedart' }, productView)).toBe('');
    expect(supportedFilterKeys(productView)).not.toContain('courier');
  });

  it('lists only the keys a view honours', () => {
    const keys = supportedFilterKeys(executiveView);
    expect(keys).toEqual(expect.arrayContaining(['period', 'comparison', 'granularity', 'limit']));
    expect(keys).not.toContain('category_id');
    expect(supportedFilterKeys(null)).toEqual([...FILTER_KEYS]); // no view -> everything
  });

  it('maps the view tokens onto the backend field names', () => {
    expect(productView.filters).toContain('category');
    expect(parseFilters(new URLSearchParams('category_id=7'), productView).category_id).toBe(7);
    expect(parseFilters(new URLSearchParams('category_id=7'), executiveView).category_id).toBeNull();
  });

  it('round-trips within a view too', () => {
    const canonical = normaliseFilters({ period: '7d', courier: 'bluedart', product_id: 3 }, courierView);
    expect(parseFilters(toSearchParams(canonical, courierView), courierView)).toEqual(canonical);
    expect(canonical.product_id).toBeNull(); // courier view does not offer `product`
  });

  it('accepts a bare token array as the view', () => {
    expect(parseFilters(new URLSearchParams('courier=x'), ['courier']).courier).toBe('x');
    expect(parseFilters(new URLSearchParams('courier=x'), []).courier).toBeNull();
  });
});
