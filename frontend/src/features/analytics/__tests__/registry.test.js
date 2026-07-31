import { describe, it, expect } from 'vitest';
import contract from '@/features/analytics/registry.contract.json';
import {
  KPIS,
  MODULES,
  allViews,
  defaultView,
  getKpi,
  getModule,
  getView,
  getViewBySlug,
  moduleNavItems,
  presentationIntegrity,
  viewKpis,
  viewNavItems,
} from '@/features/analytics/registry.js';
import {
  MODULE_PRESENTATION,
  VIEW_PRESENTATION,
  chartPresentation,
  diffPresentation,
  tablePresentation,
} from '@/features/analytics/presentation.js';
import { VIEW_STATE } from '@/features/analytics/viewState.js';

const views = allViews();

describe('contract integrity', () => {
  it('ships exactly 12 modules, 73 views and 63 KPIs', () => {
    expect(MODULES).toHaveLength(12);
    expect(views).toHaveLength(73);
    // 59 -> 61: basket cross-sell (basket_attach_rate, basket_pairs_observed).
    // 61 -> 63: blended ROAS (total_spend, blended_roas — deliberately distinct
    // from the ad-platform marketing_spend/cac entries, which stay gated).
    // This count is a guard against ACCIDENTAL additions: update it only in the
    // same change that adds a KpiDef, never to sync a drifted contract.
    expect(KPIS).toHaveLength(63);
  });

  it('has unique module slugs, view slugs and KPI ids', () => {
    expect(new Set(MODULES.map((m) => m.slug)).size).toBe(MODULES.length);
    expect(new Set(views.map((v) => v.slug)).size).toBe(views.length);
    expect(new Set(KPIS.map((k) => k.id)).size).toBe(KPIS.length);
  });

  // View numbers are the operator-facing "view 43 of 73". A gap or a duplicate
  // means the registry lost or double-counted a view during a regeneration.
  it('numbers the views exactly 1..73 with no gaps or repeats', () => {
    const numbers = views.map((v) => v.number).sort((a, b) => a - b);
    expect(numbers).toEqual(Array.from({ length: 73 }, (_, i) => i + 1));
  });

  it('every module opens on a view it actually owns', () => {
    for (const module of MODULES) {
      expect(defaultView(module.slug), module.slug).not.toBeNull();
      expect(defaultView(module.slug).moduleSlug).toBe(module.slug);
    }
  });

  it('every KPI a view declares exists in the catalogue', () => {
    for (const view of views) {
      expect(viewKpis(view), view.slug).toHaveLength(view.kpis.length);
    }
  });

  it('does not restate the contract — every slug is carried through verbatim', () => {
    expect(MODULES.map((m) => m.slug)).toEqual(contract.modules.map((m) => m.slug));
    expect(views.map((v) => v.slug)).toEqual(
      contract.modules.flatMap((m) => m.views.map((v) => v.slug)),
    );
  });
});

describe('view state contract', () => {
  const KNOWN_STATES = Object.values(VIEW_STATE);

  it('declares one of the six known states for every view', () => {
    expect(KNOWN_STATES).toHaveLength(6);
    for (const view of views) {
      expect(KNOWN_STATES, `${view.slug} -> ${view.state}`).toContain(view.state);
    }
  });

  // A view that is not fully LIVE has to be able to explain itself. Without
  // `requires` and `limitation` the gate renders an empty apology, which is
  // worse than no view at all — the operator cannot tell whether it is broken,
  // unbuilt, or waiting on something they could go and connect.
  it('gives every non-LIVE view a non-empty `requires` and `limitation`', () => {
    for (const view of views.filter((v) => v.state !== VIEW_STATE.LIVE)) {
      expect(view.requires, view.slug).not.toHaveLength(0);
      expect(view.limitation, view.slug).not.toBe('');
    }
  });
});

