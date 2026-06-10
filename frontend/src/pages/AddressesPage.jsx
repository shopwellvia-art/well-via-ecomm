import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { MapPin, Plus, X } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardHeader, CardBody } from '@/components/ui/Card.jsx';
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

export default function AddressesPage() {
  const user = useAuthStore((s) => s.user);

  const { data: addresses, isLoading, isError } = useAddresses();
  const createAddress = useCreateAddress();
  const updateAddress = useUpdateAddress();
  const deleteAddress = useDeleteAddress();
  const setDefaultAddress = useSetDefaultAddress();

  // 'create' | { id } for edit | null
  const [formMode, setFormMode] = useState(null);
  // id of address currently being deleted (for per-card loading state)
  const [deletingId, setDeletingId] = useState(null);
  // inline error message
  const [formError, setFormError] = useState(null);

  // Redirect when signed out — mirror AccountSecurityPage pattern.
  if (!user) {
    return (
      <Page>
        <div className="mb-8 border-b border-line-subtle pb-6">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-accent/12 text-accent">
              <MapPin className="size-5" aria-hidden="true" />
            </span>
            <div>
              <h1 className="text-h1 tracking-tight text-ink-primary">My addresses</h1>
              <p className="mt-0.5 text-sm text-ink-secondary">
                Saved delivery addresses — pick one at checkout.
              </p>
            </div>
          </div>
        </div>
        <div className="mt-6">
          <EmptyState
            icon={MapPin}
            title="Sign in first"
            action={
              <Link to="/login?next=/account/addresses">
                <Button size="sm">Sign in</Button>
              </Link>
            }
          />
        </div>
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

  function handleDelete(id) {
    if (!window.confirm('Delete this address?')) return;
    setDeletingId(id);
    deleteAddress.mutate(id, {
      onSettled: () => setDeletingId(null),
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
      {/* ── Page header ── */}
      <div className="mb-8 border-b border-line-subtle pb-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-accent/12 text-accent">
              <MapPin className="size-5" aria-hidden="true" />
            </span>
            <div>
              <h1 className="text-h1 tracking-tight text-ink-primary">My addresses</h1>
              <p className="mt-0.5 text-sm text-ink-secondary">
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
              Add address
            </Button>
          )}
        </div>
      </div>

      <motion.div
        variants={staggerContainer(0.06)}
        initial="hidden"
        animate="show"
        className="max-w-2xl space-y-5"
      >
        {/* ── Add / Edit form ── */}
        {formMode !== null && (
          <motion.div variants={scaleIn}>
            <Card className="overflow-hidden p-0">
              <CardHeader
                title={formMode === 'create' ? 'New address' : 'Edit address'}
                action={
                  <button
                    type="button"
                    aria-label="Close form"
                    onClick={() => { setFormMode(null); setFormError(null); }}
                    className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
                  >
                    <X className="size-4" aria-hidden="true" />
                  </button>
                }
              />
              <CardBody className="p-5">
                {formError && (
                  <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-danger/30 bg-danger/8 px-3.5 py-3 text-sm text-danger">
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
              </CardBody>
            </Card>
          </motion.div>
        )}

        {/* ── Address list ── */}
        {isLoading ? (
          <motion.div variants={fadeUp} className="flex flex-col gap-3">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-32 rounded-lg" />
            ))}
          </motion.div>
        ) : isError ? (
          <motion.div variants={fadeUp}>
            <div className="flex items-start gap-2.5 rounded-lg border border-danger/30 bg-danger/8 p-4 text-sm text-danger">
              Could not load your addresses. Please refresh the page.
            </div>
          </motion.div>
        ) : !addresses?.length && formMode === null ? (
          <motion.div variants={fadeUp}>
            <EmptyState
              icon={MapPin}
              title="No saved addresses yet"
              description="Add a delivery address to make checkout faster."
              action={
                <Button
                  size="sm"
                  onClick={() => { setFormMode('create'); setFormError(null); }}
                >
                  <Plus className="size-4" aria-hidden="true" />
                  Add address
                </Button>
              }
            />
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
                  onEdit={(a) => { setFormMode({ id: a.id }); setFormError(null); }}
                  onDelete={handleDelete}
                  onSetDefault={handleSetDefault}
                  deleting={deletingId === addr.id}
                />
              </motion.div>
            ))}
          </motion.div>
        )}
      </motion.div>
    </Page>
  );
}
