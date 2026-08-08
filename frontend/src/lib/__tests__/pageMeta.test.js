// @vitest-environment jsdom
/**
 * Head-management unit coverage (eng review 3A, 2026-08-03).
 *
 * The two behaviors that silently wreck SEO if they regress:
 *  - the title claim counter — SiteMeta must stand down while any page
 *    manages the title, and the site default must come back after the last
 *    page unmounts;
 *  - upsert cleanup — a tag this lib created is removed on unmount, a tag
 *    that already existed (e.g. shipped in index.html) is restored, never
 *    deleted.
 */
import { beforeEach, describe, it, expect } from 'vitest';
import {
  applyPageMeta,
  isPageTitleClaimed,
  absoluteUrl,
  clampDescription,
} from '../pageMeta.js';

beforeEach(() => {
  document.head.querySelectorAll('meta, link').forEach((el) => el.remove());
  document.title = 'Site Default';
});

describe('title claim counter', () => {
  it('claims while a page holds the title and releases on cleanup', () => {
    expect(isPageTitleClaimed()).toBe(false);
    const cleanup = applyPageMeta({ title: 'Product — Wellvia' });
    expect(isPageTitleClaimed()).toBe(true);
    expect(document.title).toBe('Product — Wellvia');
    cleanup();
    expect(isPageTitleClaimed()).toBe(false);
    expect(document.title).toBe('Site Default');
  });

  it('stays claimed until the LAST overlapping page unmounts', () => {
    const first = applyPageMeta({ title: 'Page A' });
    const second = applyPageMeta({ title: 'Page B' });
    first();
    expect(isPageTitleClaimed()).toBe(true);
    second();
    expect(isPageTitleClaimed()).toBe(false);
  });

  it('does not claim the title when none is given', () => {
    const cleanup = applyPageMeta({ description: 'desc only' });
    expect(isPageTitleClaimed()).toBe(false);
    cleanup();
  });
});

describe('meta/link upsert cleanup', () => {
  it('removes tags it created', () => {
    const cleanup = applyPageMeta({
      title: 'T',
      description: 'A fine description',
      canonical: 'https://shopwellvia.in/products/1',
    });
    expect(document.head.querySelector('meta[name="description"]')).not.toBeNull();
    expect(document.head.querySelector('link[rel="canonical"]')).not.toBeNull();
    cleanup();
    expect(document.head.querySelector('meta[name="description"]')).toBeNull();
    expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
  });

  it('restores — never removes — a tag that already existed', () => {
    const preexisting = document.createElement('meta');
    preexisting.setAttribute('name', 'description');
    preexisting.setAttribute('content', 'shipped in index.html');
    document.head.appendChild(preexisting);

    const cleanup = applyPageMeta({ description: 'page description' });
    expect(
      document.head.querySelector('meta[name="description"]').getAttribute('content'),
    ).toBe('page description');
    cleanup();
    const after = document.head.querySelector('meta[name="description"]');
    expect(after).not.toBeNull();
    expect(after.getAttribute('content')).toBe('shipped in index.html');
  });

  it('emits robots noindex only when asked', () => {
    const cleanup = applyPageMeta({ title: 'T', noindex: true });
    expect(
      document.head.querySelector('meta[name="robots"]').getAttribute('content'),
    ).toBe('noindex,follow');
    cleanup();
    const none = applyPageMeta({ title: 'T' });
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
    none();
  });

  it('picks the twitter card style from image presence', () => {
    const withImage = applyPageMeta({ title: 'T', image: 'https://x/y.jpg' });
    expect(
      document.head.querySelector('meta[name="twitter:card"]').getAttribute('content'),
    ).toBe('summary_large_image');
    withImage();
    const without = applyPageMeta({ title: 'T' });
    expect(
      document.head.querySelector('meta[name="twitter:card"]').getAttribute('content'),
    ).toBe('summary');
    without();
  });
});

describe('absoluteUrl', () => {
  it('prefixes the current origin', () => {
    expect(absoluteUrl('/products/9')).toBe(`${window.location.origin}/products/9`);
  });
});

describe('clampDescription', () => {
  it('returns empty for missing text', () => {
    expect(clampDescription()).toBe('');
    expect(clampDescription(null)).toBe('');
  });

  it('flattens whitespace and leaves short text alone', () => {
    expect(clampDescription('  two\n  words \t here ')).toBe('two words here');
  });

  it('cuts on a word boundary with an ellipsis', () => {
    const long = 'word '.repeat(60).trim();
    const clamped = clampDescription(long);
    expect(clamped.length).toBeLessThanOrEqual(160);
    expect(clamped.endsWith('…')).toBe(true);
    expect(clamped).not.toMatch(/wor…$/);
  });
});
