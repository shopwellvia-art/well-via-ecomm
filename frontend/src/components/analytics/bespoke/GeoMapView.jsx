import { MapPin } from 'lucide-react';
import { CircleMarker, MapContainer, TileLayer, Tooltip as LeafletTooltip } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { formatValue } from '@/features/analytics/format.js';
import { isMissing } from '@/features/analytics/viewState.js';
import { ChartCard } from '../charts/ChartCard.jsx';
import { MISSING_DASH, valueAt } from '../charts/seriesGuards.js';
import {
  CaveatBanner,
  MiniTable,
  NotConfiguredPanel,
  Panel,
  StatusPill,
  Td,
  Th,
} from './BespokeParts.jsx';
import { notConfigured, seriesFor, tableBlock, warningWith } from './bespokeHelpers.js';

/**
 * View 43 — Geographic Sales.
 *
 * ── A production-only CSP hazard, recorded here on purpose ──────────────────
 * This map uses Leaflet with a RASTER tile layer. Raster tiles are fetched by
 * the browser as `<img>` elements, and `frontend/nginx.conf` allows those:
 *
 *     img-src 'self' data: https:
 *
 * A vector-tile provider (MapLibre GL, Protomaps, Mapbox GL) is a different
 * story. Vector tiles are pulled with `fetch`/XHR, which is governed by
 * `connect-src` — and that directive is not declared, so it falls back to
 * `default-src 'self'`. Every tile request would be blocked.
 *
 * The trap is that it would work perfectly in development: `vite dev` serves
 * no CSP header at all, so a vector map renders fine locally and fails only
 * once nginx serves the built bundle. If anyone swaps this for a vector
 * provider, `connect-src` has to be widened in nginx.conf in the same change.
 * ───────────────────────────────────────────────────────────────────────────
 *
 * Coordinates are not in the envelope — `agg_geo_daily` stores state names and
 * pincodes, not lat/lng — so states are placed from the static centroid table
 * below. A state with no centroid is NOT dropped: it is listed under the map
 * with its figures, because a state silently missing from a map reads as a
 * state with no sales.
 */

/** Approximate geographic centroids, degrees. Placement only, never analysis. */
const STATE_CENTROIDS = {
  'andhra pradesh': [15.9129, 79.74],
  'arunachal pradesh': [28.218, 94.7278],
  assam: [26.2006, 92.9376],
  bihar: [25.0961, 85.3131],
  chandigarh: [30.7333, 76.7794],
  chhattisgarh: [21.2787, 81.8661],
  delhi: [28.7041, 77.1025],
  goa: [15.2993, 74.124],
  gujarat: [22.2587, 71.1924],
  haryana: [29.0588, 76.0856],
  'himachal pradesh': [31.1048, 77.1734],
  'jammu and kashmir': [33.7782, 76.5762],
  jharkhand: [23.6102, 85.2799],
  karnataka: [15.3173, 75.7139],
  kerala: [10.8505, 76.2711],
  ladakh: [34.2268, 77.5619],
  lakshadweep: [10.5667, 72.6417],
  'madhya pradesh': [22.9734, 78.6569],
  maharashtra: [19.7515, 75.7139],
  manipur: [24.6637, 93.9063],
  meghalaya: [25.467, 91.3662],
  mizoram: [23.1645, 92.9376],
  nagaland: [26.1584, 94.5624],
  odisha: [20.9517, 85.0985],
  puducherry: [11.9416, 79.8083],
  punjab: [31.1471, 75.3412],
  rajasthan: [27.0238, 74.2179],
  sikkim: [27.533, 88.5122],
  'tamil nadu': [11.1271, 78.6569],
  telangana: [18.1124, 79.0193],
  tripura: [23.9408, 91.9882],
  'uttar pradesh': [26.8467, 80.9462],
  uttarakhand: [30.0668, 79.0193],
  'west bengal': [22.9868, 87.855],
};

const INDIA_CENTER = [22.5, 79.0];

function centroidFor(state) {
  if (!state) return null;
  return STATE_CENTROIDS[String(state).trim().toLowerCase()] ?? null;
}

/**
 * Split rows into the ones a marker can be drawn for and the ones it cannot.
 *
 * A row is unmapped either because the state name has no centroid or because
 * its revenue is not computable. Neither is treated as zero and neither is
 * discarded — both come back in `unmapped` so the view can list them.
 */
function placeRows(rows) {
  const mapped = [];
  const unmapped = [];
  let maxRevenue = 0;
  for (const row of rows ?? []) {
    const revenue = valueAt(row, 'net_revenue') ?? valueAt(row, 'revenue');
    const point = centroidFor(row?.state);
    if (revenue !== null && revenue > maxRevenue) maxRevenue = revenue;
    if (!point) {
      unmapped.push({ row, revenue, reason: 'no centroid for this state name' });
      continue;
    }
    if (revenue === null) {
      unmapped.push({ row, revenue: null, reason: 'revenue not computable' });
      continue;
    }
    mapped.push({ row, revenue, point });
  }
  return { mapped, unmapped, maxRevenue };
}

