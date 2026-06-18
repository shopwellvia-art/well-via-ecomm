  import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { AlertOctagon, Database, Trash2, CheckCircle2, AlertTriangle } from 'lucide-react';
import { useAuthStore } from '@/features/auth/store.js';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { scaleIn } from '@/lib/motion.js';

// Must match database_admin_service.CONFIRM_PHRASE on the backend.
const CONFIRM_PHRASE = 'DELETE EVERYTHING';

/**
 * Superadmin-only "danger zone" for wiping the database.
 *
 * Self-gates on `is_admin` (the full-access tier the product treats as
 * superadmin) and renders nothing for everyone else — so even staff who can
 * open the Settings page via `settings.manage` never see it. The backend
 * endpoint independently enforces the same gate via require_admin.
 *
 * On success the caller's own account is wiped and re-seeded, so we clear local
 * auth and bounce to /login; the operator signs back in as the re-seeded admin.
 */
export function DatabaseTruncatePanel() {
  const isSuperadmin = useAuthStore((s) => !!s.user?.is_admin);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  const [armed, setArmed] = useState(false);
  const [confirm, setConfirm] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  // Hard gate — non-superadmins get nothing rendered at all.
  if (!isSuperadmin) return null;

  const matches = confirm === CONFIRM_PHRASE;

  function reset() {
    setArmed(false);
    setConfirm('');
    setError(null);
  }

  async function onConfirm() {
    if (!matches || pending) return;
    setPending(true);
    setError(null);
    try {
      // Lazy import keeps the system slice out of the main settings bundle.
      const { systemApi } = await import('@/features/system/api.js');
      await systemApi.truncateDatabase(confirm);
      // Our own session is dead now — clear local auth and send to login.
      logout();
      navigate('/login', { replace: true });
    } catch (err) {
      setPending(false);
      setError(
        err?.response?.data?.error?.message ||
          'Truncate failed. The database may be partially affected — check the server logs.',
      );
    }
  }

  return (
    <motion.section
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className="mt-8 rounded-xl border border-danger/40 bg-danger/4 p-5"
      aria-labelledby="db-danger-heading"
    >
      <div className="mb-3 flex items-center gap-2">
        <AlertOctagon className="size-4 text-danger" aria-hidden="true" />
        <h3 id="db-danger-heading" className="text-sm font-semibold text-danger">
          Danger zone
        </h3>
        <Badge tone="danger" size="sm">
          Superadmin only
        </Badge>
      </div>

      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-danger/12 text-danger">
          <Database className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink-primary">Truncate database</p>
          <p className="mt-1 text-xs text-ink-secondary">
            Permanently empties <strong>every table</strong> — orders, products, customers,
            reviews, coupons, payments and all other data. The bootstrap admin account and the
            role/permission set are then re-created so you can sign back in. This{' '}
            <strong>cannot be undone</strong>, and it will sign you out immediately.
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
                    Type <span className="font-mono font-semibold text-danger">{CONFIRM_PHRASE}</span> to
                    confirm
                  </>
                }
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={CONFIRM_PHRASE}
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
    </motion.section>
  );
}
