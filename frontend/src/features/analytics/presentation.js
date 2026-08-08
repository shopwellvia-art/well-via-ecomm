/**
 * The presentation overlay — the ONLY place look-and-feel for an analytics
 * module or view is declared. Pure data plus three lookups; no React, no
 * imports from the contract, so it stays testable in the node environment.
 *
 * The split is deliberate and load-bearing:
 *
 *   `registry.contract.json` (generated, backend-owned)  →  what a view MEANS.
 *                                                           Slugs, permissions,
 *                                                           state, KPI ids,
 *                                                           which chart series
 *                                                           exist.
 *   this file (hand-maintained, frontend-owned)          →  what it LOOKS like.
 *                                                           Icon, tone, grid
 *                                                           span, table density.
 *
 * Icons are stored as Lucide *names*, not components, so this file can be
 * imported by a node test. `registry.icons.js` resolves the names to
 * components and is the only file in the feature that pulls in React.
 *
 * Because the contract is regenerated from Python, a new view can appear
 * without anyone touching this file. `diffPresentation` makes that loud: the
 * bijection between contract slugs and the keys below is asserted in
 * `__tests__/registry.test.js`, so a missing entry fails CI instead of
 * rendering an unlabelled card in production.
 *
 * Vocabulary
 * ----------
 * `span`    — columns in the entry's own grid. For a view this is the chart
 *             grid the contract's per-chart `span` is expressed in (so `2` is
 *             the normal half/half layout and `1` means one full-width canvas,
 *             which is what the bespoke visuals need). For a module it is the
 *             column count of the landing page's view-card grid.
 * `tone`    — semantic colour role, not a hex value: revenue | volume | rate |
 *             duration | risk | neutral. Derived from the chart's format by
 *             default; overridden per chart id below where the format is right
 *             but the *sentiment* is not — `returns_trend` is an int series
 *             like any other, but a rising line there is bad news and must not
 *             be drawn in the same encouraging colour as `orders_trend`.
 * `density` — table row height: comfortable | compact.
 */

/** Colour role implied by a chart's FormatId when nothing overrides it. */
export const TONE_BY_FORMAT = Object.freeze({
  money: 'revenue',
  int: 'volume',
  pct: 'rate',
  ratio: 'rate',
  days: 'duration',
  hours: 'duration',
  text: 'neutral',
});

export const DEFAULT_MODULE_PRESENTATION = Object.freeze({
  icon: 'BarChart3',
  span: 3,
  charts: {},
  tables: {},
});

export const DEFAULT_VIEW_PRESENTATION = Object.freeze({
  icon: 'BarChart3',
  span: 2,
  charts: {},
  tables: {},
});

/** Keyed by module slug. */
export const MODULE_PRESENTATION = {
  executive: { icon: 'Gauge', span: 3 },
  'sales-finance': { icon: 'Banknote', span: 3 },
  products: { icon: 'Package', span: 3 },
  customers: { icon: 'Users', span: 3 },
  marketing: { icon: 'Megaphone', span: 3 },
  website: { icon: 'Globe', span: 3 },
  inventory: { icon: 'Warehouse', span: 3 },
  orders: { icon: 'Truck', span: 3 },
  payments: { icon: 'CreditCard', span: 3 },
  marketplace: { icon: 'Store', span: 3 },
  'customer-experience': { icon: 'MessageCircle', span: 3 },
  'control-centre': { icon: 'Settings2', span: 2 },
};

/**
 * Keyed by view slug. Entries carry only what differs from
 * `DEFAULT_VIEW_PRESENTATION` — an entry of `{ icon }` is the common case and
 * means "standard two-column chart grid, tone derived from format".
 */
