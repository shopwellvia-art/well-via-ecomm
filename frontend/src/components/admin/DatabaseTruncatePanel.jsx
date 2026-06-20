import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Database,
  Loader2,
  Sprout,
  Trash2,
} from 'lucide-react';
import { useAuthStore } from '@/features/auth/store.js';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { scaleIn } from '@/lib/motion.js';
import {
  useDatabaseGroups,
  useSeedScope,
  useTruncateScope,
} from '@/features/system/hooks.js';

const EVERYTHING = 'everything';

function errorMessage(err, fallback) {
  return err?.response?.data?.error?.message || fallback;
}

/** The extra tables a scoped wipe pulls in beyond its own (FK dependents). */
function extraClearedTables(group) {
  const primary = new Set(group.primary_tables);
  return group.cleared_tables.filter((t) => !primary.has(t));
}

/**
 * One scoped domain group: a row-count, a blast-radius disclosure, a Truncate
 * (arm → type confirm phrase → delete) flow and an optional Seed button.
 * Session-safe — scoped wipes never touch the operator's own account.
 */
function ScopedGroupCard({ group }) {
  const truncate = useTruncateScope();
  const seed = useSeedScope();

  const [armed, setArmed] = useState(false);
  const [confirm, setConfirm] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState(null); // { tone, text }

  const matches = confirm === group.confirm_phrase;
  const busy = truncate.isPending || seed.isPending;
  const extras = extraClearedTables(group);
  const isEmpty = group.row_count === 0;

  function reset() {
    setArmed(false);
    setConfirm('');
  }

  function onTruncate() {
    if (!matches || busy) return;
    setStatus(null);
    truncate.mutate(
      { scope: group.key, confirm },
      {
        onSuccess: (data) => {
          reset();
          setStatus({ tone: 'success', text: data.detail });
        },
        onError: (err) =>
          setStatus({ tone: 'danger', text: errorMessage(err, 'Truncate failed — check server logs.') }),
      },
    );
  }

  function onSeed() {
    if (busy) return;
    setStatus(null);
    seed.mutate(group.key, {
      onSuccess: (data) => setStatus({ tone: 'success', text: data.detail }),
      onError: (err) =>
        setStatus({ tone: 'danger', text: errorMessage(err, 'Seeding failed — check server logs.') }),
    });
  }

  return (
    <div className="rounded-xl border border-line-subtle bg-bg-elevated p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink-primary">{group.label}</p>
          <p className="mt-1 text-xs text-ink-secondary">{group.description}</p>
        </div>
        <Badge tone={isEmpty ? 'neutral' : 'info'} size="sm" className="shrink-0">
          {group.row_count.toLocaleString()} {group.row_count === 1 ? 'row' : 'rows'}
        </Badge>
      </div>

      {/* Blast radius — disclose the FK-dependent tables this also clears. */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-3 inline-flex items-center gap-1 text-xs text-ink-tertiary hover:text-ink-secondary"
      >
        <ChevronDown
          className={`size-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
        Clears {group.cleared_tables.length}{' '}
        {group.cleared_tables.length === 1 ? 'table' : 'tables'}
        {extras.length > 0 && ` (incl. ${extras.length} dependent)`}
      </button>
      {expanded && (
        <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-ink-tertiary">
          {group.cleared_tables.join(', ')}
        </p>
      )}

      {!armed ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              setStatus(null);
              setArmed(true);
            }}
            disabled={busy || isEmpty}
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
            Truncate
          </Button>
          {group.seedable && (
            <Button variant="outline" size="sm" onClick={onSeed} loading={seed.isPending} disabled={busy}>
              <Sprout className="size-3.5" aria-hidden="true" />
              Seed sample data
            </Button>
          )}
        </div>
      ) : (
        <div className="mt-3 rounded-lg border border-danger/30 bg-danger/4 p-3">
          <Input
            label={
              <>
                Type{' '}
                <span className="font-mono font-semibold text-danger">{group.confirm_phrase}</span>{' '}
                to confirm
              </>
            }
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={group.confirm_phrase}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            disabled={truncate.isPending}
          />
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <Button
              variant="destructive"
              size="sm"
              onClick={onTruncate}
              loading={truncate.isPending}
              disabled={!matches || truncate.isPending}
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Delete {group.label.toLowerCase()}
            </Button>
            <Button variant="ghost" size="sm" onClick={reset} disabled={truncate.isPending}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {status && (
        <p
          className={`mt-2.5 flex items-start gap-1.5 text-xs ${
            status.tone === 'success' ? 'text-success' : 'text-danger'
          }`}
        >
          {status.tone === 'success' ? (
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          )}
          <span>{status.text}</span>
        </p>
      )}
    </div>
  );
}

/**
 * The nuclear option: wipe every table + re-seed the bootstrap admin. On
 * success the operator's own account is gone, so we clear local auth and bounce
 * to /login to sign back in as the re-seeded admin.
 */
function EverythingPanel({ group }) {
  const truncate = useTruncateScope();
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  const [armed, setArmed] = useState(false);
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState(null);

  const matches = confirm === group.confirm_phrase;
  const pending = truncate.isPending;

  function reset() {
    setArmed(false);
    setConfirm('');
    setError(null);
  }

  function onConfirm() {
    if (!matches || pending) return;
    setError(null);
    truncate.mutate(
      { scope: EVERYTHING, confirm },
      {
        onSuccess: () => {
          // Our own session is dead now — clear local auth and send to login.
          logout();
          navigate('/login', { replace: true });
        },
        onError: (err) =>
          setError(
            errorMessage(
              err,
              'Truncate failed. The database may be partially affected — check the server logs.',
            ),
          ),
      },
    );
  }

  return (
    <div className="mt-6 rounded-xl border border-danger/40 bg-danger/4 p-5">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-danger/12 text-danger">
          <Database className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-danger">Truncate everything</p>
            <Badge tone="danger" size="sm">
              {group.row_count.toLocaleString()} rows
            </Badge>
          </div>
          <p className="mt-1 text-xs text-ink-secondary">
            {group.description} This <strong>cannot be undone</strong>.
          </p>

          {!armed ? (
            <div className="mt-4">
              <Button variant="destructive" onClick={() => setArmed(true)}>
                <Trash2 className="size-4" aria-hidden="true" />
                Truncate database…
              </Button>
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-danger/30 bg-bg-elevated p-4">
              <Input
                label={
                  <>
                    Type{' '}
                    <span className="font-mono font-semibold text-danger">
                      {group.confirm_phrase}
                    </span>{' '}
                    to confirm
                  </>
                }
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={group.confirm_phrase}
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                disabled={pending}
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  variant="destructive"
                  onClick={onConfirm}
                  loading={pending}
                  disabled={!matches || pending}
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                  Permanently delete everything
                </Button>
                <Button variant="outline" onClick={reset} disabled={pending}>
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {error && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/8 px-3.5 py-3 text-xs text-danger">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          {pending && !error && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-ink-tertiary">
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
              Wiping and re-seeding… you'll be signed out when it finishes.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Superadmin-only "danger zone" for resetting the database by domain.
 *
 * Self-gates on `is_admin` (the full-access tier the product treats as
 * superadmin) and renders nothing for everyone else. Each scoped group can be
 * truncated (FK-safe on the backend) and optionally re-seeded with sample data;
 * the full "everything" reset re-creates the bootstrap admin and signs you out.
 * The backend independently enforces the same gate via require_admin.
 */
export function DatabaseTruncatePanel() {
  const isSuperadmin = useAuthStore((s) => !!s.user?.is_admin);
  const { data: groups, isLoading, isError, error } = useDatabaseGroups();

  // Hard gate — non-superadmins get nothing rendered at all.
  if (!isSuperadmin) return null;

  const scoped = (groups || []).filter((g) => g.key !== EVERYTHING);
  const everything = (groups || []).find((g) => g.key === EVERYTHING);

  return (
    <motion.section
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className="mt-8 rounded-xl border border-danger/40 bg-danger/4 p-5"
      aria-labelledby="db-danger-heading"
    >
      <div className="mb-1 flex items-center gap-2">
        <AlertOctagon className="size-4 text-danger" aria-hidden="true" />
        <h3 id="db-danger-heading" className="text-sm font-semibold text-danger">
          Reset &amp; seed data
        </h3>
        <Badge tone="danger" size="sm">
          Superadmin only
        </Badge>
      </div>
      <p className="mb-4 text-xs text-ink-secondary">
        Empty a single area of the database, then optionally reload it with sample data. Each
        truncate clears its own tables plus any rows that depend on them. Scoped wipes keep you
        signed in.
      </p>

      {isLoading && (
        <p className="flex items-center gap-2 py-6 text-xs text-ink-tertiary">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading database groups…
        </p>
      )}

      {isError && (
        <div className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/8 px-3.5 py-3 text-xs text-danger">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{errorMessage(error, 'Could not load database groups.')}</span>
        </div>
      )}

      {!isLoading && !isError && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {scoped.map((group) => (
              <ScopedGroupCard key={group.key} group={group} />
            ))}
          </div>
          {everything && <EverythingPanel group={everything} />}
        </>
      )}
    </motion.section>
  );
}
