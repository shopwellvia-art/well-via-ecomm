import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Plus, Trash2, TicketPercent, Pencil, X, Tag } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, listStagger, scaleIn } from '@/lib/motion.js';
import {
  useCoupons,
  useCreateCoupon,
  useDeleteCoupon,
  useUpdateCoupon,
} from '@/features/coupons/hooks.js';

const EMPTY_FORM = {
  code: '',
  description: '',
  discount_type: 'percent',
  discount_value: '',
  min_order_amount: '',
  max_discount: '',
  starts_at: '',
  expires_at: '',
  usage_limit: '',
  per_user_limit: '',
  is_active: true,
};

// "2026-05-30T00:00:00+00:00" -> "2026-05-30T00:00" for <input type="datetime-local">
function isoToLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// "2026-05-30T00:00" -> ISO string the API accepts.
function localInputToIso(local) {
  if (!local) return null;
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function toPayload(form) {
  const numOrNull = (v) => (v === '' || v == null ? null : Number(v));
  return {
    code: form.code.trim().toUpperCase(),
    description: form.description.trim() || null,
    discount_type: form.discount_type,
    discount_value: Number(form.discount_value),
    min_order_amount: numOrNull(form.min_order_amount),
    max_discount: numOrNull(form.max_discount),
    starts_at: localInputToIso(form.starts_at),
    expires_at: localInputToIso(form.expires_at),
    usage_limit: numOrNull(form.usage_limit),
    per_user_limit: numOrNull(form.per_user_limit),
    is_active: !!form.is_active,
  };
}

function fromCoupon(c) {
  return {
    code: c.code || '',
    description: c.description || '',
    discount_type: c.discount_type || 'percent',
    discount_value: c.discount_value ?? '',
    min_order_amount: c.min_order_amount ?? '',
    max_discount: c.max_discount ?? '',
    starts_at: isoToLocalInput(c.starts_at),
    expires_at: isoToLocalInput(c.expires_at),
    usage_limit: c.usage_limit ?? '',
    per_user_limit: c.per_user_limit ?? '',
    is_active: !!c.is_active,
  };
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function formatDiscount(c) {
  if (c.discount_type === 'percent') return `${Number(c.discount_value)}%`;
  return `₹${Number(c.discount_value).toFixed(2)}`;
}

function CouponForm({ initial, onCancel, onSaved, mode = 'create' }) {
  const [form, setForm] = useState(initial || EMPTY_FORM);
  const [error, setError] = useState(null);
  const create = useCreateCoupon();
  const update = useUpdateCoupon();

  useEffect(() => {
    setForm(initial || EMPTY_FORM);
    setError(null);
  }, [initial]);

  function set(k) {
    return (e) => {
      const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      setForm((f) => ({ ...f, [k]: v }));
    };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!form.code.trim()) {
      setError('Coupon code is required.');
      return;
    }
    const v = Number(form.discount_value);
    if (!form.discount_value || Number.isNaN(v) || v <= 0) {
      setError('Discount value must be greater than zero.');
      return;
    }
    if (form.discount_type === 'percent' && v > 100) {
      setError('Percent discount cannot exceed 100.');
      return;
    }
    if (form.starts_at && form.expires_at && form.starts_at >= form.expires_at) {
      setError('Expiry must be after the start date.');
      return;
    }

    const payload = toPayload(form);
    try {
      if (mode === 'edit' && initial?.id) {
        // PATCH ignores `code` server-side anyway; strip to keep the diff small.
        const { code, ...rest } = payload;
        await update.mutateAsync({ id: initial.id, data: rest });
      } else {
        await create.mutateAsync(payload);
      }
      onSaved?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not save the coupon.');
    }
  }

  const isPercent = form.discount_type === 'percent';
  const pending = create.isPending || update.isPending;

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
            <Tag className="size-3.5" aria-hidden="true" />
          </span>
          <h2 className="text-h3 font-semibold text-ink-primary">
            {mode === 'edit' ? 'Edit coupon' : 'New coupon'}
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close form"
          onClick={onCancel}
          className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="p-6">
        {/* Section: Core fields */}
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
          Basic details
        </p>
        <div className="mb-6 grid gap-4 sm:grid-cols-2">
          <Input
            label="Code"
            required
            placeholder="SUMMER20"
            value={form.code}
            onChange={set('code')}
            disabled={mode === 'edit'}
            helper={mode === 'edit' ? 'Code cannot be changed after creation.' : 'Case-insensitive. Stored uppercase.'}
          />
          <Input
            label="Description (optional)"
            placeholder="Summer launch offer"
            value={form.description}
            onChange={set('description')}
          />

          <Select label="Discount type" value={form.discount_type} onChange={set('discount_type')}>
            <option value="percent">Percent (%)</option>
            <option value="fixed">Fixed amount</option>
          </Select>
          <Input
            label={isPercent ? 'Discount %' : 'Discount amount'}
            required
            type="number"
            step="0.01"
            min="0"
            value={form.discount_value}
            onChange={set('discount_value')}
            helper={isPercent ? 'e.g. 10 for 10% off' : 'e.g. 100 for ₹100 off'}
          />
        </div>

        {/* Section: Constraints */}
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
          Constraints
        </p>
        <div className="mb-6 grid gap-4 sm:grid-cols-2">
          <Input
            label="Min order amount (optional)"
            type="number"
            step="0.01"
            min="0"
            value={form.min_order_amount}
            onChange={set('min_order_amount')}
            helper="Coupon won't apply below this cart subtotal."
          />
          <Input
            label="Max discount (optional)"
            type="number"
            step="0.01"
            min="0"
            value={form.max_discount}
            onChange={set('max_discount')}
            helper={isPercent ? 'Caps a percent discount.' : 'Usually unused for fixed.'}
          />

          <Input
            label="Total usage limit (optional)"
            type="number"
            min="1"
            step="1"
            value={form.usage_limit}
            onChange={set('usage_limit')}
            helper="Across all users."
          />
          <Input
            label="Per-user limit (optional)"
            type="number"
            min="1"
            step="1"
            value={form.per_user_limit}
            onChange={set('per_user_limit')}
            helper="How many times one user can redeem."
          />
        </div>

        {/* Section: Validity window */}
        <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
          Validity window
        </p>
        <div className="mb-5 grid gap-4 sm:grid-cols-2">
          <Input
            label="Starts at (optional)"
            type="datetime-local"
            value={form.starts_at}
            onChange={set('starts_at')}
          />
          <Input
            label="Expires at (optional)"
            type="datetime-local"
            value={form.expires_at}
            onChange={set('expires_at')}
          />
        </div>

        <label className="flex items-center gap-2.5 rounded-md border border-line-subtle bg-bg-sunken px-4 py-3 text-sm text-ink-secondary transition-colors hover:border-line-strong">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={set('is_active')}
            className="size-4 rounded-sm border border-line-subtle bg-bg-sunken text-accent focus-visible:focus-ring"
          />
          <span className="font-medium text-ink-primary">Active</span>
          <span className="text-xs text-ink-tertiary">— coupon is redeemable at checkout</span>
        </label>

        {error && (
          <p className="mt-4 flex items-center gap-2 rounded-md border border-danger/30 bg-danger/8 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" loading={pending}>
            {mode === 'edit' ? 'Save changes' : 'Create coupon'}
          </Button>
        </div>
      </div>
    </motion.form>
  );
}

function CouponRow({ coupon, onEdit }) {
  const update = useUpdateCoupon();
  const del = useDeleteCoupon();
  const [confirming, setConfirming] = useState(false);

  const limitText = coupon.usage_limit
    ? `${coupon.usage_count}/${coupon.usage_limit}`
    : `${coupon.usage_count}`;

  function toggleActive() {
    update.mutate({ id: coupon.id, data: { is_active: !coupon.is_active } });
  }

  function handleDelete() {
    del.mutate(coupon.id, { onSuccess: () => setConfirming(false) });
  }

  const pending = update.isPending || del.isPending;

  return (
    <motion.tr
      variants={fadeUp}
      className="group border-t border-line-subtle transition-colors hover:bg-bg-sunken/50"
    >
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="inline-block rounded bg-accent/10 px-2 py-0.5 font-mono text-sm font-semibold tracking-wider text-accent">
            {coupon.code}
          </span>
        </div>
        {coupon.description && (
          <p className="mt-1 text-xs text-ink-tertiary">{coupon.description}</p>
        )}
      </td>
      <td className="px-5 py-3.5">
        <span className="nums text-sm font-medium text-ink-primary">
          {formatDiscount(coupon)}
        </span>
        <span className="ml-1.5 text-xs text-ink-tertiary">
          {coupon.discount_type === 'percent' ? 'off' : 'flat'}
        </span>
      </td>
      <td className="px-5 py-3.5 text-sm nums text-ink-secondary">
        {coupon.min_order_amount ? `₹${Number(coupon.min_order_amount).toFixed(2)}` : '—'}
      </td>
      <td className="px-5 py-3.5 text-sm text-ink-secondary">
        {formatDate(coupon.expires_at)}
      </td>
      <td className="px-5 py-3.5 text-sm nums text-ink-secondary">{limitText}</td>
      <td className="px-5 py-3.5">
        <button
          type="button"
          role="switch"
          aria-checked={coupon.is_active}
          aria-label={coupon.is_active ? 'Deactivate coupon' : 'Activate coupon'}
          disabled={pending}
          onClick={toggleActive}
          className={cn(
            'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200',
            'focus-visible:focus-ring disabled:opacity-40 disabled:pointer-events-none',
            coupon.is_active ? 'bg-accent' : 'bg-fill-strong',
          )}
        >
          <span
            className={cn(
              'pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
              coupon.is_active ? 'translate-x-4' : 'translate-x-0',
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
                onClick={handleDelete}
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
                aria-label={`Edit coupon ${coupon.code}`}
                disabled={pending}
                onClick={() => onEdit(coupon)}
                className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring disabled:opacity-30 disabled:pointer-events-none"
              >
                <Pencil className="size-3.5" />
              </button>
              <button
                type="button"
                aria-label={`Delete coupon ${coupon.code}`}
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

export default function AdminCouponsPage() {
  const { data: coupons = [], isLoading, isError, refetch } = useCoupons();
  // null = closed, 'new' = creating, object = editing that coupon
  const [editing, setEditing] = useState(null);

  const isFormOpen = editing !== null;
  // For edit, carry the id alongside the form fields so CouponForm can issue the PATCH.
  const formInitial =
    editing && editing !== 'new' ? { ...fromCoupon(editing), id: editing.id } : null;

  const active = coupons.filter((c) => c.is_active).length;
  const inactive = coupons.length - active;

  return (
    <AdminPage
      title="Coupons"
      description={
        isLoading
          ? 'Loading…'
          : `${coupons.length} coupon${coupons.length === 1 ? '' : 's'} — customers redeem these at checkout.`
      }
      action={
        !isFormOpen && (
          <Button onClick={() => setEditing('new')}>
            <Plus className="size-4" aria-hidden="true" />
            New coupon
          </Button>
        )
      }
    >
      {isFormOpen && (
        <CouponForm
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={formInitial}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}

      {isError ? (
        <EmptyState
          icon={TicketPercent}
          iconTone="danger"
          title="Couldn't load coupons"
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
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      ) : coupons.length === 0 ? (
        <EmptyState
          icon={TicketPercent}
          title="No coupons yet"
          description="Create your first discount code to start running promotions."
          action={
            !isFormOpen && (
              <Button onClick={() => setEditing('new')}>
                <Plus className="size-4" aria-hidden="true" />
                New coupon
              </Button>
            )
          }
          className="py-12"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated shadow-md">
          {/* Summary strip */}
          {coupons.length > 0 && (
            <div className="flex items-center gap-3 border-b border-line-subtle px-5 py-3">
              <span className="text-xs text-ink-tertiary">
                <span className="nums font-medium text-ink-primary">{coupons.length}</span> total
              </span>
              <span className="text-ink-tertiary">·</span>
              <Badge tone="success" dot>{active} active</Badge>
              {inactive > 0 && (
                <>
                  <span className="text-ink-tertiary">·</span>
                  <Badge tone="neutral">{inactive} inactive</Badge>
                </>
              )}
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/50 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Code</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Discount</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Min order</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Expires</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Used</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Active</th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-widest text-ink-tertiary">Actions</th>
                </tr>
              </thead>
              <motion.tbody
                variants={listStagger(0.04)}
                initial="hidden"
                animate="show"
              >
                {coupons.map((c) => (
                  <CouponRow key={c.id} coupon={c} onEdit={setEditing} />
                ))}
              </motion.tbody>
            </table>
          </div>
        </div>
      )}
    </AdminPage>
  );
}
