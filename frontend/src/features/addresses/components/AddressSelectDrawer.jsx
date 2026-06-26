import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Plus, MapPin, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { useAddresses, useCreateAddress } from '../hooks.js';
import AddressForm from './AddressForm.jsx';

const LABEL_TEXT = { home: 'Home', work: 'Work', other: 'Other' };

/** Returns all focusable elements inside a container. */
function getFocusable(container) {
  if (!container) return [];
  return Array.from(
    container.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

/**
 * Right-side "Select delivery address" drawer (Flipkart-style).
 *
 * Lists the customer's saved addresses as radio rows; tapping one calls
 * `onSelect(address)` and closes. "+ Add New" swaps the list for an inline
 * AddressForm — the created address is auto-selected.
 *
 * Props
 *   open       – controls visibility
 *   onClose    – backdrop / X / Escape
 *   selectedId – id of the currently selected address (highlighted)
 *   onSelect   – called with the full AddressRead object
 */
export default function AddressSelectDrawer({ open, onClose, selectedId, onSelect }) {
  const { data: addresses, isLoading, isError } = useAddresses();
  const createAddress = useCreateAddress();
  const [adding, setAdding] = useState(false);

  // Refs for focus management
  const panelRef = useRef(null);
  const closeBtnRef = useRef(null);
  const triggerRef = useRef(null); // stores the element that opened the drawer

  // Open straight onto the form when there's nothing saved yet.
  useEffect(() => {
    if (open && addresses && addresses.length === 0) setAdding(true);
  }, [open, addresses]);

  // Escape key handler
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') onClose?.();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Focus management: capture trigger, move focus into drawer on open, restore on close
  useEffect(() => {
    if (open) {
      // Store the element that triggered the open so we can restore focus on close
      triggerRef.current = document.activeElement;
      // Move focus into the drawer after the animation frame so the panel is rendered
      requestAnimationFrame(() => {
        closeBtnRef.current?.focus();
      });
    } else {
      // Restore focus to the trigger element when the drawer closes
      if (triggerRef.current && typeof triggerRef.current.focus === 'function') {
        triggerRef.current.focus();
        triggerRef.current = null;
      }
    }
  }, [open]);

  // Focus trap: confine Tab / Shift+Tab within the panel while it is open
  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(e) {
      if (e.key !== 'Tab') return;
      const focusable = getFocusable(panelRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  function handleCreate(values) {
    createAddress.mutate(values, {
      onSuccess: (saved) => {
        setAdding(false);
        onSelect?.(saved);
        onClose?.();
      },
    });
  }

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          {/* Backdrop */}
          <motion.button
            type="button"
            aria-label="Close address selector"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 h-full w-full cursor-default bg-black/50"
          />

          {/* Panel */}
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="drawer-title"
            tabIndex={-1}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'tween', duration: 0.25, ease: 'easeOut' }}
            className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-wcard shadow-xl outline-none"
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-wline px-5 py-4">
              <h2 id="drawer-title" className="font-wserif text-sm font-semibold text-wink">
                Select delivery address
              </h2>
              <div className="flex items-center gap-2">
                {!adding && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setAdding(true)}
                    className="text-wgreen hover:text-wgreen"
                  >
                    <Plus className="size-3.5" aria-hidden="true" />
                    Add New
                  </Button>
                )}
                <button
                  ref={closeBtnRef}
                  type="button"
                  aria-label="Close"
                  onClick={onClose}
                  className="grid size-8 place-items-center rounded-md text-wmuted transition-colors hover:bg-wpaper hover:text-wink"
                >
                  <X className="size-4" />
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto p-4">
              {adding ? (
                <div className="pb-[env(safe-area-inset-bottom,0)]">
                  <AddressForm
                    onSubmit={handleCreate}
                    onCancel={
                      (addresses?.length ?? 0) > 0 ? () => setAdding(false) : undefined
                    }
                    submitLabel="Save & deliver here"
                    busy={createAddress.isPending}
                    showSetDefault={false}
                  />
                  {createAddress.isError && (
                    <p className="mt-2 text-xs text-red-600">
                      {createAddress.error?.response?.data?.error?.message ||
                        'Could not save the address. Please check the details and try again.'}
                    </p>
                  )}
                </div>
              ) : isError ? (
                <div className="p-4 text-sm text-red-600">
                  Could not load addresses.{' '}
                  <button
                    type="button"
                    onClick={() => window.location.reload()}
                    className="font-semibold underline underline-offset-2"
                  >
                    Retry
                  </button>
                </div>
              ) : isLoading ? (
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-20 rounded-xl" />
                  <Skeleton className="h-20 rounded-xl" />
                </div>
              ) : (
                <>
                  <p className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-wmuted">
                    Saved addresses
                  </p>
                  <ul
                    role="radiogroup"
                    aria-label="Saved addresses"
                    className="flex flex-col gap-2"
                  >
                    {(addresses ?? []).map((addr) => {
                      const isSelected = selectedId === addr.id;
                      return (
                        <li key={addr.id}>
                          <button
                            type="button"
                            role="radio"
                            aria-checked={isSelected}
                            onClick={() => {
                              onSelect?.(addr);
                              onClose?.();
                            }}
                            className={cn(
                              'w-full rounded-xl border p-3.5 text-left transition-colors duration-150',
                              isSelected
                                ? 'border-wgreen bg-wgreen/10'
                                : 'border-wline bg-wcard hover:border-wline hover:shadow-sm',
                            )}
                          >
                            <div className="flex items-start gap-3">
                              {/* Radio dot */}
                              <span
                                className={cn(
                                  'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
                                  isSelected
                                    ? 'border-wgreen bg-wgreen'
                                    : 'border-wline bg-transparent',
                                )}
                                aria-hidden="true"
                              >
                                {isSelected && (
                                  <span className="size-1.5 rounded-full bg-white" />
                                )}
                              </span>
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="min-w-0 truncate text-sm font-semibold text-wink">
                                    {addr.full_name}
                                  </span>
                                  <span className="rounded-md bg-wpaper px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-wmuted">
                                    {LABEL_TEXT[String(addr.label).toLowerCase()] || 'Other'}
                                  </span>
                                  <span className="text-xs font-semibold text-wink nums">
                                    {addr.pincode}
                                  </span>
                                  {isSelected && (
                                    <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-semibold text-wgreen">
                                      <CheckCircle2 className="size-3" aria-hidden="true" />
                                      Selected
                                    </span>
                                  )}
                                </div>
                                <p className="mt-1 line-clamp-2 text-xs text-wmuted">
                                  {addr.line1}
                                  {addr.line2 ? `, ${addr.line2}` : ''}
                                  {addr.landmark ? ` (${addr.landmark})` : ''}, {addr.city},{' '}
                                  {addr.state}
                                </p>
                                <p className="mt-0.5 text-[11px] text-wmuted nums">
                                  {addr.phone}
                                </p>
                              </div>
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>

                  {(addresses?.length ?? 0) === 0 && (
                    <div className="mt-6 flex flex-col items-center gap-2 text-center">
                      <MapPin className="size-6 text-wmuted" aria-hidden="true" />
                      <p className="text-sm text-wmuted">
                        No saved addresses yet.
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
