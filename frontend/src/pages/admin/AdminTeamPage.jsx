import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Search,
  Pencil,
  X,
  ShieldCheck,
  Users as UsersIcon,
  ChevronLeft,
  ChevronRight,
  KeyRound,
  Loader2,
  UserPlus,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { toast } from '@/components/ui/Toaster.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, staggerContainer } from '@/lib/motion.js';
import {
  useUsers,
  useUpdateUser,
  useTriggerPasswordReset,
  useInviteStaff,
} from '@/features/users/hooks.js';
import { useRoles, useAssignUserRoles } from '@/features/roles/hooks.js';
import { useAuthStore, useHasPermission } from '@/features/auth/store.js';

const PAGE_SIZE = 25;

function useDebounced(value, ms = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function RoleBadge({ name, isAdmin }) {
  return (
    <Badge tone={isAdmin ? 'accent' : 'neutral'} size="sm">
      {name}
    </Badge>
  );
}

function RoleAssignForm({ user, roles, onClose }) {
  const [selected, setSelected] = useState(() =>
    new Set((user.roles || []).map((r) => r.id)),
  );
  const [error, setError] = useState(null);
  const assign = useAssignUserRoles();

  function toggle(id) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSave() {
    setError(null);
    try {
      await assign.mutateAsync({ userId: user.id, roleIds: Array.from(selected) });
      onClose();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not update roles.');
    }
  }

  return (
    <tr className="bg-bg-sunken">
      <td colSpan={5} className="px-4 py-4">
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          className="rounded-lg border border-line-subtle bg-bg-elevated p-5 shadow-md"
        >
          <div className="mb-4 flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-ink-primary">
                Assign roles
              </p>
              <p className="mt-0.5 font-mono text-xs text-ink-tertiary">{user.email}</p>
            </div>
            <button
              type="button"
              aria-label="Cancel"
              onClick={onClose}
              className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <X className="size-4" />
            </button>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            {roles.length === 0 ? (
              <p className="col-span-2 text-sm text-ink-tertiary">
                No roles defined yet. Create one on the Roles page first.
              </p>
            ) : (
              roles.map((r) => (
                <label
                  key={r.id}
                  className={cn(
                    'flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 text-sm transition-all duration-150',
                    selected.has(r.id)
                      ? 'border-accent/40 bg-accent/8 shadow-glow-sm'
                      : 'border-line-subtle bg-bg-sunken hover:border-line-strong hover:bg-fill',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(r.id)}
                    onChange={() => toggle(r.id)}
                    className="mt-0.5 size-4 rounded border border-line-subtle bg-bg-sunken text-accent focus-visible:focus-ring"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium text-ink-primary">{r.name}</span>
                    {r.description && (
                      <span className="block text-xs text-ink-tertiary">
                        {r.description}
                      </span>
                    )}
                    <span className="mt-0.5 block text-[10px] text-ink-tertiary">
                      <span className="nums">{r.permissions?.length || 0}</span> permission
                      {r.permissions?.length === 1 ? '' : 's'}
                    </span>
                  </span>
                </label>
              ))
            )}
          </div>

          {error && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-danger">
              {error}
            </p>
          )}

          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={assign.isPending}>
              Cancel
            </Button>
            <Button onClick={handleSave} loading={assign.isPending}>
              Save roles
            </Button>
          </div>
        </motion.div>
      </td>
    </tr>
  );
}

/** Inline full-name editor — same expand-a-row affordance as RoleAssignForm. */
function NameEditForm({ user, onClose }) {
  const [name, setName] = useState(user.full_name || '');
  const update = useUpdateUser();

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed === (user.full_name || '')) {
      onClose();
      return;
    }
    try {
      await update.mutateAsync({
        userId: user.id,
        data: { full_name: trimmed || null },
      });
      toast.success(trimmed ? 'Name updated.' : 'Name cleared.');
      onClose();
    } catch (err) {
      toast.error(
        err.response?.data?.error?.message || 'Could not update the name.',
      );
    }
  }

  return (
    <tr className="bg-bg-sunken">
      <td colSpan={5} className="px-4 py-4">
        <motion.form
          variants={fadeUp}
          initial="hidden"
          animate="show"
          onSubmit={handleSubmit}
          className="rounded-lg border border-line-subtle bg-bg-elevated p-5 shadow-md"
        >
          <div className="mb-4 flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-ink-primary">Edit name</p>
              <p className="mt-0.5 font-mono text-xs text-ink-tertiary">{user.email}</p>
            </div>
            <button
              type="button"
              aria-label="Cancel"
              onClick={onClose}
              className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <X className="size-4" />
            </button>
          </div>

          <div className="max-w-sm">
            <Input
              label="Full name"
              placeholder="Priya Sharma"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={255}
              helper="Leave blank to clear the name."
            />
          </div>

          <div className="mt-5 flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              disabled={update.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" loading={update.isPending}>
              Save name
            </Button>
          </div>
        </motion.form>
      </td>
    </tr>
  );
}

