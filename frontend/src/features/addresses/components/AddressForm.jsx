import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Home, Briefcase, MapPin, LocateFixed, Loader2, MapPinned } from 'lucide-react';
import { Input } from '@/components/storefront/ui/Input.jsx';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { cn } from '@/lib/utils.js';
import { usePincodeLookup, useCurrentLocation, friendlyGeoError } from '../hooks.js';

// Lazy-load the map picker so the ~150 KB leaflet chunk is only fetched when opened.
const MapAddressPicker = lazy(() => import('./MapAddressPicker.jsx'));

// API label values are lowercase ("home"/"work"/"other") — AddressLabel is a
// str-enum validated by value, so the payload must use these exact strings.
const LABELS = ['home', 'work', 'other'];

const LABEL_META = {
  home:  { icon: Home,      label: 'Home'  },
  work:  { icon: Briefcase, label: 'Work'  },
  other: { icon: MapPin,    label: 'Other' },
};

const EMPTY = {
  full_name: '',
  phone: '',
  line1: '',
  line2: '',
  landmark: '',
  city: '',
  state: '',
  pincode: '',
  country: 'IN',
  label: 'home',
  is_default: false,
  latitude: null,
  longitude: null,
};

function validate(v) {
  const errs = {};
  if (!v.full_name.trim()) errs.full_name = 'Name is required.';
  if (!v.phone.trim()) {
    errs.phone = 'Phone is required.';
  } else {
    const digits = v.phone.replace(/[\s\-+]/g, '').replace(/^91/, '');
    if (digits.length < 8 || digits.length > 20) {
      errs.phone = 'Enter 8–20 digits (e.g. +91 98765 43210).';
    }
  }
  if (!v.line1.trim()) errs.line1 = 'Address line 1 is required.';
  if (!v.city.trim()) errs.city = 'City is required.';
  if (!v.state.trim()) errs.state = 'State is required.';
  if (!/^[1-9][0-9]{5}$/.test(v.pincode.trim())) {
    errs.pincode = 'Enter a valid 6-digit Indian pincode.';
  }
  return errs;
}

/**
 * Controlled address form. Drives city/state from pincode lookup (user can
 * still edit — auto-fill never overwrites values typed after the fill).
 *
 * Props
 *   initialValues  – pre-populate (edit mode)
 *   onSubmit(values) – called with validated payload
 *   onCancel       – cancel button handler
 *   submitLabel    – button label (default "Save address")
 *   busy           – shows spinner on submit button
 *   showSetDefault – render the "Set as default" checkbox
 */
