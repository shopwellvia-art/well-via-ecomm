import { describe, it, expect } from 'vitest';
import { SCORE, searchViews } from '@/features/analytics/search.js';
import { allViews } from '@/features/analytics/registry.js';

const slugs = (results) => results.map((r) => r.view.slug);

describe('searchViews', () => {
  it('puts the Cohort view first for "cohort"', () => {
    const results = searchViews('cohort');
    expect(results[0].view.slug).toBe('cohort-and-retention');
    expect(results[0].score).toBeGreaterThanOrEqual(SCORE.NAME_WORD_PREFIX);
  });

  it('returns nothing for a query that matches nothing', () => {
    expect(searchViews('zzzqqqx')).toEqual([]);
    expect(searchViews('')).toEqual([]);
    expect(searchViews('   ')).toEqual([]);
    expect(searchViews(null)).toEqual([]);
  });

  it('ranks exact above prefix above substring above keyword', () => {
    // "COD Performance" is an exact name match; nothing else may outrank it.
    expect(searchViews('cod performance')[0].view.slug).toBe('cod-performance');
    // Prefix on the name.
    expect(searchViews('returns')[0].view.slug).toBe('returns-and-refunds');
    // Keyword only — "diwali" appears in no view name, just in keywords.
    const diwali = searchViews('diwali');
    expect(slugs(diwali)).toContain('seasonal-and-festival-sales');
    expect(diwali[0].matchedOn).toContain('keyword');
  });

  it('matches on the owning module name too', () => {
    const results = searchViews('marketplace');
    expect(slugs(results)).toContain('marketplace-performance');
    expect(results.every((r) => r.score > 0)).toBe(true);
  });

  it('ANDs the tokens of a multi-word query', () => {
    const results = searchViews('payment failure');
    expect(results[0].view.slug).toBe('payment-failure');
    // "cohort" and "courier" never co-occur, so requiring both finds nothing.
    expect(searchViews('cohort courier')).toEqual([]);
  });

  it('is case- and punctuation-tolerant', () => {
    expect(searchViews('RFM')[0].view.slug).toBe('rfm-customer-analysis');
    expect(searchViews('cod-performance')[0].view.slug).toBe('cod-performance');
    expect(searchViews('Average Order Value')[0].view.slug).toBe('orders-and-average-order-value');
  });

  it('honours the limit and defaults to a menu-sized page', () => {
    expect(searchViews('a').length).toBeLessThanOrEqual(10);
    expect(searchViews('a', { limit: 3 })).toHaveLength(3);
    expect(searchViews('sales', { limit: 100 }).length).toBeLessThanOrEqual(allViews().length);
  });

  it('breaks ties by registry order, so results do not shuffle between keystrokes', () => {
    const results = searchViews('sales', { limit: 100 });
    for (let i = 1; i < results.length; i += 1) {
      const [prev, curr] = [results[i - 1], results[i]];
      expect(prev.score >= curr.score).toBe(true);
      if (prev.score === curr.score) expect(prev.view.number).toBeLessThan(curr.view.number);
    }
  });

  it('returns the merged registry view, ready to navigate to', () => {
    const [top] = searchViews('cohort');
    expect(top.view.to).toBe('/admin/analytics/customers/cohort-and-retention');
    expect(top.view.state).toBeTruthy();
    expect(top.view.moduleName).toBeTruthy();
  });

  it('can search a caller-supplied subset', () => {
    const subset = allViews().filter((v) => v.moduleSlug === 'payments');
    expect(searchViews('cohort', { views: subset })).toEqual([]);
    expect(searchViews('fraud', { views: subset })[0].view.slug).toBe('fraud-and-risk-analytics');
  });
});