/** Confirm step for enabling/disabling an account, with the session-revocation
 *  consequence spelled out. Rendered as an expansion row below the user. */
function StatusConfirmRow({ user, onClose }) {
  const update = useUpdateUser();
  const disabling = user.is_active;

  async function handleConfirm() {
    try {
      await update.mutateAsync({
        userId: user.id,
        data: { is_active: !user.is_active },
      });
      toast.success(
        disabling
          ? `${user.email} has been disabled and signed out of all devices.`
          : `${user.email} has been re-enabled and can log in again.`,
      );
      onClose();
    } catch (err) {
      toast.error(
        err.response?.data?.error?.message || 'Could not update the account.',
      );
    }
  }

  return (
    <tr className="bg-bg-sunken">
      <td colSpan={5} className="px-4 py-4">
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          role="alertdialog"
          aria-label={
            disabling ? `Disable ${user.email}` : `Re-enable ${user.email}`
          }
          className="rounded-lg border border-line-subtle bg-bg-elevated p-5 shadow-md"
        >
          <p className="text-sm font-semibold text-ink-primary">
            {disabling ? 'Disable this account?' : 'Re-enable this account?'}
          </p>
          <p className="mt-1.5 max-w-2xl text-sm text-ink-secondary">
            {disabling ? (
              <>
                <span className="font-medium text-ink-primary">{user.email}</span>{' '}
                will be signed out of every device immediately — all active
                sessions are revoked — and will not be able to log in until the
                account is re-enabled.
              </>
            ) : (
              <>
                <span className="font-medium text-ink-primary">{user.email}</span>{' '}
                will be able to log in again. Sessions revoked while the account
                was disabled stay signed out, so a fresh login is required.
              </>
            )}
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={update.isPending}>
              Cancel
            </Button>
            <Button
              variant={disabling ? 'destructive' : 'primary'}
              loading={update.isPending}
              onClick={handleConfirm}
            >
              {disabling ? 'Disable account' : 'Re-enable account'}
            </Button>
          </div>
        </motion.div>
      </td>
    </tr>
  );
}

/** Invite a colleague. Rendered as a panel above the table rather than a modal,
 *  matching the expand-in-place idiom the rest of this page uses. */
function InviteForm({ roles, onClose }) {
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [selected, setSelected] = useState(() => new Set());
  const [error, setError] = useState(null);
  const invite = useInviteStaff();

  function toggle(id) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    try {
      const res = await invite.mutateAsync({
        email: email.trim(),
        full_name: fullName.trim() || null,
        role_ids: Array.from(selected),
      });
      toast.success(res?.detail || 'Invitation sent.');
      onClose();
    } catch (err) {
      setError(
        err.response?.data?.error?.message || 'Could not send the invitation.',
      );
    }
  }

  return (
    <motion.form
      variants={fadeUp}
      initial="hidden"
      animate="show"
      onSubmit={handleSubmit}
      className="rounded-xl border border-line-subtle bg-bg-elevated p-5 shadow-md"
    >
      <div className="mb-4 flex items-start justify-between">
        <div>
          <p className="text-sm font-semibold text-ink-primary">
            Invite a team member
          </p>
          <p className="mt-0.5 text-xs text-ink-tertiary">
            They receive an email with a one-time code to set their own
            password. Nobody — including you — ever sees it.
          </p>
        </div>
        <button
          type="button"
          aria-label="Cancel"
          onClick={onClose}
          className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          label="Email"
          type="email"
          required
          placeholder="colleague@shopwellvia.in"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Input
          label="Full name"
          placeholder="Priya Sharma"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          maxLength={255}
        />
      </div>

      <p className="mb-2 mt-4 text-xs font-medium text-ink-secondary">
        Roles — you can only grant permissions you hold yourself.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {roles.length === 0 ? (
          <p className="col-span-2 text-sm text-ink-tertiary">
            No roles defined yet. Create one on the Roles page first.
          </p>
        ) : (
          roles.map((r) => (
            <label
              key={r.id}
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 text-sm transition-all duration-150',
                selected.has(r.id)
                  ? 'border-accent/40 bg-accent/8 shadow-glow-sm'
                  : 'border-line-subtle bg-bg-sunken hover:border-line-strong hover:bg-fill',
              )}
            >
              <input
                type="checkbox"
                checked={selected.has(r.id)}
                onChange={() => toggle(r.id)}
                className="mt-0.5 size-4 rounded border border-line-subtle bg-bg-sunken text-accent focus-visible:focus-ring"
              />
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-ink-primary">{r.name}</span>
                {r.description && (
                  <span className="block text-xs text-ink-tertiary">
                    {r.description}
                  </span>
                )}
              </span>
            </label>
          ))
        )}
      </div>

      {error && <p className="mt-3 text-xs text-danger">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={onClose}
          disabled={invite.isPending}
        >
          Cancel
        </Button>
        <Button type="submit" loading={invite.isPending} disabled={!email.trim()}>
          Send invitation
        </Button>
      </div>
    </motion.form>
  );
}