export const VIEW_PRESENTATION = {
  // --- executive ---------------------------------------------------------
  'executive-overview': {
    icon: 'LayoutDashboard',
    charts: { revenue_trend: { height: 'tall' } },
  },
  'real-time-sales': {
    icon: 'Radio',
    // A feed that reloads every minute; compact rows keep more of it on screen.
    tables: { recent_orders: { density: 'compact' } },
  },
  'sales-forecasting': { icon: 'TrendingUp' },
  'seasonal-and-festival-sales': { icon: 'Sparkles' },
  'ecommerce-business-health': { icon: 'HeartPulse' },
  'budget-vs-actual': { icon: 'Target' },

  // --- sales-finance -----------------------------------------------------
  'revenue-and-profitability': {
    icon: 'Coins',
    // The waterfall is the view; it needs the whole width to stay readable.
    span: 1,
  },
  'orders-and-average-order-value': { icon: 'ShoppingCart' },
  'coupon-and-discount-performance': {
    icon: 'TicketPercent',
    // Discount given away is a cost, however healthy the trend looks.
    charts: { discount_trend: { tone: 'risk' } },
  },
  'pricing-and-margin': { icon: 'Tag', tables: { pricing_table: { density: 'compact' } } },
  'contribution-margin': { icon: 'Layers' },
  'unit-economics': { icon: 'Calculator' },
  'cash-flow-and-working-capital': { icon: 'Wallet' },
  'tax-and-gst': { icon: 'Percent' },

  // --- products ----------------------------------------------------------
  'product-performance': { icon: 'Package' },
  'category-performance': { icon: 'Tags' },
  'sku-level-analytics': { icon: 'Barcode', tables: { sku_table: { density: 'compact' } } },
  'product-recommendation-performance': { icon: 'Wand2' },
  'product-bundling-and-cross-sell': { icon: 'Boxes' },
  'upsell-performance': { icon: 'ArrowUpRight' },

  // --- customers ---------------------------------------------------------
  'customer-analytics': { icon: 'Users' },
  'new-vs-returning-customers': { icon: 'UserPlus' },
  'customer-lifetime-value': { icon: 'Gem' },
  'customer-segmentation': { icon: 'PieChart' },
  'cohort-and-retention': {
    icon: 'LayoutGrid',
    // Heatmap: one wide canvas, never half a row.
    span: 1,
  },
  'customer-churn': {
    icon: 'UserMinus',
    charts: { lapsed_trend: { tone: 'risk' }, lapse_by_segment: { tone: 'risk' } },
  },
  'loyalty-and-rewards': { icon: 'Award' },
  'subscription-commerce': { icon: 'RefreshCw' },
  'rfm-customer-analysis': { icon: 'Grid2x2', span: 1 },

  // --- marketing ---------------------------------------------------------
  'marketing-channel-performance': { icon: 'Share2' },
  'campaign-performance': { icon: 'Rocket' },
  'roas-and-marketing-profitability': { icon: 'LineChart' },
  'seo-performance': { icon: 'Search' },
  'social-media-commerce': { icon: 'Instagram' },
  'email-and-sms-marketing': { icon: 'Mail' },
  'affiliate-and-influencer': { icon: 'Handshake' },
  'customer-journey-and-attribution': { icon: 'Route', span: 1 },

  // --- website -----------------------------------------------------------
  'conversion-funnel': { icon: 'Filter', span: 1 },
  'cart-abandonment': {
    icon: 'ShoppingBag',
    charts: { abandonment_trend: { tone: 'risk' }, abandoned_value: { tone: 'risk' } },
  },
  'checkout-performance': { icon: 'CreditCard' },
  'website-traffic': { icon: 'Globe' },
  'landing-page-performance': { icon: 'FileText' },
  'geographic-sales': { icon: 'MapPin', span: 1, tables: { geo_table: { density: 'compact' } } },
  'device-and-browser-analytics': { icon: 'Smartphone' },
  'search-analytics': { icon: 'ScanSearch' },

  // --- inventory ---------------------------------------------------------
  'inventory-analytics': { icon: 'Boxes' },
  'stock-availability': { icon: 'PackageCheck' },
  'low-stock-and-out-of-stock': {
    icon: 'PackageX',
    charts: { stockouts_trend: { tone: 'risk' } },
    tables: { low_stock_table: { density: 'compact' } },
  },
  'inventory-turnover': { icon: 'RefreshCcw' },
  'demand-forecasting': { icon: 'TrendingUp' },
  'supplier-performance': { icon: 'Factory' },
  'warehouse-performance': { icon: 'Warehouse' },

  // --- orders ------------------------------------------------------------
  'shipping-and-delivery': {
    icon: 'Truck',
    // Ageing stock in transit is a backlog, not a volume.
    charts: { in_transit_ageing: { tone: 'risk' } },
  },
  'courier-performance': { icon: 'Bike', tables: { courier_table: { density: 'compact' } } },
  'order-fulfilment': { icon: 'ClipboardCheck', tables: { ageing_orders: { density: 'compact' } } },
  'returns-and-refunds': {
    icon: 'Undo2',
    charts: { returns_trend: { tone: 'risk' }, return_reasons: { tone: 'risk' } },
  },
  'cancellation-analytics': {
    icon: 'XCircle',
    charts: { cancellations_trend: { tone: 'risk' }, cancel_reasons: { tone: 'risk' } },
  },

  // --- payments ----------------------------------------------------------
  'payment-analytics': { icon: 'CreditCard' },
  'payment-failure': {
    icon: 'AlertTriangle',
    charts: { failure_trend: { tone: 'risk' } },
    tables: { failure_reasons: { density: 'compact' } },
  },
  'cod-performance': {
    icon: 'Banknote',
    charts: { cod_rto_by_state: { tone: 'risk' } },
    tables: { cod_by_state: { density: 'compact' } },
  },
  'fraud-and-risk-analytics': {
    icon: 'ShieldAlert',
    charts: { risk_signals: { tone: 'risk' } },
    tables: { flagged_orders: { density: 'compact' } },
  },
  'settlements-and-payouts': { icon: 'Landmark' },

  // --- marketplace -------------------------------------------------------
  'marketplace-performance': { icon: 'Store' },
  'store-or-branch-performance': { icon: 'Building2' },
  'b2b-customer-analytics': { icon: 'Briefcase' },

  // --- customer-experience -----------------------------------------------
  'customer-support-and-complaint': { icon: 'LifeBuoy' },
  'reviews-and-ratings': { icon: 'Star' },
  'behaviour-and-ux-insights': { icon: 'MousePointerClick' },
  'website-speed-and-technical-performance': {
    icon: 'Gauge',
    charts: { latency_trend: { tone: 'duration' }, error_rate: { tone: 'risk' } },
    tables: { slow_endpoints: { density: 'compact' } },
  },

  // --- control-centre ----------------------------------------------------
  'analytics-tracking-health': { icon: 'Activity', span: 1 },
  'data-reconciliation': {
    icon: 'Scale',
    span: 1,
    charts: { variance_trend: { tone: 'risk' } },
    tables: { variances: { density: 'compact' } },
  },
  'experiment-and-ab-testing': { icon: 'FlaskConical', span: 1 },
  'alerts-and-anomaly': {
    icon: 'BellRing',
    span: 1,
    charts: { alerts_trend: { tone: 'risk' } },
    tables: { alert_feed: { density: 'compact' } },
  },
};

