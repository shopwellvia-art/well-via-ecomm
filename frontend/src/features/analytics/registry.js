/**
 * The analytics registry as the frontend sees it: the generated backend
 * contract with the presentation overlay merged on top.
 *
 * Pure data and lookups — no React, no network — so the whole registry is
 * exercised by the node-environment test suite.
 *
 * The rule this file follows without exception: **no slug, permission, KPI id
 * or route fragment is written down here.** Every one of them comes out of
 * `registry.contract.json`, which is generated from
 * `backend/app/services/analytics/registry.py`. Routes are built by
 * concatenating slugs the contract supplies, so adding a view in Python and
 * regenerating the JSON is the entire frontend change — there is no second
 * list to forget to update, and no way for the two sides to disagree about
 * what a view is called.
 *
 * Icons are names, not components. `registry.icons.js` maps them to Lucide
 * components; keeping that in a separate module is what lets a node test
 * import this file without pulling React into the process.
 */
import contract from './registry.contract.json';
import {
  diffPresentation,
  modulePresentation,
  viewPresentation,
} from './presentation.js';

/** Where the analytics section is mounted. The only literal path in the feature. */
export const ANALYTICS_BASE_PATH = '/admin/analytics';

export const CONTRACT_VERSION = contract.version;

/** Route for a module's landing page. */
export function moduleRoute(moduleSlug) {
  return `${ANALYTICS_BASE_PATH}/${moduleSlug}`;
}

/** Route for a single view. */
export function viewRoute(moduleSlug, viewSlug) {
  return `${moduleRoute(moduleSlug)}/${viewSlug}`;
}

function buildView(module, view) {
  return Object.freeze({
    ...view,
    moduleSlug: module.slug,
    moduleName: module.name,
    to: viewRoute(module.slug, view.slug),
    presentation: viewPresentation(view.slug),
  });
}

function buildModule(module) {
  const views = module.views.map((view) => buildView(module, view));
  return Object.freeze({
    ...module,
    to: moduleRoute(module.slug),
    presentation: modulePresentation(module.slug),
    views: Object.freeze(views),
  });
}

/** All 12 modules, in registry order, each with its views merged. */
export const MODULES = Object.freeze(contract.modules.map(buildModule));

/** Every view across every module, flattened, in registry order. */
const ALL_VIEWS = Object.freeze(MODULES.flatMap((m) => m.views));

/** The KPI catalogue, untouched — it is pure backend metadata. */
export const KPIS = Object.freeze(contract.kpis);

const MODULE_BY_SLUG = new Map(MODULES.map((m) => [m.slug, m]));
const VIEW_BY_PATH = new Map(ALL_VIEWS.map((v) => [`${v.moduleSlug}/${v.slug}`, v]));
const VIEW_BY_SLUG = new Map(ALL_VIEWS.map((v) => [v.slug, v]));
const KPI_BY_ID = new Map(KPIS.map((k) => [k.id, k]));

export function getModule(slug) {
  return MODULE_BY_SLUG.get(slug) ?? null;
}

export function getView(moduleSlug, viewSlug) {
  return VIEW_BY_PATH.get(`${moduleSlug}/${viewSlug}`) ?? null;
}

/**
 * Look a view up by slug alone.
 *
 * View slugs are unique across the whole registry (asserted in the tests), so
 * this is safe — it exists for the command menu and for deep links that have
 * lost their module segment.
 */
export function getViewBySlug(slug) {
  return VIEW_BY_SLUG.get(slug) ?? null;
}

export function allViews() {
  return ALL_VIEWS;
}

export function getKpi(id) {
  return KPI_BY_ID.get(id) ?? null;
}

/** The KPI definitions a view declares, in the order the view declares them. */
export function viewKpis(view) {
  return (view?.kpis ?? []).map((id) => getKpi(id)).filter(Boolean);
}

/** The view a module opens on. */
export function defaultView(moduleSlug) {
  const module = getModule(moduleSlug);
  return module ? getView(module.slug, module.default_view_slug) : null;
}

/**
 * Sidebar nav items for all 12 modules, in `AdminSidebar`'s shape:
 * `{ to, label, icon, end, permission }`.
 *
 * `icons` is an optional slug → component map (see `registry.icons.js`). Left
 * out, `icon` is the Lucide *name* — which keeps this module React-free and
 * lets a node test assert the shape. `AdminSidebar` renders `<item.icon />`,
 * so the React caller must pass the map; `analyticsNavItems()` in
 * `registry.icons.js` is that call, pre-wired.
 *
 * `end` is false for every item: each module owns a subtree of routes, so the
 * nav entry should stay highlighted while the operator is inside it.
 *
 * `permission` comes straight from the backend — the sidebar's existing
 * `isVisible` check then hides modules a staff user cannot open, which means
 * the nav and the API agree on access by construction.
 */
export function moduleNavItems(icons = null) {
  return MODULES.map((module) => ({
    to: module.to,
    label: module.name,
    icon: icons?.[module.slug] ?? module.presentation.icon,
    end: false,
    permission: module.permission,
  }));
}

/** Nav items for one module's views, same shape, for a second-level menu. */
export function viewNavItems(moduleSlug, icons = null) {
  const module = getModule(moduleSlug);
  if (!module) return [];
  return module.views.map((view) => ({
    to: view.to,
    label: view.name,
    icon: icons?.[view.slug] ?? view.presentation.icon,
    end: true,
    permission: view.permission,
  }));
}

/**
 * Does the presentation overlay still line up with the generated contract?
 *
 * Returns `{ modules: { missing, extra }, views: { missing, extra } }`, all
 * empty when they agree. Asserted in the tests so a regenerated contract that
 * introduces a view cannot ship without its icon and layout.
 */
export function presentationIntegrity() {
  return diffPresentation(
    MODULES.map((m) => m.slug),
    ALL_VIEWS.map((v) => v.slug),
  );
}