function UserRow({
  user,
  roles,
  canManage,
  canAssignRoles,
  actorIsSuperadmin,
  actorId,
  expandedMode,
  onExpand,
}) {
  const initial = (user.email || '?').charAt(0).toUpperCase();
  const reset = useTriggerPasswordReset();

  // The backend only lets a superadmin modify another superadmin account —
  // mirror that shield here so we don't offer controls that guarantee a 403.
  const targetLocked = user.is_admin && !actorIsSuperadmin;
  const showManage = canManage && !targetLocked;
  // Nobody edits their own access. Mirrors the server guard so the control
  // isn't offered at all rather than failing on click.
  const isSelf = user.id === actorId;

  function handlePasswordReset() {
    reset.mutate(user.id, {
      onSuccess: (res) =>
        toast.success(
          res?.detail || "Password reset code sent to the user's email.",
        ),
      onError: (err) =>
        toast.error(
          err.response?.data?.error?.message ||
            'Could not send the password reset email.',
        ),
    });
  }

  return (
    <>
      <motion.tr
        variants={fadeUp}
        className={cn(
          'group border-t border-line-subtle transition-colors duration-150',
          expandedMode ? 'bg-accent/4' : 'hover:bg-fill/60',
        )}
      >
        <td className="px-5 py-3.5">
          <div className="flex items-center gap-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
              {initial}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-ink-primary">{user.email}</p>
              {user.full_name && (
                <p className="truncate text-xs text-ink-tertiary">{user.full_name}</p>
              )}
            </div>
          </div>
        </td>
        <td className="px-5 py-3.5">
          <div className="flex flex-wrap items-center gap-1.5">
            {user.is_admin && <RoleBadge name="superadmin" isAdmin />}
            {(user.roles || []).map((r) => (
              <RoleBadge key={r.id} name={r.name} />
            ))}
            {isSelf && (
              <span className="text-[10px] uppercase tracking-wider text-ink-tertiary">
                you
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-ink-tertiary">
            <span className="nums">{(user.permissions || []).length}</span>{' '}
            permission{(user.permissions || []).length === 1 ? '' : 's'}
            {user.totp_enabled && ' · 2FA on'}
          </p>
        </td>
        <td className="px-5 py-3.5 text-xs text-ink-secondary">
          {user.last_login_at ? (
            new Date(user.last_login_at).toLocaleDateString(undefined, {
              day: 'numeric',
              month: 'short',
              year: 'numeric',
            })
          ) : (
            // NULL means "not since the column existed", never "never".
            <span className="text-ink-tertiary">Unknown</span>
          )}
        </td>
        <td className="px-5 py-3.5">
          <div className="flex items-center gap-2">
            {showManage && (
              <button
                type="button"
                role="switch"
                aria-checked={user.is_active}
                aria-label={
                  user.is_active
                    ? `Disable ${user.email}`
                    : `Enable ${user.email}`
                }
                onClick={() => onExpand('status')}
                className={cn(
                  'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200',
                  'focus-visible:focus-ring',
                  user.is_active ? 'bg-accent' : 'bg-fill-strong',
                )}
              >
                <span
                  className={cn(
                    'pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
                    user.is_active ? 'translate-x-4' : 'translate-x-0',
                  )}
                />
              </button>
            )}
            <Badge tone={user.is_active ? 'success' : 'neutral'} dot>
              {user.is_active ? 'Active' : 'Disabled'}
            </Badge>
          </div>
        </td>
        <td className="px-5 py-3.5">
          <div className="flex items-center justify-end gap-1">
            {showManage && (
              <>
                <button
                  type="button"
                  aria-label={`Edit name for ${user.email}`}
                  onClick={() => onExpand('name')}
                  className={cn(
                    'grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors focus-visible:focus-ring',
                    expandedMode === 'name'
                      ? 'bg-accent/12 text-accent'
                      : 'hover:bg-fill hover:text-ink-primary',
                  )}
                >
                  <Pencil className="size-4" />
                </button>
                <button
                  type="button"
                  aria-label={`Send password reset email to ${user.email}`}
                  title="Send password reset email"
                  disabled={reset.isPending}
                  onClick={handlePasswordReset}
                  className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40"
                >
                  {reset.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <KeyRound className="size-4" />
                  )}
                </button>
              </>
            )}
            {canAssignRoles && !isSelf && (
              <button
                type="button"
                aria-label={`Edit roles for ${user.email}`}
                title="Assign roles"
                onClick={() => onExpand('roles')}
                className={cn(
                  'grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors focus-visible:focus-ring',
                  expandedMode === 'roles'
                    ? 'bg-accent/12 text-accent'
                    : 'hover:bg-fill hover:text-ink-primary',
                )}
              >
                <ShieldCheck className="size-4" />
              </button>
            )}
          </div>
        </td>
      </motion.tr>
      {expandedMode === 'roles' && (
        <RoleAssignForm user={user} roles={roles} onClose={() => onExpand(null)} />
      )}
      {expandedMode === 'name' && (
        <NameEditForm user={user} onClose={() => onExpand(null)} />
      )}
      {expandedMode === 'status' && (
        <StatusConfirmRow user={user} onClose={() => onExpand(null)} />
      )}
    </>
  );
}

export default function AdminTeamPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebounced(search, 250);
  // { id, mode: 'roles' | 'name' | 'status' } — at most one expansion open.
  const [expanded, setExpanded] = useState(null);
  const [inviting, setInviting] = useState(false);

  // Route is gated at users.view; finer-grained controls are gated per action.
  // UX only — the API re-checks every permission server-side.
  const canManage = useHasPermission('users.manage');
  const canAssignRoles = useHasPermission('users.assign_role');
  const canInvite = useHasPermission('users.invite');
  const actorIsSuperadmin = useAuthStore((s) => !!s.user?.is_admin);
  const actorId = useAuthStore((s) => s.user?.id);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  // `scope: 'staff'` is what makes this the TEAM page: shoppers are excluded
  // entirely, so the role-assign control can never land on a customer row.
  const { data, isLoading, isError, refetch } = useUsers({
    q: debouncedSearch,
    scope: 'staff',
    page,
    page_size: PAGE_SIZE,
  });
  const { data: roles = [] } = useRoles();

  const items = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <AdminPage
      title="Team"
      description={
        isLoading
          ? 'Loading…'
          : `${total} staff account${total === 1 ? '' : 's'} — everyone with admin access. Shoppers live under Customers.`
      }
      action={
        canInvite && !inviting ? (
          <Button size="sm" onClick={() => setInviting(true)}>
            <UserPlus className="size-4" /> Invite member
          </Button>
        ) : null
      }
    >
      {inviting && (
        <InviteForm roles={roles} onClose={() => setInviting(false)} />
      )}

      {/* Search bar */}
      <div className="max-w-sm">
        <Input
          icon={Search}
          placeholder="Search by email or name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isError ? (
        <EmptyState
          icon={UsersIcon}
          iconTone="danger"
          title="Couldn't load the team"
          description="Something went wrong. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <div className="border-b border-line-subtle px-5 py-3">
            <Skeleton variant="text" lines={1} className="w-32" />
          </div>
          <div className="divide-y divide-line-subtle">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                <Skeleton variant="circle" className="size-9 shrink-0" />
                <div className="flex-1">
                  <Skeleton variant="text" lines={1} className="w-48 mb-1" />
                  <Skeleton variant="text" lines={1} className="w-28" />
                </div>
                <Skeleton className="h-5 w-16 rounded-full" />
              </div>
            ))}
          </div>
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title={
            debouncedSearch
              ? 'No team members match your search'
              : 'No staff accounts yet'
          }
          description={
            debouncedSearch
              ? 'Try a different email or name.'
              : 'Invite a colleague, or grant a role to an existing account.'
          }
        />
      ) : (
        <>
          <motion.div
            className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md"
            variants={staggerContainer(0.02)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[760px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    User
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Access
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Last login
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Status
                  </th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((u) => (
                  <UserRow
                    key={u.id}
                    user={u}
                    roles={roles}
                    canManage={canManage}
                    canAssignRoles={canAssignRoles}
                    actorIsSuperadmin={actorIsSuperadmin}
                    actorId={actorId}
                    expandedMode={expanded?.id === u.id ? expanded.mode : null}
                    onExpand={(mode) =>
                      setExpanded((cur) =>
                        !mode || (cur?.id === u.id && cur.mode === mode)
                          ? null
                          : { id: u.id, mode },
                      )
                    }
                  />
                ))}
              </tbody>
            </table>
          </motion.div>

          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between gap-2">
              <p className="text-xs text-ink-tertiary">
                Showing <span className="nums font-medium text-ink-secondary">{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)}</span> of{' '}
                <span className="nums font-medium text-ink-secondary">{total}</span> team members
              </p>
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink-tertiary">
                  Page <span className="nums">{page}</span> of <span className="nums">{totalPages}</span>
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  aria-label="Previous page"
                  className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:bg-fill hover:border-line-strong focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  aria-label="Next page"
                  className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:bg-fill hover:border-line-strong focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronRight className="size-4" />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </AdminPage>
  );
}
