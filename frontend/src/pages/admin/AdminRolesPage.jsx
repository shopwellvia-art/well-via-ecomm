import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Plus, Trash2, Pencil, X, ShieldCheck, Lock, Users as UsersIcon, Check } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, scaleIn, listStagger } from '@/lib/motion.js';
import {
  useRoles,
  usePermissions,
  useCreateRole,
  useUpdateRole,
  useDeleteRole,
} from '@/features/roles/hooks.js';

const EMPTY_FORM = { name: '', description: '', permission_ids: [] };

function groupPermissions(permissions) {
  const groups = {};
  for (const p of permissions) {
    const key = p.group_name || 'Other';
    (groups[key] ||= []).push(p);
  }
  return Object.entries(groups)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, perms]) => [name, perms.sort((a, b) => a.name.localeCompare(b.name))]);
}

function PermissionPicker({ permissions, selected, onChange, disabled }) {
  const grouped = useMemo(() => groupPermissions(permissions), [permissions]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  function toggle(id) {
    const next = new Set(selectedSet);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(Array.from(next));
  }

  function toggleGroup(perms) {
    const ids = perms.map((p) => p.id);
    const allSelected = ids.every((id) => selectedSet.has(id));
    const next = new Set(selectedSet);
    if (allSelected) ids.forEach((id) => next.delete(id));
    else ids.forEach((id) => next.add(id));
    onChange(Array.from(next));
  }

  if (!permissions?.length) {
    return (
      <p className="text-sm text-ink-tertiary">Permissions list isn&apos;t loaded yet.</p>
    );
  }

  return (
    <div className="grid gap-3 md:grid-cols-2">
      {grouped.map(([groupName, perms]) => {
        const allSelected = perms.every((p) => selectedSet.has(p.id));
        const someSelected = !allSelected && perms.some((p) => selectedSet.has(p.id));
        const selectedCount = perms.filter((p) => selectedSet.has(p.id)).length;
        return (
          <fieldset
            key={groupName}
            className={cn(
              'rounded-xl border bg-bg-sunken p-4 transition-colors duration-150',
              allSelected ? 'border-accent/30 bg-accent/4' : 'border-line-subtle',
            )}
          >
            {/* Group header */}
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <legend className="text-xs font-semibold uppercase tracking-wider text-ink-secondary">
                  {groupName}
                </legend>
                {(allSelected || someSelected) && (
                  <span className="nums rounded-full bg-accent/12 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                    {selectedCount}/{perms.length}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => toggleGroup(perms)}
                disabled={disabled}
                className={cn(
                  'rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide transition-colors focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40',
                  allSelected
                    ? 'bg-accent/12 text-accent hover:bg-accent/25'
                    : 'text-ink-tertiary hover:bg-fill hover:text-ink-secondary',
                )}
              >
                {allSelected ? 'Clear all' : 'Select all'}
              </button>
            </div>

            <div className="flex flex-col gap-1.5">
              {perms.map((p) => {
                const checked = selectedSet.has(p.id);
                return (
                  <label
                    key={p.id}
                    className={cn(
                      'flex cursor-pointer items-start gap-2.5 rounded-lg px-3 py-2 text-sm transition-all duration-150',
                      checked
                        ? 'bg-accent/10 text-ink-primary'
                        : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                      disabled && 'cursor-not-allowed opacity-50',
                    )}
                  >
                    {/* Custom checkbox visual */}
                    <span
                      className={cn(
                        'mt-0.5 grid size-4 shrink-0 place-items-center rounded border transition-colors',
                        checked
                          ? 'border-accent bg-accent'
                          : 'border-line-strong bg-bg-elevated',
                      )}
                    >
                      {checked && <Check className="size-2.5 text-white" strokeWidth={3} />}
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => toggle(p.id)}
                        className="sr-only"
                      />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono text-xs font-medium text-ink-primary">
                        {p.name}
                      </span>
                      {p.description && (
                        <span className="block text-[11px] text-ink-tertiary">
                          {p.description}
                        </span>
                      )}
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        );
      })}
    </div>
  );
}

function RoleForm({ initial, mode, permissions, onCancel, onSaved }) {
  const [form, setForm] = useState(initial || EMPTY_FORM);
  const [error, setError] = useState(null);
  const create = useCreateRole();
  const update = useUpdateRole();

  useEffect(() => {
    setForm(initial || EMPTY_FORM);
    setError(null);
  }, [initial]);

  const pending = create.isPending || update.isPending;
  const isSystem = !!initial?.is_system;

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (!form.name.trim()) {
      setError('Role name is required.');
      return;
    }
    try {
      if (mode === 'edit' && initial?.id) {
        await update.mutateAsync({
          id: initial.id,
          data: {
            ...(isSystem ? {} : { name: form.name.trim() }),
            description: form.description.trim() || null,
            permission_ids: form.permission_ids,
          },
        });
      } else {
        await create.mutateAsync({
          name: form.name.trim(),
          description: form.description.trim() || null,
          permission_ids: form.permission_ids,
        });
      }
      onSaved?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not save the role.');
    }
  }

  return (
    <motion.form
      variants={scaleIn}
      initial="hidden"
      animate="show"
      onSubmit={handleSubmit}
      className="mb-6 rounded-xl border border-line-subtle bg-bg-elevated p-6 shadow-md"
    >
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-full bg-accent/12 text-accent">
            <ShieldCheck className="size-5" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-h3 font-semibold tracking-tight text-ink-primary">
              {mode === 'edit' ? 'Edit role' : 'New role'}
            </h2>
            {isSystem && (
              <div className="mt-0.5 flex items-center gap-1">
                <Badge tone="neutral" size="sm">
                  <Lock className="size-2.5" aria-hidden="true" /> System
                </Badge>
                <span className="text-xs text-ink-tertiary">cannot be renamed or deleted</span>
              </div>
            )}
          </div>
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
          placeholder="manager"
          value={form.name}
          disabled={isSystem}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          helper={isSystem ? 'System roles cannot be renamed.' : 'Lowercase, short, e.g. "manager".'}
        />
        <Input
          label="Description (optional)"
          placeholder="Can manage products & orders"
          value={form.description}
          onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
        />
      </div>

      <div className="mt-5">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm font-semibold text-ink-secondary">
            Permissions
          </p>
          <Badge tone={form.permission_ids.length > 0 ? 'accent' : 'neutral'} size="sm">
            <span className="nums">{form.permission_ids.length}</span> selected
          </Badge>
        </div>
        <PermissionPicker
          permissions={permissions}
          selected={form.permission_ids}
          onChange={(ids) => setForm((f) => ({ ...f, permission_ids: ids }))}
          disabled={pending}
        />
      </div>

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
          {mode === 'edit' ? 'Save changes' : 'Create role'}
        </Button>
      </div>
    </motion.form>
  );
}

function RoleRow({ role, onEdit }) {
  const del = useDeleteRole();
  const [confirming, setConfirming] = useState(false);

  return (
    <motion.tr variants={fadeUp} className="group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60">
      <td className="px-5 py-4">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'grid size-8 shrink-0 place-items-center rounded-full',
              role.is_system ? 'bg-fill text-ink-tertiary' : 'bg-accent/12 text-accent',
            )}
          >
            <ShieldCheck className="size-4" aria-hidden="true" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm font-semibold text-ink-primary">{role.name}</p>
              {role.is_system && (
                <Badge tone="neutral" size="sm">
                  <Lock className="size-2.5" aria-hidden="true" /> System
                </Badge>
              )}
            </div>
            {role.description && (
              <p className="mt-0.5 text-xs text-ink-tertiary">{role.description}</p>
            )}
          </div>
        </div>
      </td>
      <td className="px-5 py-4">
        <Badge tone={role.permissions?.length > 0 ? 'accent' : 'neutral'} size="sm">
          <span className="nums">{role.permissions?.length ?? 0}</span> permission{role.permissions?.length === 1 ? '' : 's'}
        </Badge>
      </td>
      <td className="px-5 py-4">
        <div className="flex items-center justify-end gap-1">
          {confirming ? (
            <div className="flex items-center gap-2">
              <p className="text-xs text-ink-secondary">Delete this role?</p>
              <Button
                variant="destructive"
                size="sm"
                loading={del.isPending}
                onClick={() =>
                  del.mutate(role.id, { onSuccess: () => setConfirming(false) })
                }
              >
                Confirm delete
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
                aria-label={`Edit ${role.name}`}
                onClick={() => onEdit(role)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
              >
                <Pencil className="size-4" />
              </button>
              <button
                type="button"
                aria-label={`Delete ${role.name}`}
                disabled={role.is_system}
                title={role.is_system ? 'System roles cannot be deleted' : undefined}
                onClick={() => setConfirming(true)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
              >
                <Trash2 className="size-4" />
              </button>
            </>
          )}
        </div>
      </td>
    </motion.tr>
  );
}

export default function AdminRolesPage() {
  const { data: roles = [], isLoading, isError, refetch } = useRoles();
  const { data: permissions = [], isLoading: permsLoading } = usePermissions();
  const [editing, setEditing] = useState(null);

  const isFormOpen = editing !== null;
  const formInitial =
    editing && editing !== 'new'
      ? {
          id: editing.id,
          name: editing.name,
          description: editing.description || '',
          permission_ids: editing.permissions?.map((p) => p.id) || [],
          is_system: editing.is_system,
        }
      : null;

  return (
    <AdminPage
      title="Roles & permissions"
      description={
        isLoading
          ? 'Loading…'
          : `${roles.length} role${roles.length === 1 ? '' : 's'} — assign to staff to control admin access.`
      }
      action={
        !isFormOpen && (
          <Button onClick={() => setEditing('new')}>
            <Plus className="size-4" aria-hidden="true" />
            New role
          </Button>
        )
      }
    >
      {isFormOpen && (
        <RoleForm
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={formInitial}
          permissions={permissions}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}

      {isError ? (
        <EmptyState
          icon={ShieldCheck}
          iconTone="danger"
          title="Couldn't load roles"
          description="Something went wrong. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading || permsLoading ? (
        <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <div className="border-b border-line-subtle px-5 py-3 bg-bg-sunken/60">
            <Skeleton variant="text" lines={1} className="w-40" />
          </div>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 border-t border-line-subtle px-5 py-4">
              <Skeleton variant="circle" className="size-8 shrink-0" />
              <div className="flex-1">
                <Skeleton variant="text" lines={1} className="w-32 mb-1" />
                <Skeleton variant="text" lines={1} className="w-52" />
              </div>
              <Skeleton className="h-5 w-24 rounded-full" />
            </div>
          ))}
        </div>
      ) : roles.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No roles yet"
          description="The system roles should always exist — try restarting the API."
        />
      ) : (
        <>
          <motion.div
            className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md"
            variants={listStagger(0.03)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[560px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Role
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Permissions
                  </th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {roles.map((r) => (
                  <RoleRow key={r.id} role={r} onEdit={setEditing} />
                ))}
              </tbody>
            </table>
          </motion.div>

          <p className="mt-4 flex items-center gap-2 text-xs text-ink-tertiary">
            <UsersIcon className="size-3.5" aria-hidden="true" />
            Assign roles to individual staff from the Team page.
          </p>
        </>
      )}
    </AdminPage>
  );
}