export function GeoMapView({ envelope, viewDef }) {
  if (notConfigured(envelope)) {
    return <NotConfiguredPanel envelope={envelope} viewDef={viewDef} />;
  }

  const block = tableBlock(envelope, 'geo_table');
  const rows = block?.rows ?? [];
  const scopeWarning = warningWith(envelope, 'GEO_SCOPE_PARTIAL');

  const { mapped, unmapped, maxRevenue } = placeRows(rows);

  const chartSpec = (viewDef?.charts ?? [])[0];

  return (
    <div className="space-y-4">
      {scopeWarning && <CaveatBanner warning={scopeWarning} tone="info" />}

      <Panel
        title="Where the orders went"
        action={
          block?.truncated ? (
            <StatusPill tone="warning">top rows only</StatusPill>
          ) : null
        }
        description={
          'Circle area is proportional to revenue. Placement uses a static ' +
          'state centroid, so a marker sits at the middle of a state rather ' +
          'than at any customer.'
        }
        bodyClassName="p-0"
      >
        {mapped.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={MapPin}
              size="sm"
              bordered={false}
              title="Nothing to place on the map"
              description={
                rows.length === 0
                  ? 'No delivery address in this window resolved to a state.'
                  : 'No row could be given a location or a revenue figure. ' +
                    'The figures are listed below rather than dropped.'
              }
            />
          </div>
        ) : (
          <div className="h-96 w-full">
            <MapContainer
              center={INDIA_CENTER}
              zoom={4}
              scrollWheelZoom={false}
              className="h-full w-full"
              // No entry animation: this is a data surface, not a hero.
              zoomAnimation={false}
              fadeAnimation={false}
              markerZoomAnimation={false}
            >
              {/* Raster tiles: <img> requests, permitted by `img-src https:`.
                  See the CSP note in the module docstring before swapping this
                  for a vector-tile provider. */}
              <TileLayer
                attribution="&copy; OpenStreetMap contributors"
                url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              {mapped.map(({ row, revenue, point }) => (
                <CircleMarker
                  key={`${row.state}-${row.city ?? row.pincode ?? ''}`}
                  center={point}
                  radius={radiusFor(revenue, maxRevenue)}
                  pathOptions={{
                    color: '#2874F0',
                    weight: 1,
                    fillColor: '#2874F0',
                    fillOpacity: 0.35,
                  }}
                >
                  <LeafletTooltip>
                    <span className="text-xs">
                      {`${row.state}: ${formatValue(revenue, 'money')}`}
                      {isMissing(row.orders)
                        ? ''
                        : ` · ${formatValue(row.orders, 'int')} orders`}
                    </span>
                  </LeafletTooltip>
                </CircleMarker>
              ))}
            </MapContainer>
          </div>
        )}

        {unmapped.length > 0 && (
          <div className="border-t border-line-subtle px-5 py-3">
            <p className="mb-2 text-xs font-medium text-ink-secondary">
              {`${unmapped.length} row${unmapped.length === 1 ? '' : 's'} not on the map`}
              {' — listed here rather than dropped, because a state missing from a '}
              map reads as a state with no sales.
            </p>
            <ul className="space-y-1 text-xs text-ink-tertiary">
              {unmapped.slice(0, 8).map(({ row, revenue, reason }) => (
                <li key={`${row.state}-${reason}`}>
                  {`${row.state ?? MISSING_DASH} — `}
                  {revenue === null ? MISSING_DASH : formatValue(revenue, 'money')}
                  {` (${reason})`}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Panel>

      <ChartCard
        spec={
          chartSpec ?? {
            id: 'geo_revenue',
            type: 'hbar',
            x: 'state',
            series: ['net_revenue'],
            format: 'money',
            title: 'Revenue by state',
          }
        }
        data={seriesFor(envelope, chartSpec?.id ?? 'geo_revenue')}
        labels={{ net_revenue: 'Net revenue' }}
      />

      <Panel title="States and cities" bodyClassName="p-0">
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={MapPin}
              size="sm"
              bordered={false}
              title="No rows"
              description="No delivery address in this window resolved to a state."
            />
          </div>
        ) : (
          <MiniTable
            caption="Orders, revenue and RTO rate by state"
            head={
              <>
                <Th>State</Th>
                <Th>City</Th>
                <Th align="right">Orders</Th>
                <Th align="right">Revenue</Th>
                <Th align="right">COD share</Th>
                <Th align="right">RTO</Th>
              </>
            }
          >
            {rows.map((row, index) => (
              <tr
                key={`${row.state}-${row.city ?? row.pincode ?? index}`}
                className="border-b border-line-subtle/60 last:border-0"
              >
                <Td>{row.state ?? MISSING_DASH}</Td>
                <Td className="text-ink-secondary">
                  {row.city ?? row.pincode ?? MISSING_DASH}
                </Td>
                <Td align="right">{formatValue(row.orders, 'int')}</Td>
                <Td align="right">
                  {formatValue(row.net_revenue ?? row.revenue, 'money')}
                </Td>
                <Td align="right">{formatValue(row.cod_share, 'pct')}</Td>
                <Td align="right">{formatValue(row.rto_rate, 'pct')}</Td>
              </tr>
            ))}
          </MiniTable>
        )}
        {block?.truncated && (
          <p className="border-t border-line-subtle px-5 py-3 text-xs text-ink-tertiary">
            Capped at the row limit — this is a top-N, not the whole population.
          </p>
        )}
      </Panel>
    </div>
  );
}

/** Circle AREA scales with revenue, so radius scales with its square root. */
function radiusFor(revenue, max) {
  if (!Number.isFinite(revenue) || !Number.isFinite(max) || max <= 0) return 5;
  return 5 + Math.sqrt(Math.max(0, revenue) / max) * 22;
}

export default GeoMapView;
