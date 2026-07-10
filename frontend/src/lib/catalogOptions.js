/**
 * Shared catalog vocabulary — single source of truth for BOTH the admin
 * product form and the storefront filter sidebar, so values never drift.
 *
 * FLAVOURS feed `products.flavour` verbatim; the listing filter sends them
 * back via the `flavours` CSV query param, so an exact string match matters.
 *
 * GOALS map the mockup's "Shop by Goal" checkboxes onto category slugs —
 * goals ARE categories (no separate backend field). The slugs must match the
 * categories seeded via Admin → Categories.
 */

export const FLAVOURS = [
  'Grape',
  'Green Apple',
  'Black Currant',
  'Mixed Berry',
  'Orange',
  'Mixed Fruit',
];

export const GOALS = [
  { label: 'Immunity Support', slug: 'immunity-support' },
  { label: 'Gut Health', slug: 'gut-health' },
  { label: 'Beauty & Glow', slug: 'beauty-glow' },
  { label: 'Better Sleep', slug: 'better-sleep' },
  { label: 'Daily Wellness', slug: 'daily-wellness' },
  { label: 'Energy & Vitality', slug: 'energy-vitality' },
];
