import { useEffect, useRef, useState } from 'react';
import { Home, Briefcase, MapPin } from 'lucide-react';
import { Input } from '@/components/ui/Input.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { cn } from '@/lib/utils.js';
import { usePincodeLookup } from '../hooks.js';

const LABELS = ['HOME', 'WORK', 'OTHER'];

const LABEL_META = {
  HOME:  { icon: Home,      label: 'Home'  },
  WORK:  { icon: Briefcase, label: 'Work'  },
  OTHER: { icon: MapPin,    label: 'Other' },
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
  label: 'HOME',
  is_default: false,
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
  const [values, setValues] = useState(() => ({ ...EMPTY, ...initialValues }));
  const [errors, setErrors] = useState({});
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
    set('pincode', val);
  }

  function handleCityChange(val) {
    userEditedCity.current = true;
    set('city', val);
  }

  function handleStateChange(val) {
    userEditedState.current = true;
    set('state', val);
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
        <span className="text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
          Address type
        </span>
        <div className="flex gap-2">
          {LABELS.map((l) => {
            const meta = LABEL_META[l];
            const LabelIcon = meta.icon;
            return (
              <button
                key={l}
                type="button"
                onClick={() => set('label', l)}
                className={cn(
                  'flex flex-1 items-center justify-center gap-1.5 rounded-lg border py-2.5 text-xs font-semibold transition-all duration-150 focus-visible:focus-ring',
                  values.label === l
                    ? 'border-accent bg-accent/12 text-accent shadow-glow-sm'
                    : 'border-line-subtle bg-bg-elevated text-ink-secondary hover:border-line-strong hover:text-ink-primary',
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
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
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
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
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
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
          Location
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
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
        <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 text-sm transition-colors hover:border-line-strong">
          <input
            type="checkbox"
            checked={values.is_default}
            onChange={(e) => set('is_default', e.target.checked)}
            className="size-4 rounded border-line-subtle bg-bg-elevated text-accent"
          />
          <span className="font-medium text-ink-primary">Set as default delivery address</span>
        </label>
      )}

      {/* ── Footer actions ── */}
      <div className="flex justify-end gap-2 border-t border-line-subtle pt-4">
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        )}
        <Button type="submit" loading={busy}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
