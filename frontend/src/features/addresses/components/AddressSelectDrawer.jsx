import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Plus, MapPin, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { useAddresses, useCreateAddress } from '../hooks.js';
import AddressForm from './AddressForm.jsx';

const LABEL_TEXT = { home: 'Home', work: 'Work', other: 'Other' };

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
  const { data: addresses, isLoading } = useAddresses();
  const createAddress = useCreateAddress();
  const [adding, setAdding] = useState(false);

  // Open straight onto the form when there's nothing saved yet.
  useEffect(() => {
    if (open && addresses && addresses.length === 0) setAdding(true);
  }, [open, addresses]);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') onClose?.();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

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
            role="dialog"
            aria-modal="true"
            aria-label="Select delivery address"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'tween', duration: 0.25, ease: 'easeOut' }}
            className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-bg-elevated shadow-xl"
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
              <h2 className="text-sm font-semibold text-ink-primary">
                Select delivery address
              </h2>
              <div className="flex items-center gap-2">
                {!adding && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setAdding(true)}
                    className="text-accent hover:text-accent"
                  >
                    <Plus className="size-3.5" aria-hidden="true" />
                    Add New
                  </Button>
                )}
                <button
                  type="button"
                  aria-label="Close"
                  onClick={onClose}
                  className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
                >
                  <X className="size-4" />
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto p-4">
              {adding ? (
                <div>
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
                    <p className="mt-2 text-xs text-danger">
                      {createAddress.error?.response?.data?.error?.message ||
                        'Could not save the address. Please check the details and try again.'}
                    </p>
                  )}
                </div>
              ) : isLoading ? (
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-20 rounded-lg" />
                  <Skeleton className="h-20 rounded-lg" />
                </div>
              ) : (
                <>
                  <p className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
                    Saved addresses
                  </p>
                  <ul className="flex flex-col gap-2">
                    {(addresses ?? []).map((addr) => {
                      const isSelected = selectedId === addr.id;
                      return (
                        <li key={addr.id}>
                          <button
                            type="button"
                            onClick={() => {
                              onSelect?.(addr);
                              onClose?.();
                            }}
                            className={cn(
                              'w-full rounded-sm border p-3.5 text-left transition-colors duration-150',
                              isSelected
                                ? 'border-accent bg-accent/8'
                                : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm',
                            )}
                          >
                            <div className="flex items-start gap-3">
                              {/* Radio dot */}
                              <span
                                className={cn(
                                  'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
                                  isSelected
                                    ? 'border-accent bg-accent'
                                    : 'border-line-strong bg-transparent',
                                )}
                                aria-hidden="true"
                              >
                                {isSelected && (
                                  <span className="size-1.5 rounded-full bg-white" />
                                )}
                              </span>
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-sm font-semibold text-ink-primary">
                                    {addr.full_name}
                                  </span>
                                  <span className="rounded-sm bg-fill px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-secondary">
                                    {LABEL_TEXT[String(addr.label).toLowerCase()] || 'Other'}
                                  </span>
                                  <span className="text-xs font-semibold text-ink-primary nums">
                                    {addr.pincode}
                                  </span>
                                  {isSelected && (
                                    <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-semibold text-accent">
                                      <CheckCircle2 className="size-3" aria-hidden="true" />
                                      Selected
                                    </span>
                                  )}
                                </div>
                                <p className="mt-1 line-clamp-2 text-xs text-ink-secondary">
                                  {addr.line1}
                                  {addr.line2 ? `, ${addr.line2}` : ''}
                                  {addr.landmark ? ` (${addr.landmark})` : ''}, {addr.city},{' '}
                                  {addr.state}
                                </p>
                                <p className="mt-0.5 text-[11px] text-ink-tertiary nums">
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
                      <MapPin className="size-6 text-ink-tertiary" aria-hidden="true" />
                      <p className="text-sm text-ink-secondary">
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