describe('presentation overlay', () => {
  // The bijection is the guard against a regenerated contract silently
  // outrunning the frontend (a new view with no icon) and against dead overlay
  // entries for views that no longer exist.
  it('is a bijection with the contract slugs', () => {
    expect(presentationIntegrity()).toEqual({
      modules: { missing: [], extra: [] },
      views: { missing: [], extra: [] },
    });
  });

  it('has one entry per module and per view', () => {
    expect(Object.keys(MODULE_PRESENTATION)).toHaveLength(12);
    expect(Object.keys(VIEW_PRESENTATION)).toHaveLength(73);
  });

  it('detects a slug that exists on only one side', () => {
    const withGhost = diffPresentation(
      [...MODULES.map((m) => m.slug), 'ghost-module'],
      views.map((v) => v.slug).filter((s) => s !== 'cohort-and-retention'),
    );
    expect(withGhost.modules.missing).toEqual(['ghost-module']);
    expect(withGhost.views.extra).toEqual(['cohort-and-retention']);
  });

  it('resolves every merged view to a complete presentation object', () => {
    for (const view of views) {
      const p = view.presentation;
      expect(Object.keys(p).sort(), view.slug).toEqual(['charts', 'icon', 'span', 'tables']);
      expect(typeof p.icon).toBe('string');
      expect(p.span).toBeGreaterThan(0);
    }
  });

  it('derives a chart tone from the format and lets the overlay override it', () => {
    const executive = getView('executive', 'executive-overview');
    const revenue = executive.charts.find((c) => c.id === 'revenue_trend');
    expect(chartPresentation(executive.slug, revenue)).toMatchObject({
      tone: 'revenue',
      height: 'tall',
    });

    // Same format, opposite sentiment — an int series that should never be
    // drawn like a growth chart.
    const cancellations = getView('orders', 'cancellation-analytics');
    const trend = cancellations.charts.find((c) => c.id === 'cancellations_trend');
    expect(trend.format).toBe('int');
    expect(chartPresentation(cancellations.slug, trend).tone).toBe('risk');
  });

  it('defaults table density and honours the overlay', () => {
    const realtime = getView('executive', 'real-time-sales');
    expect(tablePresentation(realtime.slug, { id: 'recent_orders' }).density).toBe('compact');
    expect(tablePresentation(realtime.slug, { id: 'nope' }).density).toBe('comfortable');
  });
});

describe('lookups', () => {
  it('finds modules and views, and returns null rather than throwing', () => {
    expect(getModule('customers').name).toBe(
      contract.modules.find((m) => m.slug === 'customers').name,
    );
    expect(getModule('no-such-module')).toBeNull();
    expect(getView('customers', 'cohort-and-retention').number).toBe(12);
    expect(getView('customers', 'product-performance')).toBeNull(); // wrong module
    expect(getViewBySlug('product-performance').moduleSlug).toBe('products');
    expect(getKpi('net_revenue').unit).toBe('money');
    expect(getKpi('not_a_kpi')).toBeNull();
  });

  it('builds routes from contract slugs only', () => {
    const view = getView('payments', 'cod-performance');
    expect(view.to).toBe('/admin/analytics/payments/cod-performance');
    expect(getModule('payments').to).toBe('/admin/analytics/payments');
  });
});

describe('moduleNavItems', () => {
  it('returns 12 items in the sidebar’s exact key shape', () => {
    const items = moduleNavItems();
    expect(items).toHaveLength(12);
    for (const item of items) {
      expect(Object.keys(item)).toEqual(['to', 'label', 'icon', 'end', 'permission']);
      expect(item.to.startsWith('/admin/analytics/')).toBe(true);
      expect(item.label).not.toBe('');
      expect(item.end).toBe(false);
      expect(item.permission).toMatch(/^analytics\./);
    }
  });

  it('takes permissions straight from the contract', () => {
    expect(moduleNavItems().map((i) => i.permission)).toEqual(
      contract.modules.map((m) => m.permission),
    );
  });

  // The icon is a name until React hands over the components, which is what
  // keeps this module importable from a node test.
  it('yields icon names by default and components when a map is supplied', () => {
    expect(typeof moduleNavItems()[0].icon).toBe('string');
    const fake = Object.fromEntries(MODULES.map((m) => [m.slug, () => null]));
    expect(typeof moduleNavItems(fake)[0].icon).toBe('function');
  });

  it('produces second-level items for a module’s views', () => {
    const items = viewNavItems('control-centre');
    expect(items).toHaveLength(getModule('control-centre').views.length);
    expect(Object.keys(items[0])).toEqual(['to', 'label', 'icon', 'end', 'permission']);
    expect(viewNavItems('no-such-module')).toEqual([]);
  });
});
