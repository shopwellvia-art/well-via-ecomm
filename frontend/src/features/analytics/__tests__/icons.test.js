/**
 * The one test in this feature that imports React-land.
 *
 * `registry.icons.js` exists so that `registry.js`, `presentation.js`,
 * `format.js`, `filters.js` and `search.js` stay importable without
 * `lucide-react` — and the other three test files prove that by never touching
 * it. This file deliberately crosses the line, because the alternative is that
 * a mistyped icon name in the overlay silently resolves to `FALLBACK_ICON` and
 * ships as a generic bar chart on every card, which nobody notices in review.
 */
import { describe, it, expect } from 'vitest';
import {
  MODULE_PRESENTATION,
  VIEW_PRESENTATION,
} from '@/features/analytics/presentation.js';
import {
  FALLBACK_ICON,
  LUCIDE_ICONS,
  MODULE_ICONS,
  VIEW_ICONS,
  analyticsNavItems,
  analyticsViewNavItems,
  getModuleIcon,
  getViewIcon,
} from '@/features/analytics/registry.icons.js';
import { getModule } from '@/features/analytics/registry.js';

const namedIcons = (overlay) =>
  Object.entries(overlay).filter(([, p]) => !(p.icon in LUCIDE_ICONS));

describe('icon bindings', () => {
  it('imports every icon name the overlay uses', () => {
    expect(namedIcons(MODULE_PRESENTATION)).toEqual([]);
    expect(namedIcons(VIEW_PRESENTATION)).toEqual([]);
  });

  it('binds all 12 modules and all 73 views without falling back', () => {
    expect(Object.keys(MODULE_ICONS)).toHaveLength(12);
    expect(Object.keys(VIEW_ICONS)).toHaveLength(73);
    expect(Object.values(MODULE_ICONS).filter((c) => c === FALLBACK_ICON)).toEqual([]);
    expect(Object.values(VIEW_ICONS).filter((c) => c === FALLBACK_ICON)).toEqual([]);
  });

  it('falls back rather than returning undefined for an unknown slug', () => {
    expect(getModuleIcon('no-such-module')).toBe(FALLBACK_ICON);
    expect(getViewIcon('no-such-view')).toBe(FALLBACK_ICON);
  });

  it('hands the sidebar components, not names', () => {
    const items = analyticsNavItems();
    expect(items).toHaveLength(12);
    expect(Object.keys(items[0])).toEqual(['to', 'label', 'icon', 'end', 'permission']);
    expect(typeof items[0].icon).not.toBe('string');
    expect(analyticsViewNavItems('payments')).toHaveLength(getModule('payments').views.length);
  });
});
