import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MapPin, Check, AlertTriangle, Loader2 } from 'lucide-react';
import { Input } from '@/components/storefront/ui/Input.jsx';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { useServiceability } from '@/features/shipping/hooks.js';
import { readSavedPincode, saveSavedPincode } from '@/features/shipping/storage.js';
import { fadeUp } from '@/lib/motion.js';

/**
 * Pincode serviceability widget for the cart / checkout page.
 *
 * Behavior:
 * - The submitted pincode (not the live input) drives the query, so the
 *   carrier isn't hit on every keystroke.
 * - On a successful check we store the pincode in localStorage and call
 *   `onResult(result)` so the parent can disable checkout when the area
 *   isn't serviceable.
 */
export default function PincodeCheck({ onResult }) {
  const [draft, setDraft] = useState(readSavedPincode);
  const [submitted, setSubmitted] = useState(readSavedPincode);
  const [error, setError] = useState(null);
  const { data, isFetching, isError, error: queryError } =
    useServiceability(submitted);

  // Bubble the result up so the parent can gate the checkout button.
  useEffect(() => {
    if (data && onResult) onResult(data);
  }, [data, onResult]);

  function handleCheck(e) {
    e.preventDefault();
    setError(null);
    const clean = draft.trim();
    if (!/^\d{6}$/.test(clean)) {
      setError('Enter a 6-digit Indian pincode.');
      return;
    }
    saveSavedPincode(clean);
    setSubmitted(clean);
  }

  const result = data;
  const showServerError = isError && submitted;
  const serverMessage =
    queryError?.response?.data?.error?.message ||
    'Could not check that pincode right now.';

  return (
    <div className="mt-5 overflow-hidden rounded-xl border border-wline bg-wpaper">
      {/* Header */}
      <div className="flex items-center gap-1.5 border-b border-wline px-3 py-2.5 text-xs font-semibold uppercase tracking-wide text-wmuted">
        <MapPin className="size-3.5" aria-hidden="true" />
        Check delivery
      </div>

      {/* Input form */}
      <form onSubmit={handleCheck} className="flex items-start gap-2 p-3">
        <div className="flex-1">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').slice(0, 6))}
            placeholder="6-digit pincode"
            inputMode="numeric"
            autoComplete="postal-code"
            error={error}
            aria-label="Delivery pincode"
          />
        </div>
        <Button
          type="submit"
          variant="secondary"
          size="sm"
          loading={isFetching}
          className="mt-0.5 shrink-0"
        >
          Check
        </Button>
      </form>

      {/* Result */}
      <AnimatePresence mode="wait">
        {isFetching && !result && (
          <motion.p
            key="checking"
            variants={fadeUp}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="flex items-center gap-1.5 px-3 pb-3 text-xs text-wmuted"
          >
            <Loader2 className="size-3 animate-spin" aria-hidden="true" />
            Checking…
          </motion.p>
        )}

        {showServerError && (
          <motion.p
            key="server-error"
            variants={fadeUp}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="flex items-start gap-1.5 px-3 pb-3 text-xs text-red-600"
          >
            <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
            {serverMessage}
          </motion.p>
        )}

        {result && result.serviceable && (
          <motion.div
            key="serviceable"
            variants={fadeUp}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="flex items-start gap-1.5 px-3 pb-3 text-xs"
          >
            <Check className="mt-0.5 size-3.5 shrink-0 text-wgreen" aria-hidden="true" />
            <span className="text-wmuted">
              <span className="font-medium text-wgreen">Delivers to {result.pincode}.</span>
              {result.eta_days_min && (
                <>
                  {' '}Arrives in{' '}
                  <span className="nums">{result.eta_days_min}</span>
                  {result.eta_days_max && result.eta_days_max !== result.eta_days_min
                    ? <><span className="nums">–{result.eta_days_max}</span></>
                    : ''}{' '}
                  days.
                </>
              )}
              {result.cod_available && (
                <span className="ml-1 rounded-md bg-wgreen/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-wgreen">
                  COD
                </span>
              )}
            </span>
          </motion.div>
        )}

        {result && !result.serviceable && (
          <motion.p
            key="not-serviceable"
            variants={fadeUp}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="flex items-start gap-1.5 px-3 pb-3 text-xs text-red-600"
          >
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              <span className="font-medium">
                Sorry — we don&apos;t deliver to {result.pincode}.
              </span>{' '}
              {result.remark}
            </span>
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}