/** Full presentation for a module slug, defaults filled in. */
export function modulePresentation(slug) {
  return { ...DEFAULT_MODULE_PRESENTATION, ...(MODULE_PRESENTATION[slug] ?? {}) };
}

/** Full presentation for a view slug, defaults filled in. */
export function viewPresentation(slug) {
  return { ...DEFAULT_VIEW_PRESENTATION, ...(VIEW_PRESENTATION[slug] ?? {}) };
}

/**
 * Presentation for one chart of one view: the contract's own `span`/`format`,
 * plus the tone derived from the format and any override declared above.
 */
export function chartPresentation(viewSlug, chart) {
  const override = VIEW_PRESENTATION[viewSlug]?.charts?.[chart?.id] ?? {};
  return {
    tone: TONE_BY_FORMAT[chart?.format] ?? 'neutral',
    height: 'normal',
    span: chart?.span ?? 1,
    ...override,
  };
}

/** Presentation for one table of one view. */
export function tablePresentation(viewSlug, table) {
  const override = VIEW_PRESENTATION[viewSlug]?.tables?.[table?.id] ?? {};
  return { density: 'comfortable', ...override };
}

/**
 * Compare the overlay against the slugs the contract actually ships.
 *
 * `missing` — in the contract, no presentation entry: the view would render
 *             with a placeholder icon and a guessed layout.
 * `extra`   — an entry for a slug the contract no longer has: dead config that
 *             will quietly rot until someone wonders why an icon change did
 *             nothing.
 *
 * Both directions matter, which is why this returns a diff rather than a
 * boolean.
 */
export function diffPresentation(moduleSlugs = [], viewSlugs = []) {
  const diff = (slugs, overlay) => ({
    missing: slugs.filter((s) => !(s in overlay)).sort(),
    extra: Object.keys(overlay).filter((s) => !slugs.includes(s)).sort(),
  });
  return {
    modules: diff(moduleSlugs, MODULE_PRESENTATION),
    views: diff(viewSlugs, VIEW_PRESENTATION),
  };
}
