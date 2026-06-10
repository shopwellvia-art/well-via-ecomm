import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Plus, Trash2, Pencil, X, Percent, Receipt } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, listStagger, scaleIn } from '@/lib/motion.js';
import {
  useTaxes,
  useCreateTax,
  useUpdateTax,
  useDeleteTax,
} from '@/features/taxes/hooks.js';

const EMPTY = { name: '', rate: '', is_active: true };

function TaxForm({ initial, mode, onCancel, onSaved }) {
  const [form, setForm] = useState(initial || EMPTY);
  const [error, setError] = useState(null);
  const create = useCreateTax();
  const update = useUpdateTax();

  useEffect(() => {
    setForm(initial || EMPTY);
    setError(null);
  }, [initial]);

  const pending = create.isPending || update.isPending;

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (!form.name.trim()) {
      setError('Tax name is required.');
      return;
    }
    const rate = Number(form.rate);
    if (form.rate === '' || Number.isNaN(rate) || rate < 0 || rate > 100) {
      setError('Rate must be between 0 and 100.');
      return;
    }
    const payload = {
      name: form.name.trim(),
      rate,
      is_active: !!form.is_active,
    };
    try {
      if (mode === 'edit' && initial?.id) {
        await update.mutateAsync({ id: initial.id, data: payload });
      } else {
        await create.mutateAsync(payload);
      }
      onSaved?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not save the tax.');
    }
  }

  return (
    <motion.form
      onSubmit={handleSubmit}
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className="mb-6 rounded-lg border border-line-subtle bg-bg-elevated shadow-md"
    >
      {/* Form header */}
      <div className="flex items-center justify-between border-b border-line-subtle px-6 py-4">
        <div className="flex items-center gap-2.5">
          <span className="grid size-7 place-items-center rounded-md bg-accent/12 text-accent">
            <Receipt className="size-3.5" aria-hidden="true" />
          </span>
          <h2 className="text-h3 font-semibold text-ink-primary">
            {mode === 'edit' ? 'Edit tax rate' : 'New tax rate'}
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={onCancel}
          className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Name"
            required
            placeholder="GST 18%"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            helper="Shown on receipts and the cart breakdown."
          />
          <Input
            label="Rate (%)"
            required
            type="number"
            step="0.001"
            min="0"
            max="100"
            placeholder="18"
            value={form.rate}
            onChange={(e) => setForm((f) => ({ ...f, rate: e.target.value }))}
            helper="Up to 3 decimal places (e.g. 18.000)."
          />
        </div>

        <label className="mt-4 flex items-center gap-2.5 rounded-md border border-line-subtle bg-bg-sunken px-4 py-3 text-sm text-ink-secondary transition-colors hover:border-line-strong">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
            className="size-4 rounded-sm border border-line-subtle bg-bg-sunken text-accent focus-visible:focus-ring"
          />
          <span className="font-medium text-ink-primary">Active</span>
          <span className="text-xs text-ink-tertiary">— inactive taxes are skipped at checkout even if attached to products.</span>
        </label>

        {error && (
          <p className="mt-4 rounded-md border border-danger/30 bg-danger/8 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" loading={pending}>
            {mode === 'edit' ? 'Save changes' : 'Create tax'}
          </Button>
        </div>
      </div>
    </motion.form>
  );
}

function TaxRow({ tax, onEdit }) {
  const update = useUpdateTax();
  const del = useDeleteTax();
  const [confirming, setConfirming] = useState(false);

  const pending = update.isPending || del.isPending;

  function toggleActive() {
    update.mutate({ id: tax.id, data: { is_active: !tax.is_active } });
  }

  return (
    <motion.tr
      variants={fadeUp}
      className="group border-t border-line-subtle transition-colors hover:bg-bg-sunken/50"
    >
      <td className="px-5 py-3.5">
        <p className="text-sm font-medium text-ink-primary">{tax.name}</p>
      </td>
      <td className="px-5 py-3.5">
        <span className="nums inline-flex items-baseline gap-1 text-sm">
          <span className="font-semibold text-ink-primary">{Number(tax.rate).toFixed(3)}</span>
          <span className="text-xs text-ink-tertiary">%</span>
        </span>
      </td>
      <td className="px-5 py-3.5">
        {tax.is_active ? (
          <Badge tone="success" dot>Active</Badge>
        ) : (
          <Badge tone="neutral">Inactive</Badge>
        )}
      </td>
      <td className="px-5 py-3.5">
        <button
          type="button"
          role="switch"
          aria-checked={tax.is_active}
          aria-label={tax.is_active ? 'Deactivate' : 'Activate'}
          disabled={pending}
          onClick={toggleActive}
          className={cn(
            'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200',
            'focus-visible:focus-ring disabled:opacity-40 disabled:pointer-events-none',
            tax.is_active ? 'bg-accent' : 'bg-fill-strong',
          )}
        >
          <span
            className={cn(
              'pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
              tax.is_active ? 'translate-x-4' : 'translate-x-0',
            )}
          />
        </button>
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center justify-end gap-1">
          {confirming ? (
            <div className="flex items-center gap-1.5 rounded-md border border-danger/30 bg-danger/8 px-3 py-1.5">
              <span className="text-xs text-danger">Delete?</span>
              <Button
                variant="destructive"
                size="sm"
                loading={del.isPending}
                onClick={() =>
                  del.mutate(tax.id, { onSuccess: () => setConfirming(false) })
                }
              >
                Confirm
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={del.isPending}
                onClick={() => setConfirming(false)}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <>
              <button
                type="button"
                aria-label={`Edit ${tax.name}`}
                disabled={pending}
                onClick={() => onEdit(tax)}
                className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring disabled:opacity-30 disabled:pointer-events-none"
              >
                <Pencil className="size-3.5" />
              </button>
              <button
                type="button"
                aria-label={`Delete ${tax.name}`}
                disabled={pending}
                onClick={() => setConfirming(true)}
                className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring disabled:opacity-30 disabled:pointer-events-none"
              >
                <Trash2 className="size-3.5" />
              </button>
            </>
          )}
        </div>
      </td>
    </motion.tr>
  );
}

export default function AdminTaxesPage() {
  const { data: taxes = [], isLoading, isError, refetch } = useTaxes();
  const [editing, setEditing] = useState(null);

  const isFormOpen = editing !== null;
  const formInitial =
    editing && editing !== 'new'
      ? { id: editing.id, name: editing.name, rate: editing.rate, is_active: editing.is_active }
      : null;

  const active = taxes.filter((t) => t.is_active).length;

  return (
    <AdminPage
      title="Taxes"
      description={
        isLoading
          ? 'Loading…'
          : `${taxes.length} tax rate${taxes.length === 1 ? '' : 's'} — attach to products from the product form.`
      }
      action={
        !isFormOpen && (
          <Button onClick={() => setEditing('new')}>
            <Plus className="size-4" aria-hidden="true" />
            New tax
          </Button>
        )
      }
    >
      {isFormOpen && (
        <TaxForm
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={formInitial}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}

      {isError ? (
        <EmptyState
          icon={Percent}
          iconTone="danger"
          title="Couldn't load taxes"
          description="Something went wrong. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : taxes.length === 0 ? (
        <EmptyState
          icon={Percent}
          title="No taxes yet"
          description="Create your first tax rate to start collecting it at checkout."
          action={
            !isFormOpen && (
              <Button onClick={() => setEditing('new')}>
                <Plus className="size-4" aria-hidden="true" />
                New tax
              </Button>
            )
          }
          className="py-12"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated shadow-md">
          {/* Summary strip */}
          <div className="flex items-center gap-3 border-b border-line-subtle px-5 py-3">
            <span className="text-xs text-ink-tertiary">
              <span className="nums font-medium text-ink-primary">{taxes.length}</span> rates
            </span>
            <span className="text-ink-tertiary">·</span>
            <Badge tone="success" dot>{active} active</Badge>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/50 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Name</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Rate</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Status</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Toggle</th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Actions</th>
                </tr>
              </thead>
              <motion.tbody
                variants={listStagger(0.04)}
                initial="hidden"
                animate="show"
              >
                {taxes.map((t) => (
                  <TaxRow key={t.id} tax={t} onEdit={setEditing} />
                ))}
              </motion.tbody>
            </table>
          </div>
        </div>
      )}
    </AdminPage>
  );
}
