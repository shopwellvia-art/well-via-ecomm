import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Search,
  Plus,
  Pencil,
  Trash2,
  X,
  Coins,
  Gift,
  Users as UsersIcon,
  Sparkles,
  ShoppingBag,
  Star,
  TicketPercent,
  RefreshCw,
  Send,
  Timer,
  CheckCircle2,
  Award,
  TrendingUp,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { fadeUp, scaleIn, listStagger, staggerContainer } from '@/lib/motion.js';
import { useUsers } from '@/features/users/hooks.js';
import {
  useAdminUserLoyalty,
  useAdminAdjust,
  useAdminTiers,
  useAdminCreateTier,
  useAdminUpdateTier,
  useAdminDeleteTier,
  useAdminReferrals,
  useAdminExpirePoints,
  useAdminEarnRules,
  useAdminUpdateEarnRule,
  useAdminVipTiers,
  useAdminCreateVipTier,
  useAdminUpdateVipTier,
  useAdminDeleteVipTier,
} from '@/features/loyalty/hooks.js';

const REASON_LABELS = {
  signup_bonus:   { label: 'Welcome bonus',    icon: Sparkles },
  place_order:    { label: 'Order',            icon: ShoppingBag },
  write_review:   { label: 'Review',           icon: Star },
  redeem:         { label: 'Redeemed',         icon: TicketPercent },
  refund_reversal:{ label: 'Refund reversal',  icon: RefreshCw },
  expiry:         { label: 'Expired',          icon: RefreshCw },
  admin_adjust:   { label: 'Admin adjustment', icon: Pencil },
};

function useDebounced(v, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ---- Users tab ----

function UserPicker({ onPick, picked }) {
  const [q, setQ] = useState('');
  const dq = useDebounced(q, 250);
  const { data, isLoading } = useUsers({ q: dq, page: 1, page_size: 8 });
  const users = data?.items || [];

  return (
    <div className="rounded-xl border border-line-subtle bg-bg-elevated p-5 shadow-sm">
      <p className="mb-1 text-sm font-semibold text-ink-primary">Find a customer</p>
      <p className="mb-4 text-xs text-ink-tertiary">Search to load their balance and ledger.</p>
      <Input
        icon={Search}
        placeholder="Search by email or name…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {q.trim() && (
        <motion.ul
          variants={listStagger(0.03)}
          initial="hidden"
          animate="show"
          className="mt-2 divide-y divide-line-subtle"
        >
          {isLoading ? (
            <li className="px-2 py-3 text-xs text-ink-tertiary">Searching…</li>
          ) : users.length === 0 ? (
            <li className="px-2 py-3 text-xs text-ink-tertiary">No matches.</li>
          ) : (
            users.map((u) => (
              <motion.li key={u.id} variants={fadeUp}>
                <button
                  type="button"
                  onClick={() => onPick(u)}
                  className={cn(
                    'flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors',
                    picked?.id === u.id ? 'bg-accent/10' : 'hover:bg-fill',
                  )}
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
                    {(u.email || '?').charAt(0).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink-primary">{u.email}</span>
                    {u.full_name && (
                      <span className="block truncate text-xs text-ink-tertiary">
                        {u.full_name}
                      </span>
                    )}
                  </span>
                  <span className="nums text-[10px] uppercase tracking-wide text-ink-tertiary">
                    #{u.id}
                  </span>
                </button>
              </motion.li>
            ))
          )}
        </motion.ul>
      )}
    </div>
  );
}

function AdjustForm({ userId }) {
  const [delta, setDelta] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState(null);
  const adjust = useAdminAdjust();

  async function submit(e) {
    e.preventDefault();
    setError(null);
    const n = Number(delta);
    if (!Number.isFinite(n) || n === 0 || !Number.isInteger(n)) {
      setError('Enter a non-zero whole number.');
      return;
    }
    if (description.trim().length < 3) {
      setError('Provide a reason — adjustments require a paper trail.');
      return;
    }
    try {
      await adjust.mutateAsync({ userId, delta: n, description: description.trim() });
      setDelta('');
      setDescription('');
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not adjust.');
    }
  }

  const parsedDelta = Number(delta);
  const isCredit = parsedDelta > 0;

  return (
    <form onSubmit={submit} className="rounded-xl border border-line-subtle bg-bg-sunken p-4">
      <p className="text-sm font-semibold text-ink-primary">Adjust points</p>
      <p className="mt-0.5 text-xs text-ink-tertiary">
        Positive = credit, negative = debit. Lifetime points won&apos;t change.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[160px_1fr_auto] sm:items-end">
        <div>
          <Input
            type="number"
            step="1"
            label="Delta"
            placeholder="e.g. 100 or -50"
            value={delta}
            onChange={(e) => setDelta(e.target.value)}
          />
          {delta && !isNaN(parsedDelta) && parsedDelta !== 0 && (
            <p className={cn('mt-1 flex items-center gap-1 text-xs', isCredit ? 'text-success' : 'text-danger')}>
              {isCredit
                ? <ArrowUpRight className="size-3" aria-hidden="true" />
                : <ArrowDownRight className="size-3" aria-hidden="true" />}
              {isCredit ? '+' : ''}<span className="nums">{parsedDelta.toLocaleString()}</span> pts
            </p>
          )}
        </div>
        <Input
          label="Reason"
          placeholder="Reason (required)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <Button type="submit" loading={adjust.isPending} className="mb-px">
          Apply
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </form>
  );
}

