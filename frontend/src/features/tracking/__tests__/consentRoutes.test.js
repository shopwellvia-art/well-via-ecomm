/**
 * The analytics-settings route: that it exists, that it is guarded, and — the
 * part that would break silently — that the module page does not swallow it.
 *
 * `/admin/analytics/settings` and `/admin/analytics/:moduleSlug` both match that
 * URL. React Router ranks the static segment higher, so the settings page wins;
 * but "wins by an implicit ranking rule" is exactly the kind of guarantee that
 * evaporates when someone reorders the file, and the failure is quiet — the
 * module page renders with `moduleSlug === 'settings'` and 404s from the API
 * rather than crashing.
 *
 * So the paths are read out of App.jsx and handed to React Router's own
 * `matchRoutes`, which is the same ranking code the app runs. No component is
 * rendered: the route table is data, and this asserts against the data.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';
import { matchRoutes } from 'react-router-dom';

const APP_PATH = fileURLToPath(new URL('../../../app/App.jsx', import.meta.url));
const source = readFileSync(APP_PATH, 'utf8');

/** Every `path="…"` in App.jsx, in declaration order. */
const declaredPaths = [...source.matchAll(/\bpath="([^"]*)"/g)].map((m) => m[1]);

/** The JSX for one route, from its `path=` to the start of the next element. */
function routeBlockFor(path) {
  const start = source.indexOf(`path="${path}"`);
  if (start === -1) return '';
  const next = source.indexOf('<Route', start);
  return source.slice(start, next === -1 ? source.length : next);
}

describe('the route table', () => {
  it('declares /admin/analytics/settings', () => {
    expect(declaredPaths).toContain('admin/analytics/settings');
  });

  it('guards it with analytics.integrations.manage, not analytics.view', () => {
    const block = routeBlockFor('admin/analytics/settings');
    expect(block).toContain('permission="analytics.integrations.manage"');
    expect(block).toContain('<AdminAnalyticsSettingsPage />');
    expect(block).not.toContain('permission="analytics.view"');
  });

  it('declares it before the dynamic module route', () => {
    const settings = declaredPaths.indexOf('admin/analytics/settings');
    const moduleRoute = declaredPaths.indexOf('admin/analytics/:moduleSlug');
    expect(settings).toBeGreaterThan(-1);
    expect(moduleRoute).toBeGreaterThan(-1);
    expect(settings).toBeLessThan(moduleRoute);
  });
});

describe('matching, through React Router itself', () => {
  // Flattened: every admin route is a sibling under one pathless parent, so
  // flattening changes nothing about how these two rank against each other.
  const routes = declaredPaths.map((path) => ({ path }));

  it('sends /admin/analytics/settings to the settings route, not the module page', () => {
    const matches = matchRoutes(routes, '/admin/analytics/settings');
    expect(matches).not.toBeNull();
    expect(matches.at(-1).route.path).toBe('admin/analytics/settings');
  });

  it('still sends a real module slug to the module page', () => {
    const matches = matchRoutes(routes, '/admin/analytics/traffic');
    expect(matches.at(-1).route.path).toBe('admin/analytics/:moduleSlug');
    expect(matches.at(-1).params.moduleSlug).toBe('traffic');
  });

  it('keeps the settings route winning even if the file is reordered', () => {
    // The same table with the dynamic route first — the guarantee this test
    // exists to state is that ranking, not order, is what decides.
    const reordered = [
      { path: 'admin/analytics/:moduleSlug' },
      { path: 'admin/analytics/settings' },
    ];
    expect(matchRoutes(reordered, '/admin/analytics/settings').at(-1).route.path).toBe(
      'admin/analytics/settings',
    );
  });

  it('leaves the legacy analytics pages reachable', () => {
    expect(matchRoutes(routes, '/admin/analytics/sales').at(-1).route.path).toBe(
      'admin/analytics/sales',
    );
    expect(matchRoutes(routes, '/admin/analytics/profit').at(-1).route.path).toBe(
      'admin/analytics/profit',
    );
  });
});

describe('the app mounts tracking and the banner', () => {
  it('calls useTracking() once, inside the router', () => {
    expect(source).toContain('useTracking()');
    expect([...source.matchAll(/useTracking\(\)/g)]).toHaveLength(1);
  });

  it('mounts the consent gate', () => {
    expect(source).toContain('<ConsentGate />');
  });

  it('does not re-implement the gates useTracking already applies', () => {
    // Duplicating the consent/admin checks here is how one of the two copies
    // ends up weaker than the other.
    expect(source).not.toMatch(/hasAnalyticsConsent|isAdminRoute|initTracking/);
  });
});
