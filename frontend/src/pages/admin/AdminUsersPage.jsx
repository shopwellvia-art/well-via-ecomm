import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Search, Pencil, X, ShieldCheck, Users as UsersIcon, ChevronLeft, ChevronRight } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, listStagger, staggerContainer } from '@/lib/motion.js';
import { useUsers } from '@/features/users/hooks.js';
import { useRoles, useAssignUserRoles } from '@/features/roles/hooks.js';

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
      <td colSpan={4} className="px-4 py-4">
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

function UserRow({ user, roles, isEditing, onToggleEdit }) {
  const initial = (user.email || '?').charAt(0).toUpperCase();
  return (
    <>
      <motion.tr
        variants={fadeUp}
        className={cn(
          'group border-t border-line-subtle transition-colors duration-150',
          isEditing ? 'bg-accent/4' : 'hover:bg-fill/60',
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
          <div className="flex flex-wrap gap-1.5">
            {user.is_admin && <RoleBadge name="admin (legacy)" isAdmin />}
            {(user.roles || []).map((r) => (
              <RoleBadge key={r.id} name={r.name} />
            ))}
            {!user.is_admin && (user.roles || []).length === 0 && (
              <span className="text-xs text-ink-tertiary">— shopper —</span>
            )}
          </div>
        </td>
        <td className="px-5 py-3.5">
          <Badge tone={user.is_active ? 'success' : 'neutral'} dot>
            {user.is_active ? 'Active' : 'Disabled'}
          </Badge>
        </td>
        <td className="px-5 py-3.5 text-right">
          <button
            type="button"
            aria-label={`Edit roles for ${user.email}`}
            onClick={onToggleEdit}
            className={cn(
              'grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors focus-visible:focus-ring',
              isEditing
                ? 'bg-accent/12 text-accent'
                : 'hover:bg-fill hover:text-ink-primary',
            )}
          >
            <Pencil className="size-4" />
          </button>
        </td>
      </motion.tr>
      {isEditing && (
        <RoleAssignForm user={user} roles={roles} onClose={onToggleEdit} />
      )}
    </>
  );
}

export default function AdminUsersPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebounced(search, 250);
  const [editingId, setEditingId] = useState(null);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const { data, isLoading, isError, refetch } = useUsers({
    q: debouncedSearch,
    page,
    page_size: PAGE_SIZE,
  });
  const { data: roles = [] } = useRoles();

  const items = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <AdminPage
      title="Users"
      description={
        isLoading
          ? 'Loading…'
          : `${total} user${total === 1 ? '' : 's'} — assign roles to grant admin access.`
      }
    >
      {/* Search bar */}
      <div className="mb-5 max-w-sm">
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
          title="Couldn't load users"
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
          title={debouncedSearch ? 'No users match your search' : 'No users yet'}
          description={
            debouncedSearch
              ? 'Try a different email or name.'
              : 'When customers register they will appear here.'
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
            <table className="w-full min-w-[640px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    User
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Roles
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
                    isEditing={editingId === u.id}
                    onToggleEdit={() =>
                      setEditingId((cur) => (cur === u.id ? null : u.id))
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
                <span className="nums font-medium text-ink-secondary">{total}</span> users
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
