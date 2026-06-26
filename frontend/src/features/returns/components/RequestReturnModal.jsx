import { useState, useEffect, useRef } from 'react';
import { Undo2, AlertTriangle, Check, X } from 'lucide-react';
import { motion } from 'framer-motion';
import { Card } from '@/components/storefront/ui/Card.jsx';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { Textarea } from '@/components/storefront/ui/Textarea.jsx';
import { scaleIn, fadeUp } from '@/lib/motion.js';
import { useCreateReturn } from '@/features/returns/hooks.js';

// Mirrors backend ReturnReason. Order chosen for likelihood of use.
const REASONS = [
  { value: 'defective', label: 'Item is defective' },
  { value: 'arrived_damaged', label: 'Arrived damaged' },
  { value: 'wrong_item', label: 'Wrong item received' },
  { value: 'not_as_described', label: 'Not as described' },
  { value: 'no_longer_needed', label: 'No longer needed' },
  { value: 'other', label: 'Other' },
];

/**
 * Modal for the customer to request a return on a delivered order.
 *
 * `order` carries items + their per-line quantity. We let the customer
 * pick a return quantity (0..line.quantity) per item; lines with 0 are
 * omitted from the payload.
 */
export default function RequestReturnModal({ order, onClose, onCreated }) {
  // qty-by-order_item_id
  const [qtys, setQtys] = useState({});
  const [reason, setReason] = useState('defective');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState(null);
  const create = useCreateReturn();

  // a11y: ref to the close button so we can move focus here on open
  const closeButtonRef = useRef(null);
  // a11y: remember what had focus before the modal opened so we can restore it on close
  const previousFocusRef = useRef(null);

  // Focus management: capture trigger, move focus into dialog on mount, restore on unmount
  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    closeButtonRef.current?.focus();
    return () => {
      previousFocusRef.current?.focus();
    };
  }, []);

  // Keyboard: close on Escape while not submitting
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !create.isPending) onClose?.();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [create.isPending, onClose]);

  function setQty(itemId, value) {
    setQtys((q) => ({ ...q, [itemId]: Math.max(0, value) }));
  }

  async function submit() {
    setError(null);
    const items = Object.entries(qtys)
      .map(([id, qty]) => ({ order_item_id: Number(id), quantity: Number(qty) }))
      .filter((i) => i.quantity > 0);
    if (items.length === 0) {
      setError('Pick at least one unit to return.');
      return;
    }
    try {
      const created = await create.mutateAsync({
        order_id: order.id,
        items,
        reason,
        customer_notes: notes.trim() || null,
      });
      onCreated?.(created);
      onClose?.();
    } catch (err) {
      setError(
        err.response?.data?.error?.message || 'Could not submit return.',
      );
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="return-modal-title"
    >
      <motion.div
        variants={scaleIn}
        initial="hidden"
        animate="show"
        className="w-full max-w-lg"
      >
        <Card className="bg-wcard overflow-hidden p-0">
          {/* Modal header */}
          <div className="flex items-center gap-3 border-b border-wline px-6 py-4">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-wgreen/10 text-wgreen">
              <Undo2 className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id="return-modal-title"
                className="font-wserif text-h3 text-wink tracking-tight"
              >
                Request a return
              </h2>
              <p className="mt-0.5 text-xs text-wmuted">
                Order #{order.id} — we&apos;ll arrange pickup and refund once items
                arrive back.
              </p>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label="Close"
              disabled={create.isPending}
              className="grid size-11 shrink-0 place-items-center rounded-lg text-wmuted transition-colors hover:bg-wpaper hover:text-wink disabled:pointer-events-none"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </div>

          <div className="max-h-[calc(100svh-12rem)] overflow-y-auto px-6 py-5">
            {/* Items section */}
            <motion.div variants={fadeUp} initial="hidden" animate="show">
              <p className="text-xs font-semibold uppercase tracking-widest text-wmuted">
                Select items to return
              </p>
              <ul className="mt-2.5 flex flex-col gap-2">
                {order.items.map((it) => (
                  <li
                    key={it.id}
                    className="flex items-center gap-3 rounded-xl border border-wline bg-wpaper px-3.5 py-2.5 transition-colors hover:border-wline"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-wink">
                        {it.name || `Product #${it.product_id}`}
                      </p>
                      <p className="mt-0.5 text-[11px] text-wmuted">
                        Qty ordered:{' '}
                        <span className="nums font-semibold text-wmuted">
                          {it.quantity}
                        </span>
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-0.5">
                      <label
                        htmlFor={`qty-${it.id}`}
                        className="text-[10px] uppercase tracking-wide text-wmuted"
                      >
                        Return qty
                      </label>
                      <input
                        id={`qty-${it.id}`}
                        type="number"
                        min={0}
                        max={it.quantity}
                        step={1}
                        value={qtys[it.id] ?? 0}
                        onChange={(e) => setQty(it.id, Number(e.target.value))}
                        className="w-20 rounded-xl border border-wline bg-wcard px-2.5 py-2 text-right text-sm text-wink transition-colors hover:border-wline focus:border-wgreen focus:outline-none nums"
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </motion.div>

            {/* Return reason section */}
            <div className="mt-5">
              <p className="text-xs font-semibold uppercase tracking-widest text-wmuted">
                Reason for return
              </p>
              <div className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                {REASONS.map((r) => {
                  const selected = reason === r.value;
                  return (
                    <label
                      key={r.value}
                      className={[
                        'flex cursor-pointer items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm transition-colors',
                        selected
                          ? 'border-wgreen/40 bg-wgreen/10 text-wink'
                          : 'border-wline bg-wpaper text-wmuted hover:border-wline hover:text-wink',
                      ].join(' ')}
                    >
                      <input
                        type="radio"
                        name="reason"
                        value={r.value}
                        checked={selected}
                        onChange={(e) => setReason(e.target.value)}
                        className="accent-[#183A2E] shrink-0"
                      />
                      {r.label}
                    </label>
                  );
                })}
              </div>
            </div>

            {/* Notes section */}
            <div className="mt-5">
              <Textarea
                label="Additional notes (optional)"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="A few words help us help you faster."
              />
            </div>

            {/* Error banner */}
            {error && (
              <motion.div
                variants={fadeUp}
                initial="hidden"
                animate="show"
                className="mt-4 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3.5 py-3 text-sm text-red-600"
              >
                <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>{error}</span>
              </motion.div>
            )}
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-end gap-2.5 border-t border-wline bg-wpaper px-6 py-4">
            <Button
              variant="ghost"
              onClick={onClose}
              disabled={create.isPending}
            >
              Cancel
            </Button>
            <Button onClick={submit} loading={create.isPending}>
              <Check className="size-4" aria-hidden="true" />
              Submit return
            </Button>
          </div>
        </Card>
      </motion.div>
    </div>
  );
}
