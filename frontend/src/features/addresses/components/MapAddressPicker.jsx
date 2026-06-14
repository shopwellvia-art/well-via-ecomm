/**
 * MapAddressPicker — Flipkart/Swiggy-style map picker.
 *
 * Interaction: the pin is FIXED at the centre of the viewport (a CSS overlay,
 * NOT a Leaflet marker). The user pans/zooms the MAP underneath it; on every
 * `moveend` we read map.getCenter() and reverse-geocode that exact point.
 *
 * Props
 *   open      – controls visibility
 *   onClose   – backdrop / X / Escape
 *   onSelect  – called with { pincode, city, state, area, road, lat, lng }
 *
 * Layout
 *   Desktop (≥768px): ~80vw × 80vh centered panel, map left + info rail right.
 *   Mobile: full-screen, map top (55%) + info panel bottom (45%).
 *
 * Tiles: CartoDB Voyager (light, Google-Maps-like).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X, LocateFixed, Loader2, MapPin, Navigation, CheckCircle2 } from 'lucide-react';
import { MapContainer, TileLayer, useMapEvents, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

import { Button } from '@/components/ui/Button.jsx';
import { cn } from '@/lib/utils.js';
import { addressApi } from '../api.js';
import { getDevicePosition, friendlyGeoError } from '../hooks.js';

// ─── Constants ────────────────────────────────────────────────────────────────

const INDIA_CENTER     = [20.5937, 78.9629];
const INDIA_ZOOM       = 5;
const LOCATED_ZOOM     = 16;
const MIN_RESOLVE_ZOOM = 12;   // don't geocode while zoomed out to the country view
const DEBOUNCE_MS      = 400;

// ─── Haversine distance (km) ─────────────────────────────────────────────────

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ─── Inner map components ─────────────────────────────────────────────────────

/**
 * Watches map pan/zoom. `movestart` signals the map is in motion (so the pin
 * can lift and the stale address can clear); `moveend` reports the new centre
 * so the parent can reverse-geocode it.
 */
function CenterWatcher({ onMoveStart, onMoveEnd }) {
  const map = useMapEvents({
    movestart() {
      onMoveStart();
    },
    moveend() {
      const c = map.getCenter();
      onMoveEnd(c.lat, c.lng, map.getZoom());
    },
  });
  return null;
}

/** Imperatively flyTo when the flyTo prop changes. */
function MapController({ flyTo }) {
  const map = useMap();
  useEffect(() => {
    if (!flyTo) return;
    map.flyTo(flyTo.center, flyTo.zoom, { duration: 0.8, easeLinearity: 0.5 });
  }, [map, flyTo]);
  return null;
}

// ─── MapAddressPicker ─────────────────────────────────────────────────────────

