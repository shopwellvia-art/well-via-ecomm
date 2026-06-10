import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MapPin, Plus, ChevronDown, ChevronUp, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { scaleIn, listStagger, fadeUp } from '@/lib/motion.js';
import { useAddresses, useCreateAddress } from '../hooks.js';
import AddressCard from './AddressCard.jsx';
import AddressForm from './AddressForm.jsx';

/**
 * Checkout address picker.
 *
 * Shows the customer's saved addresses as radio cards (default pre-selected).
 * An "+ Add new address" section lets them fill an inline form with an
 * optional "Save to my addresses" checkbox.
 *
 * onChange payload:
 *   Saved selection:  { address_id: number, pincode: string, phone: string }
 *   Inline new:       { address: AddressCreate, save_address: bool, pincode: string, phone: string }
 *   Incomplete / none: { pincode: '', phone: '' }  (during editing)
 */
export default function AddressPicker({ onChange }) {
  const { data: addresses, isLoading, isError } = useAddresses();
  const createAddress = useCreateAddress();

  // Which saved address is selected (null = none / adding new)
  const [selectedId, setSelectedId] = useState(null);
  // Whether the "Add new" expander is open
  const [addOpen, setAddOpen] = useState(false);
  // The "save to my addresses" checkbox on the inline form
  const [saveNew, setSaveNew] = useState(true);

  // Pre-select the default address (or first) once the list loads.
  useEffect(() => {
    if (!addresses) return;
    if (addresses.length === 0) {
      setAddOpen(true);
      setSelectedId(null);
      return;
    }
    if (selectedId === null) {
      const def = addresses.find((a) => a.is_default) || addresses[0];
      setSelectedId(def.id);
    }
  }, [addresses, selectedId]);

  // Notify parent whenever selection or open state changes.
  useEffect(() => {
    if (!addresses) {
      onChange?.({ pincode: '', phone: '' });
      return;
    }

    if (selectedId !== null) {
      const addr = addresses.find((a) => a.id === selectedId);
      if (addr) {
        onChange?.({ address_id: addr.id, pincode: addr.pincode, phone: addr.phone });
      }
    } else {
      onChange?.({ pincode: '', phone: '' });
    }
    // When addOpen and the form is submitted, handleNewSubmit drives the final payload.
  }, [selectedId, addresses, addOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  function handleSelectSaved(id) {
    setSelectedId(id);
    setAddOpen(false);
  }

  function handleAddToggle() {
    setAddOpen((v) => !v);
    if (!addOpen) {
      setSelectedId(null);
    } else {
      if (addresses?.length) {
        const def = addresses.find((a) => a.is_default) || addresses[0];
        setSelectedId(def.id);
      }
    }
  }

  function handleNewSubmit(values) {
    if (saveNew) {
      createAddress.mutate(values, {
        onSuccess: (saved) => {
          setAddOpen(false);
          setSelectedId(saved.id);
          onChange?.({ address_id: saved.id, pincode: saved.pincode, phone: saved.phone });
        },
        onError: () => {
          onChange?.({
            address: values,
            save_address: false,
            pincode: values.pincode,
            phone: values.phone,
          });
        },
      });
    } else {
      onChange?.({
        address: values,
        save_address: false,
        pincode: values.pincode,
        phone: values.phone,
      });
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-24 rounded-lg" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-danger/30 bg-danger/8 p-3.5 text-sm text-danger">
        <MapPin className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        Could not load your addresses. Please refresh and try again.
      </div>
    );
  }

  const hasSaved = (addresses?.length ?? 0) > 0;

  return (
    <div className="flex flex-col gap-3">
      {/* Saved address radio cards */}
      {hasSaved && (
        <motion.div
          variants={listStagger(0.05)}
          initial="hidden"
          animate="show"
          className="flex flex-col gap-2"
        >
          {addresses.map((addr) => {
            const isSelected = selectedId === addr.id;
            return (
              <motion.button
                key={addr.id}
                type="button"
                variants={fadeUp}
                onClick={() => handleSelectSaved(addr.id)}
                className={cn(
                  'w-full rounded-lg border text-left transition-all duration-200',
                  isSelected
                    ? 'border-accent bg-accent/12 shadow-glow-sm'
                    : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:shadow-sm',
                )}
              >
                <div className="flex items-start gap-3 p-3.5">
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
                    <AddressCard address={addr} compact />
                  </div>
                  {isSelected && (
                    <CheckCircle2
                      className="mt-0.5 size-4 shrink-0 text-accent"
                      aria-hidden="true"
                    />
                  )}
                </div>
              </motion.button>
            );
          })}
        </motion.div>
      )}

      {/* Add new address expander */}
      <div
        className={cn(
          'overflow-hidden rounded-lg border transition-colors duration-200',
          addOpen ? 'border-accent/40 bg-bg-elevated' : 'border-line-subtle bg-bg-elevated',
        )}
      >
        <button
          type="button"
          onClick={handleAddToggle}
          className="flex w-full items-center gap-2.5 px-4 py-3 text-sm font-medium text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring"
        >
          <span
            className={cn(
              'grid size-6 shrink-0 place-items-center rounded-md transition-colors',
              addOpen ? 'bg-accent/12 text-accent' : 'bg-fill text-ink-tertiary',
            )}
          >
            {addOpen ? (
              <ChevronUp className="size-3.5" aria-hidden="true" />
            ) : (
              <Plus className="size-3.5" aria-hidden="true" />
            )}
          </span>
          <MapPin className="size-4 text-ink-tertiary" aria-hidden="true" />
          <span className={addOpen ? 'text-accent' : ''}>
            {addOpen ? 'Hide new address form' : 'Add a new address'}
          </span>
        </button>

        <AnimatePresence initial={false}>
          {addOpen && (
            <motion.div
              key="add-form"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: 'easeInOut' }}
              className="overflow-hidden"
            >
              <div className="border-t border-line-subtle px-4 pb-5 pt-4">
                <motion.div variants={scaleIn} initial="hidden" animate="show">
                  <AddressForm
                    onSubmit={handleNewSubmit}
                    onCancel={hasSaved ? handleAddToggle : undefined}
                    submitLabel={saveNew ? 'Save & use this address' : 'Use this address'}
                    busy={createAddress.isPending}
                    showSetDefault={false}
                  />
                </motion.div>

                {/* "Save to my addresses" checkbox */}
                <label className="mt-4 flex cursor-pointer items-center gap-2.5 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 text-sm transition-colors hover:border-line-strong">
                  <input
                    type="checkbox"
                    checked={saveNew}
                    onChange={(e) => setSaveNew(e.target.checked)}
                    className="size-4 rounded border-line-subtle bg-bg-elevated text-accent"
                  />
                  <span className="font-medium text-ink-primary">Save to my addresses</span>
                  <span className="ml-auto text-xs text-ink-tertiary">For faster checkout next time</span>
                </label>

                {createAddress.isError && (
                  <p className="mt-2 text-xs text-danger">
                    Could not save the address — it will still be used for this order.
                  </p>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