function LedgerRow({ tx }) {
  const meta = REASON_LABELS[tx.reason] || { label: tx.reason, icon: RefreshCw };
  const Icon = meta.icon;
  const isCredit = tx.delta > 0;
  return (
    <tr className="border-t border-line-subtle transition-colors duration-150 hover:bg-fill/40">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              'grid size-7 place-items-center rounded-full',
              isCredit ? 'bg-success/12 text-success' : 'bg-fill text-ink-secondary',
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
          </span>
          <span className="text-sm text-ink-primary">{meta.label}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-ink-tertiary">{tx.description || '—'}</td>
      <td className="px-4 py-3 font-mono text-xs text-ink-tertiary">
        {tx.ref_type ? `${tx.ref_type} #${tx.ref_id}` : '—'}
      </td>
      <td className="px-4 py-3 text-xs text-ink-tertiary">{formatDate(tx.created_at)}</td>
      <td
        className={cn(
          'px-4 py-3 text-right text-sm font-semibold nums',
          isCredit ? 'text-success' : 'text-ink-secondary',
        )}
      >
        {isCredit ? '+' : ''}
        {tx.delta.toLocaleString()}
      </td>
    </tr>
  );
}

function UserLoyaltyPanel({ user, onChange }) {
  const { data, isLoading, refetch } = useAdminUserLoyalty(user.id);

  return (
    <motion.div
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className="rounded-xl border border-line-subtle bg-bg-elevated shadow-md"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-5 py-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent/12 text-sm font-semibold text-accent">
          {(user.email || '?').charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-ink-primary">{user.email}</p>
          {user.full_name && (
            <p className="truncate text-xs text-ink-tertiary">{user.full_name}</p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={() => refetch()} disabled={isLoading}>
          <RefreshCw className="size-3.5" aria-hidden="true" /> Refresh
        </Button>
        <button
          type="button"
          aria-label="Clear selection"
          onClick={() => onChange(null)}
          className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      {/* Balance KPIs */}
      <div className="grid gap-px border-b border-line-subtle bg-line-subtle sm:grid-cols-2">
        <div className="bg-bg-elevated p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">Balance</p>
          <p className="mt-2 flex items-baseline gap-1.5">
            {isLoading
              ? <Skeleton className="h-8 w-24" />
              : (
                <>
                  <span className="nums text-3xl font-bold tracking-tight text-ink-primary">
                    {(data?.balance ?? 0).toLocaleString()}
                  </span>
                  <span className="text-sm font-normal text-ink-tertiary">pts</span>
                </>
              )}
          </p>
        </div>
        <div className="bg-bg-elevated p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">Lifetime</p>
          <p className="mt-2 flex items-baseline gap-1.5">
            {isLoading
              ? <Skeleton className="h-7 w-20" />
              : (
                <>
                  <span className="nums text-2xl font-semibold tracking-tight text-ink-secondary">
                    {(data?.lifetime ?? 0).toLocaleString()}
                  </span>
                  <span className="text-sm font-normal text-ink-tertiary">pts</span>
                </>
              )}
          </p>
        </div>
      </div>

      {/* Adjust form */}
      <div className="p-5">
        <AdjustForm userId={user.id} />
      </div>

      {/* Ledger */}
      <div className="border-t border-line-subtle px-5 py-4">
        <p className="text-sm font-semibold text-ink-primary">Transaction ledger</p>
        <p className="mt-0.5 text-xs text-ink-tertiary">
          Most recent first. Append-only — every row is preserved.
        </p>
        <div className="mt-3 overflow-x-auto">
          {isLoading ? (
            <div className="flex flex-col gap-1.5">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-11" />
              ))}
            </div>
          ) : (data?.transactions || []).length === 0 ? (
            <EmptyState
              icon={Coins}
              size="sm"
              bordered={false}
              title="No activity yet"
              description="Transactions will appear here once the customer earns or redeems points."
            />
          ) : (
            <div className="rounded-xl border border-line-subtle bg-bg-elevated overflow-hidden">
              <table className="w-full min-w-[640px]">
                <thead>
                  <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                    <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">Reason</th>
                    <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">Description</th>
                    <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">Ref</th>
                    <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">When</th>
                    <th className="px-4 py-2.5 text-right text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {data.transactions.map((t) => (
                    <LedgerRow key={t.id} tx={t} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function UsersTab() {
  const [picked, setPicked] = useState(null);
  return (
    <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
      <UserPicker onPick={setPicked} picked={picked} />
      {picked ? (
        <UserLoyaltyPanel user={picked} onChange={setPicked} />
      ) : (
        <div className="rounded-xl border border-line-subtle bg-bg-elevated p-8">
          <EmptyState
            icon={UsersIcon}
            bordered={false}
            title="Pick a customer"
            description="Search and select a customer on the left to view their balance, ledger, and adjustment controls."
          />
        </div>
      )}
    </div>
  );
}

// ---- Tiers tab ----

const EMPTY_TIER = {
  name: '',
  cost_points: '',
  discount_type: 'percent',
  discount_value: '',
  max_discount: '',
  expires_after_days: 30,
  is_active: true,
};

function TierForm({ initial, mode, onCancel, onSaved }) {
  const [form, setForm] = useState(initial || EMPTY_TIER);
  const [error, setError] = useState(null);
  const create = useAdminCreateTier();
  const update = useAdminUpdateTier();

  useEffect(() => {
    setForm(initial || EMPTY_TIER);
    setError(null);
  }, [initial]);

  const pending = create.isPending || update.isPending;

  function set(k) {
    return (e) => {
      const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      setForm((f) => ({ ...f, [k]: v }));
    };
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (!form.name.trim()) return setError('Name is required.');
    const cost = Number(form.cost_points);
    if (!cost || cost < 1) return setError('Cost must be a positive number.');
    const value = Number(form.discount_value);
    if (!value || value <= 0) return setError('Discount value must be > 0.');
    if (form.discount_type === 'percent' && value > 100) {
      return setError('Percent value cannot exceed 100.');
    }
    const payload = {
      name: form.name.trim(),
      cost_points: cost,
      discount_type: form.discount_type,
      discount_value: value,
      max_discount: form.max_discount === '' ? null : Number(form.max_discount),
      expires_after_days: Number(form.expires_after_days) || 30,
      is_active: !!form.is_active,
    };
    try {
      if (mode === 'edit' && initial?.id) {
        await update.mutateAsync({ tierId: initial.id, data: payload });
      } else {
        await create.mutateAsync(payload);
      }
      onSaved?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not save the tier.');
    }
  }

  return (
    <motion.form
      variants={scaleIn}
      initial="hidden"
      animate="show"
      onSubmit={submit}
      className="mb-6 rounded-xl border border-line-subtle bg-bg-elevated p-6 shadow-md"
    >
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-full bg-accent/12 text-accent">
            <Gift className="size-5" aria-hidden="true" />
          </div>
          <h2 className="text-h3 font-semibold tracking-tight text-ink-primary">
            {mode === 'edit' ? 'Edit tier' : 'New redemption tier'}
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={onCancel}
          className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="Name"
          required
          placeholder="10% off"
          value={form.name}
          onChange={set('name')}
          helper="What customers see on the rewards page."
        />
        <Input
          label="Cost in points"
          type="number"
          min="1"
          step="1"
          required
          value={form.cost_points}
          onChange={set('cost_points')}
        />
        <Select label="Discount type" value={form.discount_type} onChange={set('discount_type')}>
          <option value="percent">Percent (%)</option>
          <option value="fixed">Fixed amount</option>
        </Select>
        <Input
          label={form.discount_type === 'percent' ? 'Percent off' : 'Amount off'}
          type="number"
          step="0.01"
          min="0"
          required
          value={form.discount_value}
          onChange={set('discount_value')}
        />
        <Input
          label="Max discount (optional)"
          type="number"
          step="0.01"
          min="0"
          value={form.max_discount}
          onChange={set('max_discount')}
          helper={
            form.discount_type === 'percent'
              ? 'Caps a percent discount.'
              : 'Usually unused for fixed amounts.'
          }
        />
        <Input
          label="Coupon validity (days)"
          type="number"
          min="1"
          max="365"
          step="1"
          value={form.expires_after_days}
          onChange={set('expires_after_days')}
        />
      </div>

      <label
        className={cn(
          'mt-4 flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 text-sm transition-all duration-150',
          form.is_active
            ? 'border-success/40 bg-success/8 text-success'
            : 'border-line-subtle bg-bg-sunken text-ink-secondary',
        )}
      >
        <input
          type="checkbox"
          checked={form.is_active}
          onChange={set('is_active')}
          className="sr-only"
        />
        <span
          className={cn(
            'grid size-5 place-items-center rounded border transition-colors',
            form.is_active ? 'border-success bg-success text-white' : 'border-line-strong bg-bg-elevated',
          )}
        >
          {form.is_active && <CheckCircle2 className="size-3.5" strokeWidth={2.5} />}
        </span>
        <span>
          <span className="block font-medium">Active</span>
          <span className="text-xs text-ink-tertiary">Customers can redeem this tier</span>
        </span>
      </label>

      {error && (
        <p className="mt-4 rounded-lg border border-danger/30 bg-danger/8 px-3 py-2 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="mt-6 flex justify-end gap-3">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" loading={pending}>
          {mode === 'edit' ? 'Save changes' : 'Create tier'}
        </Button>
      </div>
    </motion.form>
  );
}

function describeTierReward(tier) {
  if (tier.discount_type === 'percent') {
    const cap = tier.max_discount ? ` (up to ${formatPrice(tier.max_discount)})` : '';
    return `${Number(tier.discount_value)}% off${cap}`;
  }
  return `${formatPrice(tier.discount_value)} off`;
}

function TierRow({ tier, onEdit }) {
  const del = useAdminDeleteTier();
  const update = useAdminUpdateTier();
  const [confirming, setConfirming] = useState(false);
  const pending = del.isPending || update.isPending;

  function toggleActive() {
    update.mutate({ tierId: tier.id, data: { is_active: !tier.is_active } });
  }

  return (
    <tr className="group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60">
      <td className="px-5 py-3.5">
        <p className="text-sm font-semibold text-ink-primary">{tier.name}</p>
      </td>
      <td className="px-5 py-3.5">
        <span className="nums text-sm text-ink-secondary">
          {tier.cost_points.toLocaleString()} pts
        </span>
      </td>
      <td className="px-5 py-3.5 text-sm text-ink-secondary">{describeTierReward(tier)}</td>
      <td className="px-5 py-3.5">
        <span className="nums text-sm text-ink-tertiary">{tier.expires_after_days}d</span>
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            role="switch"
            aria-checked={tier.is_active}
            aria-label={tier.is_active ? 'Deactivate' : 'Activate'}
            disabled={pending}
            onClick={toggleActive}
            className={cn(
              'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200',
              'focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40',
              tier.is_active ? 'bg-accent' : 'bg-fill-strong',
            )}
          >
            <span
              className={cn(
                'pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
                tier.is_active ? 'translate-x-4' : 'translate-x-0',
              )}
            />
          </button>
          <Badge tone={tier.is_active ? 'success' : 'neutral'} size="sm">
            {tier.is_active ? 'Active' : 'Off'}
          </Badge>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center justify-end gap-1">
          {confirming ? (
            <>
              <Button
                variant="destructive"
                size="sm"
                loading={del.isPending}
                onClick={() =>
                  del.mutate(tier.id, { onSuccess: () => setConfirming(false) })
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
            </>
          ) : (
            <>
              <button
                type="button"
                aria-label="Edit"
                disabled={pending}
                onClick={() => onEdit(tier)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
              >
                <Pencil className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Delete"
                disabled={pending}
                onClick={() => setConfirming(true)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
              >
                <Trash2 className="size-4" />
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

function TiersTab() {
  const { data: tiers = [], isLoading, isError, refetch } = useAdminTiers();
  const [editing, setEditing] = useState(null);

  const isFormOpen = editing !== null;
  const initial =
    editing && editing !== 'new'
      ? {
          id: editing.id,
          name: editing.name,
          cost_points: editing.cost_points,
          discount_type: editing.discount_type,
          discount_value: editing.discount_value,
          max_discount: editing.max_discount ?? '',
          expires_after_days: editing.expires_after_days,
          is_active: editing.is_active,
        }
      : null;

  return (
    <div>
      <div className="mb-5 flex items-center justify-between gap-4">
        <p className="text-sm text-ink-secondary">
          Customers see active tiers on the /rewards page and redeem them for one-time coupons.
        </p>
        {!isFormOpen && (
          <Button onClick={() => setEditing('new')}>
            <Plus className="size-4" aria-hidden="true" /> New tier
          </Button>
        )}
      </div>

      {isFormOpen && (
        <TierForm
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={initial}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}

      {isError ? (
        <EmptyState
          icon={Gift}
          iconTone="danger"
          title="Couldn't load tiers"
          description="Please try again."
          action={<Button size="sm" onClick={() => refetch()}>Retry</Button>}
        />
      ) : isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14" />)}
        </div>
      ) : tiers.length === 0 ? (
        <EmptyState
          icon={Gift}
          title="No redemption tiers yet"
          description="Create one so customers can spend their points."
          action={!isFormOpen && (
            <Button onClick={() => setEditing('new')}>
              <Plus className="size-4" aria-hidden="true" /> New tier
            </Button>
          )}
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Name</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Cost</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Reward</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Valid</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Status</th>
                <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tiers.map((t) => (
                <TierRow key={t.id} tier={t} onEdit={setEditing} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Referrals tab ----

function ReferralsTab() {
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(1);
  const opts = useMemo(
    () => ({ status: statusFilter || undefined, page, page_size: 50 }),
    [statusFilter, page],
  );
  useEffect(() => setPage(1), [statusFilter]);

  const { data, isLoading } = useAdminReferrals(opts);
  const items = data?.items || [];
  const total = data?.total || 0;

  return (
    <div>
      <div className="mb-4 max-w-xs">
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All referrals</option>
          <option value="pending">Pending only</option>
          <option value="completed">Completed only</option>
        </Select>
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={Send}
          title="No referrals yet"
          description="Customers can share their referral code from /rewards to start inviting friends."
        />
      ) : (
        <>
          <p className="mb-2 text-xs text-ink-tertiary">
            <span className="nums font-medium text-ink-secondary">{total.toLocaleString()}</span> total
          </p>
          <div className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Referrer</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Friend</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Code</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Signed up</th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Completed</th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id} className="border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60">
                    <td className="px-5 py-3 text-sm text-ink-primary">{r.referrer_email}</td>
                    <td className="px-5 py-3 text-sm text-ink-primary">{r.referred_email}</td>
                    <td className="px-5 py-3 font-mono text-xs text-ink-tertiary">{r.code}</td>
                    <td className="px-5 py-3 text-xs text-ink-tertiary">{formatDate(r.created_at)}</td>
                    <td className="px-5 py-3 text-xs text-ink-tertiary">
                      {formatDate(r.completed_at) || '—'}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Badge
                        tone={r.status === 'completed' ? 'success' : 'info'}
                        dot
                        size="sm"
                      >
                        {r.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ---- Maintenance tab ----

function MaintenanceTab() {
  const expire = useAdminExpirePoints();
  const [lastResult, setLastResult] = useState(null);

  function run() {
    expire.mutate(undefined, {
      onSuccess: (data) => setLastResult({ ok: true, ...data, ranAt: new Date() }),
      onError: (err) =>
        setLastResult({
          ok: false,
          message:
            err?.response?.data?.error?.message ||
            'Could not run expiry. Check logs.',
        }),
    });
  }

  return (
    <div className="max-w-2xl">
      <div className="rounded-xl border border-line-subtle bg-bg-elevated p-6 shadow-sm">
        <div className="flex items-start gap-4">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-warning/12 text-warning">
            <Timer className="size-6" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-h3 font-semibold tracking-tight text-ink-primary">Run points expiry</h3>
            <p className="mt-1.5 text-sm text-ink-secondary">
              Walks every customer&apos;s ledger oldest-first. Earn rows past
              their <code className="font-mono text-xs">expires_at</code> that
              haven&apos;t been fully consumed produce an <code className="font-mono text-xs">EXPIRY</code> debit.
              Idempotent — safe to run any time. Schedule this nightly in production.
            </p>
            <div className="mt-5">
              <Button onClick={run} loading={expire.isPending}>
                <Timer className="size-4" aria-hidden="true" /> Run expiry sweep now
              </Button>
            </div>
          </div>
        </div>

        {lastResult && (
          <motion.div
            variants={fadeUp}
            initial="hidden"
            animate="show"
            className={cn(
              'mt-5 rounded-lg border p-4 text-sm',
              lastResult.ok
                ? 'border-success/30 bg-success/8 text-ink-primary'
                : 'border-danger/30 bg-danger/8 text-danger',
            )}
          >
            {lastResult.ok ? (
              <>
                <p className="flex items-center gap-2 font-semibold">
                  <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
                  Expiry complete
                </p>
                <div className="mt-2 grid grid-cols-3 gap-3">
                  {[
                    { label: 'Users processed', value: lastResult.users_processed },
                    { label: 'Rows expired', value: lastResult.rows_expired },
                    { label: 'Points expired', value: lastResult.points_expired?.toLocaleString() },
                  ].map(({ label, value }) => (
                    <div key={label} className="rounded-lg bg-bg-elevated p-3 text-center">
                      <p className="nums text-lg font-bold text-ink-primary">{value}</p>
                      <p className="text-[10px] text-ink-tertiary">{label}</p>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="flex items-center gap-2">
                <span>{lastResult.message}</span>
              </p>
            )}
          </motion.div>
        )}
      </div>
    </div>
  );
}

// ---- Earn rules tab ----

const RULE_ICONS = {
  signup_bonus: Sparkles,
  place_order: ShoppingBag,
  write_review: Star,
};

function EarnRulesTab() {
  const { data: rules = [], isLoading } = useAdminEarnRules();
  const update = useAdminUpdateEarnRule();
  const [edits, setEdits] = useState({});
  const [savedRule, setSavedRule] = useState(null);

  function localEdit(rule, patch) {
    setEdits((cur) => ({
      ...cur,
      [rule.id]: { ...rule, ...(cur[rule.id] || {}), ...patch },
    }));
  }

  async function save(rule) {
    const draft = edits[rule.id];
    if (!draft) return;
    const payload = {};
    if (Number(draft.points_value) !== rule.points_value) {
      payload.points_value = Number(draft.points_value);
    }
    if (draft.is_active !== rule.is_active) {
      payload.is_active = !!draft.is_active;
    }
    if (Object.keys(payload).length === 0) return;
    try {
      await update.mutateAsync({ ruleId: rule.id, data: payload });
      setEdits((cur) => {
        const next = { ...cur };
        delete next[rule.id];
        return next;
      });
      setSavedRule(rule.id);
      setTimeout(() => setSavedRule((cur) => (cur === rule.id ? null : cur)), 1500);
    } catch (_err) {
      /* swallow */
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <p className="mb-5 text-sm text-ink-secondary">
        Each rule is triggered by a domain event (signup, paid order, review).
        Adjust the points value or toggle a rule off to disable it without a
        code change.
      </p>

      <motion.div
        className="flex flex-col gap-3"
        variants={staggerContainer(0.06)}
        initial="hidden"
        animate="show"
      >
        {rules.map((rule) => {
          const draft = edits[rule.id] || rule;
          const dirty =
            Number(draft.points_value) !== rule.points_value ||
            draft.is_active !== rule.is_active;
          const Icon = RULE_ICONS[rule.key] || Award;
          const hint =
            rule.key === 'place_order'
              ? 'Points per ₹1 of order subtotal (after discount).'
              : rule.key === 'signup_bonus'
                ? 'One-time bonus on account registration.'
                : 'Points per submitted review.';
          const isSaved = savedRule === rule.id;
          return (
            <motion.div
              key={rule.id}
              variants={fadeUp}
              className={cn(
                'rounded-xl border bg-bg-elevated p-5 transition-colors duration-200',
                draft.is_active ? 'border-line-subtle' : 'border-line-subtle opacity-60',
              )}
            >
              <div className="flex items-start gap-4">
                <span
                  className={cn(
                    'grid size-10 shrink-0 place-items-center rounded-full transition-colors',
                    draft.is_active ? 'bg-accent/12 text-accent' : 'bg-fill text-ink-tertiary',
                  )}
                >
                  <Icon className="size-5" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-ink-primary">
                      {rule.display_name}
                    </h3>
                    <code className="rounded bg-fill px-1.5 py-0.5 font-mono text-[10px] text-ink-tertiary">
                      {rule.key}
                    </code>
                    <Badge tone={draft.is_active ? 'success' : 'neutral'} size="sm" dot>
                      {draft.is_active ? 'Active' : 'Off'}
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-ink-tertiary">{hint}</p>

                  <div className="mt-4 grid gap-3 sm:grid-cols-[180px_auto_auto] sm:items-end">
                    <Input
                      label="Points value"
                      type="number"
                      min="0"
                      step="1"
                      value={draft.points_value}
                      onChange={(e) =>
                        localEdit(rule, { points_value: e.target.value })
                      }
                    />
                    <label className="flex cursor-pointer items-center gap-2 self-center pb-px text-sm text-ink-secondary">
                      <span
                        className={cn(
                          'grid size-5 place-items-center rounded border transition-colors',
                          draft.is_active
                            ? 'border-accent bg-accent text-white'
                            : 'border-line-strong bg-bg-elevated',
                        )}
                      >
                        {draft.is_active && <CheckCircle2 className="size-3" strokeWidth={2.5} />}
                        <input
                          type="checkbox"
                          checked={!!draft.is_active}
                          onChange={(e) =>
                            localEdit(rule, { is_active: e.target.checked })
                          }
                          className="sr-only"
                        />
                      </span>
                      Active
                    </label>
                    <Button
                      size="sm"
                      variant={isSaved ? 'secondary' : 'primary'}
                      disabled={!dirty && !isSaved}
                      loading={update.isPending && update.variables?.ruleId === rule.id}
                      onClick={() => save(rule)}
                    >
                      {isSaved ? (
                        <>
                          <CheckCircle2 className="size-4" aria-hidden="true" /> Saved
                        </>
                      ) : (
                        'Save'
                      )}
                    </Button>
                  </div>
                </div>
              </div>
            </motion.div>
          );
        })}
      </motion.div>
    </div>
  );
}

// ---- VIP tiers tab ----

const EMPTY_VIP_FORM = {
  name: '',
  threshold_lifetime_points: '',
  earn_multiplier: '1.00',
  benefits: '',
  color: '#CD7F32',
  sort_order: 0,
};

function VipTierForm({ initial, mode, onCancel, onSaved }) {
  const [form, setForm] = useState(initial || EMPTY_VIP_FORM);
  const [error, setError] = useState(null);
  const create = useAdminCreateVipTier();
  const update = useAdminUpdateVipTier();

  useEffect(() => {
    setForm(initial || EMPTY_VIP_FORM);
    setError(null);
  }, [initial]);

  const pending = create.isPending || update.isPending;

  function set(k) {
    return (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (!form.name.trim()) return setError('Name is required.');
    const thr = Number(form.threshold_lifetime_points);
    if (Number.isNaN(thr) || thr < 0) return setError('Threshold must be ≥ 0.');
    const mult = Number(form.earn_multiplier);
    if (Number.isNaN(mult) || mult < 1 || mult > 10) {
      return setError('Multiplier must be between 1.00 and 10.00.');
    }
    const payload = {
      name: form.name.trim(),
      threshold_lifetime_points: thr,
      earn_multiplier: mult,
      benefits: form.benefits?.trim() || null,
      color: form.color?.trim() || null,
      sort_order: Number(form.sort_order) || 0,
    };
    try {
      if (mode === 'edit' && initial?.id) {
        await update.mutateAsync({ tierId: initial.id, data: payload });
      } else {
        await create.mutateAsync(payload);
      }
      onSaved?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not save the tier.');
    }
  }

  return (
    <motion.form
      variants={scaleIn}
      initial="hidden"
      animate="show"
      onSubmit={submit}
      className="mb-6 rounded-xl border border-line-subtle bg-bg-elevated p-6 shadow-md"
    >
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-full bg-warning/12 text-warning">
            <Award className="size-5" aria-hidden="true" />
          </div>
          <h2 className="text-h3 font-semibold tracking-tight text-ink-primary">
            {mode === 'edit' ? 'Edit VIP tier' : 'New VIP tier'}
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={onCancel}
          className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Input label="Name" required placeholder="Silver" value={form.name} onChange={set('name')} />
        <Input
          label="Threshold (lifetime pts)"
          type="number"
          min="0"
          step="1"
          value={form.threshold_lifetime_points}
          onChange={set('threshold_lifetime_points')}
          helper="Lowest tier should be 0."
        />
        <Input
          label="Earn multiplier"
          type="number"
          min="1"
          max="10"
          step="0.01"
          value={form.earn_multiplier}
          onChange={set('earn_multiplier')}
          helper="1.00 = no boost, 1.25 = 25% extra on every earn."
        />
        <Input
          label="Badge color"
          type="text"
          placeholder="#C0C0C0"
          value={form.color}
          onChange={set('color')}
          helper="Hex code for the storefront badge."
        />
      </div>

      <div className="mt-1">
        <Input
          label="Benefits (optional)"
          placeholder="25% bonus on every points earn."
          value={form.benefits}
          onChange={set('benefits')}
          maxLength={500}
        />
      </div>

      <Input
        label="Sort order"
        type="number"
        min="0"
        step="1"
        value={form.sort_order}
        onChange={set('sort_order')}
        helper="Lower numbers appear first in the storefront ladder."
      />

      {error && (
        <p className="mt-4 rounded-lg border border-danger/30 bg-danger/8 px-3 py-2 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="mt-6 flex justify-end gap-3">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" loading={pending}>
          {mode === 'edit' ? 'Save changes' : 'Create tier'}
        </Button>
      </div>
    </motion.form>
  );
}

function VipTierRow({ tier, onEdit }) {
  const del = useAdminDeleteVipTier();
  const [confirming, setConfirming] = useState(false);
  return (
    <tr className="group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60">
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span
            className="inline-block size-3 rounded-full border border-line-subtle shadow-sm"
            style={{ backgroundColor: tier.color || 'transparent' }}
            aria-hidden="true"
          />
          <div>
            <p className="text-sm font-semibold text-ink-primary">{tier.name}</p>
            {tier.benefits && (
              <p className="mt-0.5 text-xs text-ink-tertiary">{tier.benefits}</p>
            )}
          </div>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <span className="nums text-sm text-ink-secondary">
          {tier.threshold_lifetime_points.toLocaleString()}
        </span>
      </td>
      <td className="px-5 py-3.5">
        <Badge tone="accent" size="sm">
          <span className="nums">{Number(tier.earn_multiplier).toFixed(2)}</span>×
        </Badge>
      </td>
      <td className="px-5 py-3.5">
        <span className="nums text-sm text-ink-tertiary">{tier.sort_order}</span>
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center justify-end gap-1">
          {confirming ? (
            <>
              <Button
                variant="destructive"
                size="sm"
                loading={del.isPending}
                onClick={() =>
                  del.mutate(tier.id, { onSuccess: () => setConfirming(false) })
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
            </>
          ) : (
            <>
              <button
                type="button"
                aria-label="Edit"
                onClick={() => onEdit(tier)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
              >
                <Pencil className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Delete"
                onClick={() => setConfirming(true)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
              >
                <Trash2 className="size-4" />
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

function VipTiersTab() {
  const { data: tiers = [], isLoading } = useAdminVipTiers();
  const [editing, setEditing] = useState(null);
  const isFormOpen = editing !== null;
  const initial =
    editing && editing !== 'new'
      ? {
          id: editing.id,
          name: editing.name,
          threshold_lifetime_points: editing.threshold_lifetime_points,
          earn_multiplier: String(editing.earn_multiplier),
          benefits: editing.benefits || '',
          color: editing.color || '',
          sort_order: editing.sort_order,
        }
      : null;

  return (
    <div>
      <div className="mb-5 flex items-center justify-between gap-4">
        <p className="text-sm text-ink-secondary">
          Customers move up automatically when their lifetime points cross a threshold. The multiplier applies to every positive earn.
        </p>
        {!isFormOpen && (
          <Button onClick={() => setEditing('new')}>
            <Plus className="size-4" aria-hidden="true" /> New tier
          </Button>
        )}
      </div>

      {isFormOpen && (
        <VipTierForm
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={initial}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}

      {isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : tiers.length === 0 ? (
        <EmptyState
          icon={Award}
          title="No VIP tiers configured"
          description="Add at least one tier with threshold 0 so every customer has a starting tier."
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Tier</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Threshold</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Multiplier</th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Sort</th>
                <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tiers.map((t) => (
                <VipTierRow key={t.id} tier={t} onEdit={setEditing} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Page shell ----

export default function AdminLoyaltyPage() {
  const [tab, setTab] = useState('users');

  const tabs = [
    { id: 'users',       label: 'Customers',        icon: UsersIcon },
    { id: 'tiers',       label: 'Redemption tiers', icon: Coins },
    { id: 'earn-rules',  label: 'Earn rules',        icon: TrendingUp },
    { id: 'vip-tiers',  label: 'VIP tiers',         icon: Award },
    { id: 'referrals',  label: 'Referrals',         icon: Send },
    { id: 'maintenance',label: 'Maintenance',        icon: Timer },
  ];

  return (
    <AdminPage
      title="Loyalty"
      description="Track customer points balances, configure redemption tiers, and manage the referral program."
    >
      {/* Tab bar */}
      <div
        role="tablist"
        aria-label="Loyalty sections"
        className="mb-6 flex flex-wrap gap-1 rounded-xl border border-line-subtle bg-bg-elevated p-1.5"
      >
        {tabs.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(t.id)}
              className={cn(
                'inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-all duration-150 focus-visible:focus-ring',
                active
                  ? 'bg-accent/12 text-accent shadow-sm'
                  : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'users'       && <UsersTab />}
      {tab === 'tiers'       && <TiersTab />}
      {tab === 'earn-rules'  && <EarnRulesTab />}
      {tab === 'vip-tiers'   && <VipTiersTab />}
      {tab === 'referrals'   && <ReferralsTab />}
      {tab === 'maintenance' && <MaintenanceTab />}
    </AdminPage>
  );
}
