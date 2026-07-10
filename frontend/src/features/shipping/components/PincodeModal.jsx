import { useEffect, useState } from 'react';
import { MapPin } from 'lucide-react';
import { readSavedPincode, saveSavedPincode } from '@/features/shipping/storage.js';

const DISMISS_KEY = 'wellvia.pincode_modal_dismissed';

/**
 * First-visit delivery-pincode capture (mockup: "Add Delivery Address —
 * Pincode → Continue" overlay).
 *
 * Shows once per browser: gated by the persisted pincode (shared with the
 * cart drawer / PDP pincode check via features/shipping/storage.js) and a
 * dismissal flag, so returning visitors are never nagged.
 */
export default function PincodeModal() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');

  useEffect(() => {
    try {
      if (readSavedPincode() || sessionStorage.getItem(DISMISS_KEY)) return;
    } catch {
      return;
    }
    // Small delay so the modal doesn't compete with first paint.
    const t = setTimeout(() => setOpen(true), 1200);
    return () => clearTimeout(t);
  }, []);

  if (!open) return null;

  function dismiss() {
    try {
      sessionStorage.setItem(DISMISS_KEY, '1');
    } catch {
      /* best-effort */
    }
    setOpen(false);
  }

  function submit(e) {
    e.preventDefault();
    if (!/^\d{6}$/.test(value)) return;
    saveSavedPincode(value);
    dismiss();
  }

  return (
    <div className="fixed inset-0 z-[95] flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Skip pincode entry"
        onClick={dismiss}
        className="absolute inset-0 bg-wink/50 animate-dim cursor-default"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="pincode-modal-title"
        className="relative w-full max-w-sm rounded-xl2 bg-wcard border border-wline shadow-xl p-6 animate-rise"
      >
        <button
          type="button"
          onClick={dismiss}
          aria-label="Close"
          className="absolute top-3 right-3 grid size-8 place-items-center rounded-full text-wmuted hover:text-wink bg-transparent border-0 cursor-pointer transition-colors"
        >
          ×
        </button>
        <h2
          id="pincode-modal-title"
          className="font-wserif text-[20px] font-semibold text-wink m-0 mb-1 flex items-center gap-2"
        >
          <MapPin className="size-5 text-wgreen" aria-hidden="true" />
          Add Delivery Pincode
        </h2>
        <p className="text-[13px] text-wmuted m-0 mb-4">
          Check delivery availability and dates for your area.
        </p>
        <form onSubmit={submit} className="flex gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value.replace(/\D/g, '').slice(0, 6))}
            placeholder="Pincode"
            inputMode="numeric"
            autoFocus
            aria-label="Pincode"
            className="flex-1 min-w-0 rounded-xl border border-wline bg-wpaper px-4 py-3 text-[14px] text-wink placeholder:text-wmuted"
          />
          <button
            type="submit"
            disabled={value.length !== 6}
            className="rounded-xl bg-wgreen text-white text-[13.5px] px-5 py-3 border-0 cursor-pointer hover:bg-wgreen-dark disabled:opacity-50 transition-colors"
          >
            Continue
          </button>
        </form>
      </div>
    </div>
  );
}
