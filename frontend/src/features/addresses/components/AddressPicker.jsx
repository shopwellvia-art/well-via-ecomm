import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MapPin, Plus, ChevronUp, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';
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
  const { data: addresses, isLoading, isError, refetch } = useAddresses();
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
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-24 rounded-xl" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-red-200 bg-red-50 p-3.5 text-sm text-red-600">
        <div className="flex items-start gap-2.5">
          <MapPin className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Could not load your addresses. Please refresh and try again.
        </div>
        <Button size="sm" variant="outline" className="mt-1 self-start" onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const hasSaved = (addresses?.length ?? 0) > 0;

  return (
    <div className="flex flex-col gap-3">
      {/* Saved address radio cards */}
      {hasSaved && (
        <motion.div
          role="radiogroup"
          aria-label="Select delivery address"
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
                role="radio"
                aria-checked={isSelected}
                variants={fadeUp}
                onClick={() => handleSelectSaved(addr.id)}
                className={cn(
                  'w-full rounded-xl border text-left transition-colors duration-150',
                  isSelected
                    ? 'border-wgreen bg-wgreen/10'
                    : 'border-wline bg-wcard hover:border-wline hover:shadow-sm',
                )}
              >
                <div className="flex items-start gap-3 p-3.5">
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
                    <AddressCard address={addr} compact />
                  </div>
                  {isSelected && (
                    <CheckCircle2
                      className="mt-0.5 size-4 shrink-0 text-wgreen"
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
          'overflow-hidden rounded-xl border transition-colors duration-150',
          addOpen ? 'border-wgreen/40 bg-wcard' : 'border-wline bg-wcard',
        )}
      >
        <button
          type="button"
          onClick={handleAddToggle}
          aria-expanded={addOpen}
          aria-controls="add-address-form"
          className="flex w-full items-center gap-2.5 px-4 py-3 text-sm font-medium text-wmuted transition-colors hover:text-wink"
        >
          <span
            className={cn(
              'grid size-6 shrink-0 place-items-center rounded-md transition-colors',
              addOpen ? 'bg-wgreen/10 text-wgreen' : 'bg-wpaper text-wmuted',
            )}
          >
            {addOpen ? (
              <ChevronUp className="size-3.5" aria-hidden="true" />
            ) : (
              <Plus className="size-3.5" aria-hidden="true" />
            )}
          </span>
          <MapPin className="size-4 text-wmuted" aria-hidden="true" />
          <span className={addOpen ? 'text-wgreen' : ''}>
            {addOpen ? 'Hide new address form' : 'Add a new address'}
          </span>
        </button>

        <AnimatePresence initial={false}>
          {addOpen && (
            <motion.div
              id="add-address-form"
              key="add-form"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: 'easeInOut' }}
              className="overflow-hidden"
            >
              <div className="border-t border-wline px-4 pb-5 pt-4">
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
                <label className="mt-4 flex cursor-pointer items-center gap-2.5 rounded-xl border border-wline bg-wpaper px-4 py-3 text-sm transition-colors hover:border-wline">
                  <input
                    type="checkbox"
                    checked={saveNew}
                    onChange={(e) => setSaveNew(e.target.checked)}
                    className="size-4 rounded-xs border-wline bg-wcard text-wgreen"
                  />
                  <span className="font-medium text-wink">Save to my addresses</span>
                  <span className="ml-auto text-xs text-wmuted">For faster checkout next time</span>
                </label>

                {createAddress.isError && (
                  <p className="mt-2 text-xs text-red-600">
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
