import { useState } from 'react';
import { Link } from 'react-router-dom';
import { MapPin, Plus, X } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import {
  useAddresses,
  useCreateAddress,
  useUpdateAddress,
  useDeleteAddress,
  useSetDefaultAddress,
} from '@/features/addresses/hooks.js';
import AddressCard from '@/features/addresses/components/AddressCard.jsx';
import AddressForm from '@/features/addresses/components/AddressForm.jsx';
import { fadeUp, staggerContainer, scaleIn } from '@/lib/motion.js';
import { motion } from 'framer-motion';

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

  if (!user) {
    return (
      <Page>
        {/* Page header */}
        <div className="mb-6 flex items-center gap-3 border-b border-line-subtle pb-5">
          <span className="grid size-9 place-items-center rounded-sm bg-accent/12 text-accent">
            <MapPin className="size-5" aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-lg font-semibold text-ink-primary">Manage Addresses</h1>
            <p className="text-xs text-ink-secondary">
              Saved delivery addresses — pick one at checkout.
            </p>
          </div>
        </div>
        <EmptyState
          icon={MapPin}
          title="Sign in first"
          action={
            <Link to="/login?next=/account/addresses">
              <Button size="sm">Sign in</Button>
            </Link>
          }
        />
      </Page>
    );
  }

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

  return (
    <Page>
      {/* ── Page header bar ── */}
      <div className="mb-5 flex items-center justify-between gap-4 border-b border-line-subtle pb-4">
        <div className="flex items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-accent/12 text-accent">
            <MapPin className="size-5" aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-lg font-semibold text-ink-primary">Manage Addresses</h1>
            <p className="text-xs text-ink-secondary">
              Saved delivery addresses — pick one at checkout.
            </p>
          </div>
        </div>
        {formMode === null && (
          <Button
            size="sm"
            onClick={() => { setFormMode('create'); setFormError(null); }}
          >
            <Plus className="size-4" aria-hidden="true" />
            Add new address
          </Button>
        )}
      </div>

      <motion.div
        variants={staggerContainer(0.06)}
        initial="hidden"
        animate="show"
        className="max-w-2xl space-y-4"
      >
        {/* ── Add / Edit form ── */}
        {formMode !== null && (
          <motion.div variants={scaleIn}>
            <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
              {/* Form header */}
              <div className="flex items-center justify-between border-b border-line-subtle bg-bg-sunken px-4 py-3">
                <h2 className="text-sm font-semibold text-ink-primary">
                  {formMode === 'create' ? 'Add new address' : 'Edit address'}
                </h2>
                <button
                  type="button"
                  aria-label="Close form"
                  onClick={() => { setFormMode(null); setFormError(null); }}
                  className="grid size-7 place-items-center rounded-xs text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
                >
                  <X className="size-4" aria-hidden="true" />
                </button>
              </div>
              <div className="p-4">
                {formError && (
                  <div className="mb-4 flex items-start gap-2.5 rounded-sm border border-danger/30 bg-danger/8 px-3.5 py-3 text-sm text-danger">
                    {formError}
                  </div>
                )}
                <AddressForm
                  initialValues={editingAddress || undefined}
                  onSubmit={formMode === 'create' ? handleCreate : handleUpdate}
                  onCancel={() => { setFormMode(null); setFormError(null); }}
                  submitLabel={formMode === 'create' ? 'Save address' : 'Update address'}
                  busy={createAddress.isPending || updateAddress.isPending}
                  showSetDefault
                />
              </div>
            </div>
          </motion.div>
        )}

        {/* ── Address list ── */}
        {isLoading ? (
          <motion.div variants={fadeUp} className="flex flex-col gap-3">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-32 rounded-sm" />
            ))}
          </motion.div>
        ) : isError ? (
          <motion.div variants={fadeUp}>
            <div className="flex flex-col items-start gap-2.5 rounded-sm border border-danger/30 bg-danger/8 p-4 text-sm text-danger">
              <span>Could not load your addresses. Please try again.</span>
              <Button size="sm" variant="outline" className="mt-2" onClick={() => refetch()}>
                Retry
              </Button>
            </div>
          </motion.div>
        ) : !addresses?.length && formMode === null ? (
          <motion.div variants={fadeUp}>
            <div className="rounded-sm border border-line-subtle bg-bg-elevated p-8 text-center shadow-sm">
              <MapPin className="mx-auto mb-3 size-10 text-ink-tertiary" aria-hidden="true" />
              <p className="text-sm font-medium text-ink-primary">No saved addresses yet</p>
              <p className="mt-1 text-xs text-ink-secondary">
                Add a delivery address to make checkout faster.
              </p>
              <Button
                size="sm"
                className="mt-4"
                onClick={() => { setFormMode('create'); setFormError(null); }}
              >
                <Plus className="size-4" aria-hidden="true" />
                Add new address
              </Button>
            </div>
          </motion.div>
        ) : (
          <motion.div
            variants={staggerContainer(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-3"
          >
            {(addresses || []).map((addr) => (
              <motion.div key={addr.id} variants={fadeUp}>
                <AddressCard
                  address={addr}
                  onEdit={(a) => {
                    setFormMode({ id: a.id });
                    setFormError(null);
                    setConfirmingDeleteId(null);
                  }}
                  onDelete={handleDelete}
                  onSetDefault={handleSetDefault}
                  deleting={deletingId === addr.id}
                />
                {/* Inline delete confirmation — replaces window.confirm */}
                {confirmingDeleteId === addr.id && (
                  <div
                    role="alertdialog"
                    aria-live="assertive"
                    aria-label="Confirm address deletion"
                    className="mt-1 flex items-center gap-2 rounded-sm border border-danger/30 bg-danger/8 px-3 py-2 text-sm text-danger"
                  >
                    <span className="flex-1">Delete this address?</span>
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-danger/40 text-danger hover:bg-danger/12"
                      onClick={() => handleDelete(addr.id)}
                    >
                      Delete
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-ink-secondary"
                      onClick={() => setConfirmingDeleteId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                )}
              </motion.div>
            ))}
            {/* Inline delete mutation error */}
            {deleteError && (
              <motion.div variants={fadeUp}>
                <div className="flex items-start gap-2.5 rounded-sm border border-danger/30 bg-danger/8 px-3.5 py-3 text-sm text-danger">
                  {deleteError}
                </div>
              </motion.div>
            )}
          </motion.div>
        )}
      </motion.div>
    </Page>
  );
}
