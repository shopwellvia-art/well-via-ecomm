import { describe, it, expect } from 'vitest';
import { SEARCH_PAGES, matchPages } from '@/features/search/pages.js';

const paths = (results) => results.map((p) => p.to);

describe('SEARCH_PAGES registry', () => {
  it('has no duplicate destinations', () => {
    const seen = SEARCH_PAGES.map((p) => p.to);
    expect(new Set(seen).size).toBe(seen.length);
  });

  it('gives every entry a label and at least one keyword', () => {
    for (const page of SEARCH_PAGES) {
      expect(page.label, `${page.to} needs a label`).toBeTruthy();
      expect(page.keywords?.length, `${page.to} needs keywords`).toBeGreaterThan(0);
    }
  });

  it('uses absolute in-app paths (no external or relative links)', () => {
    for (const page of SEARCH_PAGES) {
      expect(page.to).toMatch(/^\/[a-z0-9/-]*$/);
    }
  });

  // Keywords are lowercased before comparison in scorePage, so an uppercase
  // keyword in the registry would silently never match.
  it('stores keywords in lowercase', () => {
    for (const page of SEARCH_PAGES) {
      for (const kw of page.keywords) expect(kw).toBe(kw.toLowerCase());
    }
  });
});

describe('matchPages', () => {
  it('ignores queries shorter than 2 characters', () => {
    expect(matchPages('s')).toEqual([]);
    expect(matchPages('')).toEqual([]);
    expect(matchPages(null)).toEqual([]);
  });

  it('finds a page by a word in its label', () => {
    expect(paths(matchPages('press'))).toContain('/press');
  });

  it('is case-insensitive and trims', () => {
    expect(paths(matchPages('  CAREERS '))).toContain('/careers');
  });

  // The whole reason the registry carries keywords: shoppers search intent, not
  // page titles. None of these words appear in the labels they must resolve to.
  it.each([
    ['return', '/refund'],
    ['refund', '/refund'],
    ['exchange', '/refund'],
    ['delivery', '/shipping'],
    ['courier', '/shipping'],
    ['jobs', '/careers'],
    ['blog', '/stories'],
    ['support', '/contact'],
    ['cookies', '/privacy'],
  ])('resolves the intent "%s" to %s', (term, expected) => {
    expect(paths(matchPages(term))).toContain(expected);
  });

  it('ranks a label match above a keyword-only match', () => {
    // "shipping" is the label of /shipping and a keyword of nothing else that
    // outranks it.
    expect(paths(matchPages('shipping'))[0]).toBe('/shipping');
  });

  it('hides account-only pages from signed-out shoppers', () => {
    expect(paths(matchPages('orders', { isSignedIn: false }))).not.toContain('/orders');
    expect(paths(matchPages('orders', { isSignedIn: true }))).toContain('/orders');
  });

  it('offers order tracking to a signed-in shopper who types "track"', () => {
    expect(paths(matchPages('track', { isSignedIn: true }))).toContain('/orders');
  });

  it('respects the limit', () => {
    // A vowel-heavy stem hits many keywords; the cap must still hold.
    expect(matchPages('re', { isSignedIn: true, limit: 3 }).length).toBeLessThanOrEqual(3);
  });

  it('returns nothing for a term no page covers', () => {
    expect(matchPages('zzzqqq')).toEqual([]);
  });

  // Word-boundary matching: a mid-word substring must not drag in a page.
  it('does not match a substring in the middle of a label word', () => {
    expect(paths(matchPages('ellvia'))).not.toContain('/about');
  });
});
