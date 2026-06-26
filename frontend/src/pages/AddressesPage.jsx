import { useState } from 'react';
import { Link } from 'react-router-dom';
import AccountLayout from '@/components/storefront/AccountLayout';
import { CloseIcon } from '@/components/storefront/Icons';
import { useAuthStore } from '@/features/auth/store.js';
import {
  useAddresses,
  useCreateAddress,
  useUpdateAddress,
  useDeleteAddress,
  useSetDefaultAddress,
} from '@/features/addresses/hooks.js';
import AddressForm from '@/features/addresses/components/AddressForm.jsx';
import { fadeUp, staggerContainer, scaleIn } from '@/lib/motion.js';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

// Displayable label text for each label key the API returns.
const LABEL_DISPLAY = {
  home: 'Home',
  work: 'Work',
  other: 'Other',
};

// Inline SVG pin — no MapPin in the storefront Icons set.
function PinIcon({ size = 44, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  );
}

export default function AddressesPage() {
  const user = useAuthStore((s) => s.user);

  const { data: addresses, isLoading, isError, refetch } = useAddresses();
  const createAddress = useCreateAddress();
  const updateAddress = useUpdateAddress();
  const deleteAddress = useDeleteAddress();
  const setDefaultAddress = useSetDefaultAddress();

  // 'create' | { id } for edit | null
  const [formMode, setFormMode] = useState(null);
  // id of address currently being deleted (for per-card loading state)
  const [deletingId, setDeletingId] = useState(null);
  // id of address pending inline delete confirmation
  const [confirmingDeleteId, setConfirmingDeleteId] = useState(null);
  // inline error message for address form
  const [formError, setFormError] = useState(null);
  // inline error from a failed delete mutation
  const [deleteError, setDeleteError] = useState(null);

  // ── Not signed-in guard ────────────────────────────────────────────────────
  if (!user) {
    return (
      <AccountLayout active="addresses">
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <PinIcon size={48} className="text-wmuted mb-4" />
          <h2 className="font-wserif text-2xl text-wink mb-2">
            Sign in to manage addresses
          </h2>
          <p className="text-wmuted text-sm mb-6">
            Save delivery addresses for faster checkout.
          </p>
          <Link
            to="/login?next=/account/addresses"
            className="bg-wgreen text-white rounded-full px-6 py-3 text-sm hover:bg-wgreen-dark transition-colors no-underline"
          >
            Sign in
          </Link>
        </div>
      </AccountLayout>
    );
  }

  // ── Handlers (all original logic preserved) ────────────────────────────────

  function handleCreate(values) {
    setFormError(null);
    createAddress.mutate(values, {
      onSuccess: () => setFormMode(null),
      onError: (err) => {
        setFormError(
          err?.response?.data?.error?.message || 'Could not save address. Please try again.',
        );
      },
    });
  }

  function handleUpdate(values) {
    if (!formMode?.id) return;
    setFormError(null);
    updateAddress.mutate(
      { id: formMode.id, data: values },
      {
        onSuccess: () => setFormMode(null),
        onError: (err) => {
          setFormError(
            err?.response?.data?.error?.message || 'Could not update address. Please try again.',
          );
        },
      },
    );
  }

  // First click: show inline confirmation prompt on that card.
  // Second click (confirmed): run the mutation.
  function handleDelete(id) {
    if (confirmingDeleteId !== id) {
      setConfirmingDeleteId(id);
      setDeleteError(null);
      return;
    }
    // User confirmed — proceed with deletion.
    setConfirmingDeleteId(null);
    setDeletingId(id);
    setDeleteError(null);
    deleteAddress.mutate(id, {
      onSettled: () => setDeletingId(null),
      onError: (err) => {
        setDeleteError(
          err?.response?.data?.error?.message || 'Could not delete address. Please try again.',
        );
      },
    });
  }

  function handleSetDefault(id) {
    setDefaultAddress.mutate(id);
  }

  const editingAddress =
    formMode && formMode !== 'create'
      ? addresses?.find((a) => a.id === formMode.id)
      : null;

  // ── Page render ────────────────────────────────────────────────────────────
  return (
    <AccountLayout active="addresses">
      {/* ── Page header ── */}
      <div className="flex flex-wrap items-end justify-between gap-3 mb-7">
        <div>
          <h1 className="font-wserif font-medium text-[clamp(26px,3vw,38px)] text-wink mb-1 leading-tight">
            Addresses
          </h1>
          <p className="text-[14px] text-wmuted font-light">
            Saved delivery locations — pick one at checkout.
          </p>
        </div>

        {formMode === null && (
          <button
            type="button"
            onClick={() => {
              setFormMode('create');
              setFormError(null);
            }}
            className="bg-wgreen text-white border-0 rounded-full px-6 py-3 text-[13.5px] cursor-pointer hover:bg-wgreen-dark transition-colors"
          >
            + Add Address
          </button>
        )}
      </div>

      <motion.div
        variants={staggerContainer(0.06)}
        initial="hidden"
        animate="show"
      >
        {/* ── Add / Edit form ── */}
        {formMode !== null && (
          <motion.div variants={scaleIn} className="mb-6">
            <div className="bg-wcard border border-wline rounded-xl2 overflow-hidden">
              {/* Form header */}
              <div className="flex items-center justify-between border-b border-wline bg-wpaper px-5 py-4">
                <h2 className="font-wserif text-[18px] text-wink leading-tight">
                  {formMode === 'create' ? 'Add New Address' : 'Edit Address'}
                </h2>
                <button
                  type="button"
                  aria-label="Close form"
                  onClick={() => {
                    setFormMode(null);
                    setFormError(null);
                  }}
                  className="w-8 h-8 flex items-center justify-center rounded-full text-wmuted bg-transparent border-0 cursor-pointer hover:bg-wline hover:text-wink transition-colors"
                >
                  <CloseIcon size={15} />
                </button>
              </div>

              <div className="p-5">
                {/* Form-level error banner */}
                {formError && (
                  <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                    {formError}
                  </div>
                )}

                {/*
                  AddressForm is a feature component — kept intact with all its
                  logic: pincode lookup, GPS geolocation, lazy MapAddressPicker,
                  label segmented-control, set-as-default checkbox, validation.
                */}
                <AddressForm
                  initialValues={editingAddress || undefined}
                  onSubmit={formMode === 'create' ? handleCreate : handleUpdate}
                  onCancel={() => {
                    setFormMode(null);
                    setFormError(null);
                  }}
                  submitLabel={formMode === 'create' ? 'Save address' : 'Update address'}
                  busy={createAddress.isPending || updateAddress.isPending}
                  showSetDefault
                />
              </div>
            </div>
          </motion.div>
        )}

        {/* ── Address list / loading / error / empty ── */}
        {isLoading ? (
          /* Skeleton: 2-col grid matching the real card layout */
          <motion.div variants={fadeUp} className="grid sm:grid-cols-2 gap-4">
            {Array.from({ length: 2 }).map((_, i) => (
              <div
                key={i}
                className="bg-wcard border border-wline rounded-xl2 p-[22px] animate-pulse"
              >
                <div className="h-[10px] w-14 bg-wline rounded-full mb-3" />
                <div className="h-4 w-36 bg-wline rounded mb-2.5" />
                <div className="h-3 w-full bg-wline rounded mb-1.5" />
                <div className="h-3 w-4/5 bg-wline rounded mb-1.5" />
                <div className="h-3 w-28 bg-wline rounded" />
              </div>
            ))}
          </motion.div>
        ) : isError ? (
          <motion.div variants={fadeUp}>
            <div className="flex flex-col items-start gap-3 bg-wcard border border-red-200 rounded-xl2 p-5 text-sm text-red-700">
              <span>Could not load your addresses. Please try again.</span>
              <button
                type="button"
                onClick={() => refetch()}
                className="bg-transparent border border-red-300 text-red-700 rounded-full px-4 py-1.5 text-xs cursor-pointer hover:bg-red-50 transition-colors"
              >
                Retry
              </button>
            </div>
          </motion.div>
        ) : !addresses?.length && formMode === null ? (
          /* Empty state */
          <motion.div variants={fadeUp}>
            <div className="bg-wcard border border-wline rounded-xl2 p-10 text-center">
              <PinIcon size={44} className="text-wmuted mx-auto mb-4" />
              <p className="font-wserif text-[18px] text-wink mb-1">No saved addresses</p>
              <p className="text-[13px] text-wmuted mb-5">
                Add a delivery address to make checkout faster.
              </p>
              <button
                type="button"
                onClick={() => {
                  setFormMode('create');
                  setFormError(null);
                }}
                className="bg-wgreen text-white border-0 rounded-full px-6 py-3 text-[13.5px] cursor-pointer hover:bg-wgreen-dark transition-colors"
              >
                + Add Address
              </button>
            </div>
          </motion.div>
        ) : (
          /* 2-column wellness card grid */
          <motion.div
            variants={staggerContainer(0.07)}
            initial="hidden"
            animate="show"
            className="grid sm:grid-cols-2 gap-4"
          >
            {(addresses || []).map((addr) => {
              const labelKey = String(addr.label || 'other').toLowerCase();
              const labelText = (LABEL_DISPLAY[labelKey] || labelKey).toUpperCase();
              const isConfirmingDelete = confirmingDeleteId === addr.id;
              const isDeleting = deletingId === addr.id;

              return (
                <motion.div key={addr.id} variants={fadeUp}>
                  {/* ── Wellness address card ── */}
                  <div
                    className={cn(
                      'bg-wcard border border-wline rounded-xl2 p-[22px] transition-shadow duration-200',
                      addr.is_default
                        ? 'shadow-[0_0_0_1.5px_#183A2E]'
                        : 'hover:shadow-sm',
                    )}
                  >
                    {/* Top row: label eyebrow + default badge */}
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[11px] tracking-[0.12em] uppercase text-wgold">
                        {labelText}
                        {addr.latitude != null && (
                          <span className="ml-1.5 text-wmuted normal-case tracking-normal text-[10px]">
                            · pinned
                          </span>
                        )}
                      </span>
                      {addr.is_default && (
                        <span className="text-[10.5px] bg-wgreen text-white px-2.5 py-[3px] rounded-full">
                          Default
                        </span>
                      )}
                    </div>

                    {/* Recipient name */}
                    <p className="text-[15px] text-wink font-medium mb-1">{addr.full_name}</p>

                    {/* Address block */}
                    <p className="text-[13px] text-wmuted leading-[1.6] font-light">
                      {addr.line1}
                      {addr.line2 ? `, ${addr.line2}` : ''}
                      {addr.landmark ? ` (${addr.landmark})` : ''}
                      <br />
                      {addr.city}, {addr.state} — {addr.pincode}
                      {addr.phone && (
                        <>
                          <br />
                          {addr.phone}
                        </>
                      )}
                    </p>

                    {/* Action row */}
                    <div className="flex flex-wrap items-center gap-x-[18px] gap-y-2 mt-4 pt-3 border-t border-wline text-[12.5px]">
                      {/* Edit */}
                      <button
                        type="button"
                        onClick={() => {
                          setFormMode({ id: addr.id });
                          setFormError(null);
                          setConfirmingDeleteId(null);
                        }}
                        className="text-wgreen cursor-pointer bg-transparent border-0 p-0 hover:text-wgreen-dark transition-colors"
                      >
                        Edit
                      </button>

                      {/* Set default — only shown on non-default cards */}
                      {!addr.is_default && (
                        <button
                          type="button"
                          onClick={() => handleSetDefault(addr.id)}
                          className="text-wmuted cursor-pointer bg-transparent border-0 p-0 hover:text-wink transition-colors"
                        >
                          Set default
                        </button>
                      )}

                      {/* Remove — first click shows inline confirmation */}
                      <button
                        type="button"
                        onClick={() => handleDelete(addr.id)}
                        disabled={isDeleting}
                        className="text-wmuted cursor-pointer bg-transparent border-0 p-0 hover:text-red-600 transition-colors ml-auto disabled:opacity-50"
                      >
                        {isDeleting ? 'Removing…' : 'Remove'}
                      </button>
                    </div>
                  </div>

                  {/* Inline delete confirmation — replaces window.confirm (two-step flow) */}
                  {isConfirmingDelete && (
                    <div
                      role="alertdialog"
                      aria-live="assertive"
                      aria-label="Confirm address deletion"
                      className="mt-2 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-700"
                    >
                      <span className="flex-1">Delete this address?</span>
                      <button
                        type="button"
                        onClick={() => handleDelete(addr.id)}
                        className="rounded-full bg-red-600 text-white border-0 px-3 py-1.5 text-[12px] cursor-pointer hover:bg-red-700 transition-colors"
                      >
                        Delete
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingDeleteId(null)}
                        className="text-wmuted text-[12px] bg-transparent border-0 p-0 cursor-pointer hover:text-wink transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </motion.div>
              );
            })}
          </motion.div>
        )}

        {/* Inline delete mutation error (persists across cards) */}
        {deleteError && (
          <motion.div variants={fadeUp} className="mt-3">
            <div className="flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {deleteError}
            </div>
          </motion.div>
        )}
      </motion.div>
    </AccountLayout>
  );
}
