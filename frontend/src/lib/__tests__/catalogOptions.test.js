import { describe, it, expect } from 'vitest';
import { FLAVOURS, GOALS, resolveGoalCategoryIds } from '@/lib/catalogOptions.js';

const CATEGORIES = [
  { id: 0, slug: 'immunity-support', name: 'Immunity Support' },
  { id: 7, slug: 'gut-health', name: 'Gut Health' },
  { id: 12, slug: 'better-sleep', name: 'Better Sleep' },
];

describe('resolveGoalCategoryIds', () => {
  it('maps goal slugs to their category ids, preserving order', () => {
    expect(resolveGoalCategoryIds(['better-sleep', 'gut-health'], CATEGORIES)).toEqual([
      12, 7,
    ]);
  });

  it('keeps a legitimate id of 0 (null-check, not falsy-check)', () => {
    expect(resolveGoalCategoryIds(['immunity-support'], CATEGORIES)).toEqual([0]);
  });

  it('drops slugs with no seeded category instead of emitting undefined', () => {
    expect(
      resolveGoalCategoryIds(['gut-health', 'not-a-category'], CATEGORIES),
    ).toEqual([7]);
  });

  it('resolves to nothing while categories are still loading or absent', () => {
    expect(resolveGoalCategoryIds(['gut-health'], [])).toEqual([]);
    expect(resolveGoalCategoryIds(['gut-health'], undefined)).toEqual([]);
    expect(resolveGoalCategoryIds(undefined, CATEGORIES)).toEqual([]);
  });
});

describe('catalog vocabulary invariants', () => {
  it('goal slugs are unique and URL-safe (they ride in a CSV query param)', () => {
    const slugs = GOALS.map((g) => g.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const slug of slugs) expect(slug).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
  });

  it('flavours are unique (exact string match feeds products.flavour)', () => {
    expect(new Set(FLAVOURS).size).toBe(FLAVOURS.length);
  });
});
