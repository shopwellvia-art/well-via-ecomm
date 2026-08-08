import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, X, XCircle } from 'lucide-react';
import { motion } from 'framer-motion';
import { useQueryClient } from '@tanstack/react-query';
import { Card } from '@/components/storefront/ui/Card.jsx';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { Textarea } from '@/components/storefront/ui/Textarea.jsx';
import { toast } from '@/components/ui/Toaster.jsx';
import { scaleIn, fadeUp } from '@/lib/motion.js';
import { useCancelOrder } from '@/features/orders/hooks.js';
import { readApiErrorMessage } from '@/features/orders/api.js';
import { formatPrice } from '@/lib/utils.js';

/**
 * Confirm dialog for customer self-service order cancellation.
 *
 * Mirrors the RequestReturnModal pattern (focus management, Escape to close,
 * inline error banner, Card + Button footer). Reason is optional — the
 * backend defaults a blank one to "Cancelled by customer".
 */
export default function CancelOrderModal({ order, onClose }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState(null);
  const cancelOrder = useCancelOrder();
  const qc = useQueryClient();

  // a11y: focus the close button on open, restore the trigger on close.
  const closeButtonRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    closeButtonRef.current?.focus();
    return () => {
      previousFocusRef.current?.focus();
    };
  }, []);

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !cancelOrder.isPending) onClose?.();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [cancelOrder.isPending, onClose]);

  const orderLabel = order.order_number ?? `#${order.id}`;
  // Prepaid + already captured → money will flow back.
  const showRefundNote = order.status === 'paid' && order.payment_method !== 'cod';

  async function confirm() {
    setError(null);
    try {
      await cancelOrder.mutateAsync({ id: order.id, reason });
      toast.success(`Order ${orderLabel} cancelled.`);
      onClose?.();
    } catch (err) {
      const msg = await readApiErrorMessage(err, 'Could not cancel this order.');
      toast.error(msg);
      if (err?.response?.status === 409) {
        // Status moved under us (e.g. just shipped) — refresh and bail out.
        qc.invalidateQueries({ queryKey: ['orders'] });
        qc.invalidateQueries({ queryKey: ['order'] });
        onClose?.();
      } else {
        setError(msg);
      }
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cancel-order-modal-title"
    >
      <motion.div
        variants={scaleIn}
        initial="hidden"
        animate="show"
        className="w-full max-w-md"
      >
        <Card className="bg-wcard overflow-hidden p-0">
          {/* Header */}
          <div className="flex items-center gap-3 border-b border-wline px-6 py-4">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-red-50 text-red-600">
              <XCircle className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id="cancel-order-modal-title"
                className="font-wserif text-h3 text-wink tracking-tight"
              >
                Cancel this order?
              </h2>
              <p className="mt-0.5 text-xs text-wmuted">
                Order {orderLabel} ·{' '}
                {formatPrice(order.total_amount, order.currency)}
              </p>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label="Close"
              disabled={cancelOrder.isPending}
              className="grid size-11 shrink-0 place-items-center rounded-lg text-wmuted transition-colors hover:bg-wpaper hover:text-wink disabled:pointer-events-none"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </div>

          <div className="px-6 py-5">
            <motion.div variants={fadeUp} initial="hidden" animate="show">
              <p className="text-sm leading-relaxed text-wmuted">
                This can&apos;t be undone — the items will be released back to
                stock and the order closed.
                {showRefundNote && (
                  <>
                    {' '}
                    <span className="text-wink">
                      Your refund will be processed to your original payment
                      method.
                    </span>
                  </>
                )}
              </p>
            </motion.div>

            <div className="mt-4">
              <Textarea
                label="Reason (optional)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                maxLength={255}
                placeholder="Tell us why, if you like."
              />
            </div>

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

          {/* Footer */}
          <div className="flex items-center justify-end gap-2.5 border-t border-wline bg-wpaper px-6 py-4">
            <Button
              variant="ghost"
              onClick={onClose}
              disabled={cancelOrder.isPending}
            >
              Keep order
            </Button>
            <Button
              variant="destructive"
              onClick={confirm}
              loading={cancelOrder.isPending}
            >
              <XCircle className="size-4" aria-hidden="true" />
              Cancel order
            </Button>
          </div>
        </Card>
      </motion.div>
    </div>
  );
}
