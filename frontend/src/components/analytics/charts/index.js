/**
 * Chart surface for the analytics views.
 *
 * `ChartCard` is the one most callers want: hand it a registry `ChartSpec` and
 * the matching `envelope.series[spec.id]` and it picks the renderer. The
 * individual components are exported for the bespoke views, which sometimes
 * need a chart without the card shell around it.
 */
export { ChartCard } from './ChartCard.jsx';
export { LineOrAreaChart } from './LineOrAreaChart.jsx';
export { BarChartCard } from './BarChartCard.jsx';
export { PieDonutChart } from './PieDonutChart.jsx';
export { WaterfallChart } from './WaterfallChart.jsx';
export { ScatterChart } from './ScatterChart.jsx';
export { Sparkline } from './Sparkline.jsx';
export { ChartDataTable, ChartFrame, ChartNote } from './ChartA11y.jsx';

export {
  animationProps,
  AXIS_PROPS,
  AXIS_TICK,
  axisLabel,
  CAT_COLORS,
  CHART_HEIGHT,
  CHART_TYPES,
  colorAt,
  compactTick,
  DELTA_COLORS,
  GRID_PROPS,
  GRID_PROPS_HORIZONTAL_BARS,
  looksLikeDate,
  MISSING_DASH,
  MISSING_FILL,
  prefersReducedMotion,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from './chartTokens.js';

export {
  gapNote,
  isGap,
  isolatedIndexes,
  normalizeSeries,
  seriesCoverage,
  specSeries,
  toSlices,
  toWaterfallBars,
  valueAt,
} from './seriesGuards.js';