export default function AddressForm({
  initialValues,
  onSubmit,
  onCancel,
  submitLabel = 'Save address',
  busy = false,
  showSetDefault = true,
}) {
  const [values, setValues] = useState(() => {
    const v = { ...EMPTY, ...initialValues };
    // Tolerate legacy uppercase labels ("HOME") from older saved drafts.
    v.label = String(v.label || 'home').toLowerCase();
    return v;
  });
  const [errors, setErrors] = useState({});
  const [geoError, setGeoError] = useState(null);
  const [mapOpen, setMapOpen] = useState(false);
  const currentLocation = useCurrentLocation();
  // Track whether the user has manually changed city/state so autofill won't
  // clobber their edits.
  const userEditedCity = useRef(!!(initialValues?.city));
  const userEditedState = useRef(!!(initialValues?.state));

  const { data: pincodeData } = usePincodeLookup(values.pincode);

  // Autofill city + state when the pincode lookup returns a hit and the user
  // hasn't manually typed into those fields after the most recent autofill.
  useEffect(() => {
    if (!pincodeData?.found) return;
    setValues((prev) => {
      const next = { ...prev };
      if (!userEditedCity.current || !prev.city) {
        next.city = pincodeData.city || prev.city;
        userEditedCity.current = false;
      }
      if (!userEditedState.current || !prev.state) {
        next.state = pincodeData.state || prev.state;
        userEditedState.current = false;
      }
      return next;
    });
  }, [pincodeData]);

  function set(field, val) {
    setValues((prev) => ({ ...prev, [field]: val }));
    if (errors[field]) setErrors((prev) => { const n = { ...prev }; delete n[field]; return n; });
  }

  function handlePincodeChange(val) {
    userEditedCity.current = false;
    userEditedState.current = false;
    // A hand-typed pincode invalidates any previously map-pinned coordinates.
    setValues((prev) => ({ ...prev, pincode: val, latitude: null, longitude: null }));
    if (errors.pincode) setErrors((prev) => { const n = { ...prev }; delete n.pincode; return n; });
  }

  function handleCityChange(val) {
    userEditedCity.current = true;
    set('city', val);
  }

  function handleStateChange(val) {
    userEditedState.current = true;
    set('state', val);
  }

  function handleUseLocation() {
    setGeoError(null);
    currentLocation.mutate(undefined, {
      onSuccess(data) {
        if (data.found) {
          setValues((prev) => ({
            ...prev,
            pincode: data.pincode || prev.pincode,
            city: data.city || prev.city,
            state: data.state || prev.state,
            latitude: data.lat ?? prev.latitude,
            longitude: data.lng ?? prev.longitude,
          }));
          // Reset edit-guards so pincode lookup can refine and edit-protection
          // keeps working — mirrors the handlePincodeChange semantics.
          userEditedCity.current = false;
          userEditedState.current = false;
        } else {
          setGeoError(
            "We couldn't find a pincode for your location — please enter it manually.",
          );
        }
      },
      onError(err) {
        setGeoError(friendlyGeoError(err));
      },
    });
  }

  function handleMapSelect({ pincode, city, state, area, road, lat, lng }) {
    setValues((prev) => {
      const next = {
        ...prev,
        pincode:   pincode || prev.pincode,
        city:      city    || prev.city,
        state:     state   || prev.state,
        latitude:  lat     ?? prev.latitude,
        longitude: lng     ?? prev.longitude,
      };
      // Seed line2 only when it's currently empty — never overwrite user text.
      const streetHint = [road, area].filter(Boolean).join(', ');
      if (!prev.line2.trim() && streetHint) {
        next.line2 = streetHint;
      }
      return next;
    });
    // Reset edit-guards so pincode lookup can refine, same as geolocate fill.
    userEditedCity.current = false;
    userEditedState.current = false;
    // Clear any field errors that were just filled.
    setErrors((prev) => {
      const n = { ...prev };
      delete n.pincode;
      delete n.city;
      delete n.state;
      return n;
    });
    setGeoError(null);
  }

  function handleSubmit(e) {
    e.preventDefault();
    const errs = validate(values);
    if (Object.keys(errs).length) {
      setErrors(errs);
      return;
    }
    onSubmit(values);
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
      {/* ── Address type segmented control ── */}
      <div className="flex flex-col gap-2">
        <span className="text-xs font-semibold uppercase tracking-widest text-wmuted">
          Address type
        </span>
        <div role="group" aria-label="Address type" className="flex gap-2">
          {LABELS.map((l) => {
            const meta = LABEL_META[l];
            const LabelIcon = meta.icon;
            return (
              <button
                key={l}
                type="button"
                aria-pressed={values.label === l}
                onClick={() => set('label', l)}
                className={cn(
                  'flex flex-1 items-center justify-center gap-1.5 rounded-xl border py-2.5 text-xs font-semibold transition-colors duration-150',
                  values.label === l
                    ? 'border-wgreen bg-wgreen/10 text-wgreen'
                    : 'border-wline bg-wcard text-wmuted hover:border-wline hover:text-wink',
                )}
              >
                <LabelIcon className="size-3.5" aria-hidden="true" />
                {meta.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Section: Contact ── */}
      <div>
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-wmuted">
          Contact
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Full name"
            placeholder="Recipient's full name"
            value={values.full_name}
            onChange={(e) => set('full_name', e.target.value)}
            error={errors.full_name}
            autoComplete="name"
            required
          />
          <Input
            label="Phone"
            placeholder="+91 98765 43210"
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            value={values.phone}
            onChange={(e) => set('phone', e.target.value)}
            error={errors.phone}
            required
          />
        </div>
      </div>

      {/* ── Section: Address lines ── */}
      <div>
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-wmuted">
          Address
        </p>
        <div className="flex flex-col gap-4">
          <Input
            label="Address line 1"
            placeholder="House / flat no, building, street"
            value={values.line1}
            onChange={(e) => set('line1', e.target.value)}
            error={errors.line1}
            autoComplete="address-line1"
            required
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Address line 2 (optional)"
              placeholder="Apartment, suite, locality"
              value={values.line2}
              onChange={(e) => set('line2', e.target.value)}
              autoComplete="address-line2"
            />
            <Input
              label="Landmark (optional)"
              placeholder="Near X, opposite Y"
              value={values.landmark}
              onChange={(e) => set('landmark', e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* ── Section: Location ── */}
      <div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 gap-y-2">
          <p className="text-xs font-semibold uppercase tracking-widest text-wmuted">
            Location
          </p>
          <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
            <button
              type="button"
              onClick={() => setMapOpen(true)}
              className="inline-flex min-h-[44px] shrink-0 items-center gap-1 text-xs font-medium text-wgreen transition-colors hover:text-wgreen/80"
            >
              <MapPinned className="size-3.5" aria-hidden="true" />
              Pick on map
            </button>
            <button
              type="button"
              disabled={currentLocation.isPending}
              onClick={handleUseLocation}
              className="inline-flex min-h-[44px] shrink-0 items-center gap-1 text-xs font-medium text-wgreen transition-colors hover:text-wgreen/80 disabled:opacity-50"
            >
              {currentLocation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <LocateFixed className="size-3.5" aria-hidden="true" />
              )}
              {currentLocation.isPending ? 'Detecting…' : 'Use my current location'}
            </button>
          </div>
        </div>
        {geoError && (
          <p className="mb-2 text-xs text-red-600">{geoError}</p>
        )}
        <div className="grid gap-4 min-[480px]:grid-cols-3">
          <Input
            label="Pincode"
            placeholder="110001"
            inputMode="numeric"
            maxLength={6}
            value={values.pincode}
            onChange={(e) => handlePincodeChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
            error={errors.pincode}
            autoComplete="postal-code"
            required
          />
          <Input
            label="City"
            placeholder="New Delhi"
            value={values.city}
            onChange={(e) => handleCityChange(e.target.value)}
            error={errors.city}
            autoComplete="address-level2"
            required
          />
          <Input
            label="State"
            placeholder="Delhi"
            value={values.state}
            onChange={(e) => handleStateChange(e.target.value)}
            error={errors.state}
            autoComplete="address-level1"
            required
          />
        </div>
      </div>

      {/* ── Set as default checkbox ── */}
      {showSetDefault && (
        <label className="flex cursor-pointer items-center gap-2.5 rounded-xl border border-wline bg-wpaper px-4 py-3 text-sm transition-colors hover:border-wline">
          <input
            type="checkbox"
            checked={values.is_default}
            onChange={(e) => set('is_default', e.target.checked)}
            className="size-4 rounded-xs border-wline bg-wcard text-wgreen"
          />
          <span className="font-medium text-wink">Set as default delivery address</span>
        </label>
      )}

      {/* ── Footer actions ── */}
      <div className="flex justify-end gap-2 border-t border-wline pt-4">
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        )}
        <Button type="submit" loading={busy}>
          {submitLabel}
        </Button>
      </div>

      {/* ── Map picker — lazy-loaded, only mounts when opened ── */}
      {mapOpen && (
        <Suspense fallback={<div className="flex h-64 items-center justify-center"><Loader2 className="size-6 animate-spin text-wgreen" /></div>}>
          <MapAddressPicker
            open={mapOpen}
            onClose={() => setMapOpen(false)}
            onSelect={handleMapSelect}
          />
        </Suspense>
      )}
    </form>
  );
}