export default function MapAddressPicker({ open, onClose, onSelect }) {
  const [pinPosition, setPinPosition] = useState(INDIA_CENTER); // last map centre
  const [flyTo, setFlyTo]             = useState(null);
  const [devicePos, setDevicePos]     = useState(null);   // { lat, lng } when known
  const [geoStatus, setGeoStatus]     = useState('idle'); // 'idle'|'locating'|'error'
  const [geoError, setGeoError]       = useState(null);
  const [dragging, setDragging]       = useState(false);  // map currently in motion

  const [resolving, setResolving]     = useState(false);
  const [geoResult, setGeoResult]     = useState(null);   // reverseGeocode response
  const [hasResolved, setHasResolved] = useState(false);

  const debounceRef  = useRef(null);
  // Focus management refs
  const closeButtonRef  = useRef(null);
  const openerRef       = useRef(null); // stores document.activeElement before open

  // ── Escape key ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) { if (e.key === 'Escape') onClose?.(); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // ── Focus management: capture opener, move focus in, restore on close ───────
  useEffect(() => {
    if (open) {
      // Capture the element that had focus before the modal opened
      openerRef.current = document.activeElement;
      // After paint, move focus to the close button
      const raf = requestAnimationFrame(() => {
        closeButtonRef.current?.focus();
      });
      return () => cancelAnimationFrame(raf);
    } else {
      // Restore focus to the opener when the modal closes
      if (openerRef.current && typeof openerRef.current.focus === 'function') {
        openerRef.current.focus();
        openerRef.current = null;
      }
    }
  }, [open]);

  // ── Reverse geocode (debounced) ──────────────────────────────────────────────
  const scheduleResolve = useCallback((lat, lng) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setResolving(true);
      try {
        const result = await addressApi.reverseGeocode(lat, lng);
        setGeoResult(result);
        setHasResolved(result.found === true);
      } catch {
        setGeoResult(null);
        setHasResolved(false);
      } finally {
        setResolving(false);
      }
    }, DEBOUNCE_MS);
  }, []);

  // ── Auto-locate on open ──────────────────────────────────────────────────────
  // Geocoding is driven by `moveend` (see handleMoveEnd) — here we only fly the
  // map to the device location; the resulting moveend resolves the address.
  useEffect(() => {
    if (!open) return;
    // Reset per-open state
    setGeoResult(null);
    setHasResolved(false);
    setGeoError(null);
    setDevicePos(null);
    setDragging(false);
    setPinPosition(INDIA_CENTER);
    setFlyTo(null);
    setGeoStatus('locating');

    getDevicePosition()
      .then(({ latitude, longitude }) => {
        setDevicePos({ lat: latitude, lng: longitude });
        setGeoStatus('idle');
        setFlyTo({ center: [latitude, longitude], zoom: LOCATED_ZOOM });
      })
      .catch(() => {
        setGeoStatus('idle');
        setGeoError('Location access denied — move the map to your address.');
      });
  }, [open]);

  // ── Map moved ────────────────────────────────────────────────────────────────
  function handleMoveStart() {
    setDragging(true);
    // hide the stale address while the map is in motion
    setGeoResult(null);
    setHasResolved(false);
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }

  function handleMoveEnd(lat, lng, zoom) {
    setDragging(false);
    setPinPosition([lat, lng]);
    if (zoom >= MIN_RESOLVE_ZOOM) scheduleResolve(lat, lng);
  }

  // ── "Use my current location" button ─────────────────────────────────────────
  function handleLocateMe() {
    setGeoError(null);
    setGeoStatus('locating');
    getDevicePosition()
      .then(({ latitude, longitude }) => {
        setDevicePos({ lat: latitude, lng: longitude });
        setGeoStatus('idle');
        setFlyTo({ center: [latitude, longitude], zoom: LOCATED_ZOOM });
      })
      .catch((err) => {
        setGeoStatus('error');
        setGeoError(friendlyGeoError(err));
      });
  }

  // ── Confirm ──────────────────────────────────────────────────────────────────
  function handleConfirm() {
    if (!geoResult?.found) return;
    onSelect?.({
      pincode: geoResult.pincode || '',
      city:    geoResult.city    || '',
      state:   geoResult.state   || '',
      area:    geoResult.area    || '',
      road:    geoResult.road    || '',
      lat:     pinPosition[0],
      lng:     pinPosition[1],
    });
    onClose?.();
  }

  // ── Derived values ───────────────────────────────────────────────────────────
  const distanceLabel = (() => {
    if (!devicePos || !hasResolved) return null;
    const km = haversineKm(
      devicePos.lat, devicePos.lng,
      pinPosition[0], pinPosition[1],
    );
    return `${km.toFixed(1)} km away from your current location`;
  })();

  const addrLines = (() => {
    if (!geoResult?.found) return [];
    const { road, area, city, state, pincode } = geoResult;
    return [
      [road, area].filter(Boolean).join(', '),
      [city, state].filter(Boolean).join(', '),
      pincode,
    ].filter(Boolean);
  })();

  // ─────────────────────────────────────────────────────────────────────────────

  return createPortal(
    <AnimatePresence>
      {open && (
        /* z-[60] keeps us above drawers (z-50) but below any toasts */
        <div className="fixed inset-0 z-[60] flex items-center justify-center">
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            onClick={onClose}
            aria-hidden="true"
          />

          {/* Modal panel
              Desktop: centered card 80vw × 80vh, flex-row (map | rail)
              Mobile:  full-screen, flex-col (map top | rail bottom)   */}
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Pick delivery location on map"
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1,    y: 0  }}
            exit={{ opacity: 0, scale: 0.96, y: 12 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            // `isolate` creates a stacking context so Leaflet panes (z: 400–650)
            // stay inside the modal, not above the close button.
            className="isolate relative z-0 flex w-full h-full overflow-hidden bg-bg-elevated
                       flex-col
                       md:flex-row md:w-[80vw] md:h-[80vh] md:max-w-5xl md:rounded-xl
                       md:border md:border-line-subtle md:shadow-lg"
          >
            {/* ── Close button (always on top of map) ── */}
            <button
              ref={closeButtonRef}
              type="button"
              aria-label="Close map picker"
              onClick={onClose}
              className={cn(
                'absolute top-3 right-3 z-[500]',
                'grid size-8 place-items-center rounded-full p-2 -m-2',
                'bg-bg-elevated border border-line-subtle',
                'text-ink-tertiary transition-colors hover:text-ink-primary',
                'focus-visible:focus-ring',
              )}
            >
              <X className="size-4" />
            </button>

            {/* ── Map area ── */}
            {/* flex-[55] on mobile stacks above rail; flex-1 on desktop fills left */}
            <div className="relative flex-[55_1_0%] min-h-0 md:flex-1">
              <MapContainer
                center={INDIA_CENTER}
                zoom={INDIA_ZOOM}
                zoomControl={false}
                attributionControl={true}
                className="h-full w-full bg-bg-sunken"
              >
                <TileLayer
                  url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
                  attribution="&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors &copy; <a href='https://carto.com/attributions'>CARTO</a>"
                  subdomains="abcd"
                  maxZoom={20}
                />
                <CenterWatcher onMoveStart={handleMoveStart} onMoveEnd={handleMoveEnd} />
                <MapController flyTo={flyTo} />
              </MapContainer>

              {/* ── Fixed centre pin (the map moves under it) ── */}
              <div className="pointer-events-none absolute inset-0 z-[450]">
                {/* ground shadow — marks the exact map centre */}
                <span
                  className={cn(
                    'absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-[50%]',
                    'bg-black/40 transition-all duration-200',
                    dragging ? 'h-1 w-2.5 opacity-25 blur-[2px]' : 'h-1.5 w-3.5 opacity-50 blur-[1px]',
                  )}
                />
                {/* pin — tip rests on the map centre, lifts while dragging */}
                <div
                  className={cn(
                    'absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-full origin-bottom',
                    'transition-transform duration-200 ease-out',
                    dragging && '-translate-y-[calc(100%+8px)] scale-105',
                  )}
                >
                  {/* text-accent makes currentColor resolve to the accent token */}
                  <span className="text-accent">
                    <svg
                      width="34" height="46" viewBox="0 0 32 44" fill="none"
                      xmlns="http://www.w3.org/2000/svg"
                      className="drop-shadow-[0_6px_8px_rgba(0,0,0,0.35)]"
                    >
                      <path
                        d="M16 0C7.163 0 0 7.163 0 16c0 11 16 28 16 28S32 27 32 16C32 7.163 24.837 0 16 0z"
                        fill="currentColor"
                      />
                      <circle cx="16" cy="16" r="6" fill="white" fillOpacity="0.95" />
                    </svg>
                  </span>
                </div>
              </div>

              {/* "Move the map" hint (auto-locate failed or not resolved yet) */}
              {geoStatus === 'idle' && !devicePos && !hasResolved && (
                <div className="pointer-events-none absolute top-4 left-1/2 -translate-x-1/2 z-[400]">
                  <p className="rounded-sm bg-bg-elevated px-3 py-1.5
                                text-xs text-ink-secondary shadow-md border border-line-subtle whitespace-nowrap">
                    Move the map to position the pin over your address
                  </p>
                </div>
              )}

              {/* "Use my current location" pill — bottom-center of map */}
              <div
                className="absolute left-1/2 -translate-x-1/2 z-[460] flex flex-col items-center gap-1.5"
                style={{ bottom: 'max(1rem, env(safe-area-inset-bottom))' }}
              >
                <button
                  type="button"
                  onClick={handleLocateMe}
                  disabled={geoStatus === 'locating'}
                  aria-label={geoStatus === 'locating' ? 'Locating your position' : 'Use my current location'}
                  className={cn(
                    'inline-flex items-center gap-2 rounded-full px-4 py-2',
                    'bg-bg-elevated border border-accent/30',
                    'text-sm font-semibold text-accent shadow-md',
                    'transition-all hover:border-accent hover:shadow-sm',
                    'disabled:opacity-60 disabled:cursor-not-allowed',
                    'focus-visible:focus-ring',
                  )}
                >
                  {geoStatus === 'locating' ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <LocateFixed className="size-4" aria-hidden="true" />
                  )}
                  {geoStatus === 'locating' ? 'Locating…' : 'Use my current location'}
                </button>
                {geoStatus === 'error' && geoError && (
                  <p className="max-w-[280px] rounded-sm bg-bg-elevated px-3 py-1.5
                                text-center text-xs text-danger shadow-md border border-line-subtle">
                    {geoError}
                  </p>
                )}
              </div>
            </div>

            {/* ── Info rail (right on desktop, bottom on mobile) ── */}
            <div
              className={cn(
                'flex flex-col gap-4 bg-bg-elevated p-5',
                'flex-[45_1_0%] overflow-y-auto',
                'pb-[env(safe-area-inset-bottom,1rem)]',
                'md:flex-none md:w-72 md:border-l md:border-line-subtle',
                'border-t border-line-subtle md:border-t-0',
              )}
            >
              <div className="flex items-center gap-2">
                <MapPin className="size-4 shrink-0 text-accent" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-ink-primary leading-tight">
                  Deliver to this location
                </h2>
              </div>

              {/* Resolved address */}
              <div className="flex-1 flex flex-col gap-3">
                {resolving ? (
                  <div className="flex items-center gap-2 py-2">
                    <Loader2 className="size-4 animate-spin text-accent" aria-hidden="true" />
                    <span className="text-xs text-ink-secondary">Finding address…</span>
                  </div>
                ) : geoResult ? (
                  geoResult.found ? (
                    <motion.div
                      key="found"
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.2 }}
                      className="flex flex-col gap-1.5"
                    >
                      <div className="flex items-start gap-2">
                        <CheckCircle2 className="size-4 shrink-0 mt-0.5 text-success" aria-hidden="true" />
                        <div className="flex flex-col gap-0.5 min-w-0">
                          {addrLines.map((line, i) => (
                            <p
                              key={i}
                              className={cn(
                                'break-words',
                                i === 0
                                  ? 'text-sm font-medium text-ink-primary'
                                  : 'text-xs text-ink-secondary',
                              )}
                            >
                              {line}
                            </p>
                          ))}
                        </div>
                      </div>
                      {distanceLabel && (
                        <div className="flex items-center gap-1.5 pl-6">
                          <Navigation className="size-3 shrink-0 text-accent" aria-hidden="true" />
                          <p className="text-[11px] font-medium text-accent nums">
                            {distanceLabel}
                          </p>
                        </div>
                      )}
                    </motion.div>
                  ) : (
                    <motion.div
                      key="not-found"
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.2 }}
                      className="rounded-sm border border-warning/30 bg-warning/12 p-3"
                    >
                      <p className="text-xs text-warning leading-relaxed">
                        We couldn&apos;t find a deliverable pincode here — move the map
                        closer to a road or town.
                      </p>
                    </motion.div>
                  )
                ) : (
                  <p className="text-xs text-ink-tertiary leading-relaxed">
                    Move the map to see the address.
                  </p>
                )}

                {/* Inline geo error (locate-me failures shown below the address area) */}
                {geoStatus === 'idle' && geoError && !devicePos && (
                  <p className="text-xs text-ink-tertiary italic">{geoError}</p>
                )}
              </div>

              {/* Confirm button */}
              <div className="flex flex-col gap-2 pt-2 border-t border-line-subtle">
                <Button
                  type="button"
                  onClick={handleConfirm}
                  disabled={!hasResolved || resolving}
                  className="w-full"
                >
                  {resolving ? (
                    <>
                      <Loader2 className="size-4 animate-spin mr-1.5" aria-hidden="true" />
                      Resolving…
                    </>
                  ) : (
                    'Update pin and proceed'
                  )}
                </Button>
                <p className="text-[10px] text-center text-ink-tertiary">
                  Move the map to refine
                </p>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
