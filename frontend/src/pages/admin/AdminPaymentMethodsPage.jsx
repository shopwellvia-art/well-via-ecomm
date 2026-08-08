import { useState } from 'react';
import {
  Search,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  Check,
  Eye,
  EyeOff,
  CreditCard,
  Save,
  CheckCircle2,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeIn, fadeUp, listStagger } from '@/lib/motion.js';
import {
  usePaymentMethods,
  useUpdatePaymentMethod,
} from '@/features/paymentMethods/hooks.js';

// ---------------------------------------------------------------------------
// Toggle Switch — matches AdminCouponsPage toggle style
// ---------------------------------------------------------------------------
function ToggleSwitch({ checked, onChange, disabled, title }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      title={title}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full',
        'border-2 border-transparent transition-colors duration-200 focus-visible:focus-ring',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:pointer-events-none',
        checked ? 'bg-accent' : 'bg-fill-strong',
      )}
    >
      <span
        className={cn(
          'pointer-events-none block h-4 w-4 rounded-full bg-white shadow',
          'transform transition-transform duration-200',
          checked ? 'translate-x-4' : 'translate-x-0',
        )}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Segmented control (Sandbox / Live)
// ---------------------------------------------------------------------------
function SegmentedControl({ value, onChange, options }) {
  return (
    <div
      role="group"
      className="inline-flex rounded-md border border-line-subtle bg-bg-sunken p-0.5"
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            'rounded-sm px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus-visible:focus-ring',
            value === opt.value
              ? 'bg-bg-elevated text-ink-primary shadow-sm'
              : 'text-ink-secondary hover:text-ink-primary',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Secret field with visibility toggle
// ---------------------------------------------------------------------------
function SecretInput({ field, value, onChange }) {
  const [visible, setVisible] = useState(false);
  const EyeIcon = visible ? EyeOff : Eye;

  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-ink-primary">
        {field.label}
        {field.required && (
          <span className="ml-0.5 text-danger" aria-hidden="true">
            *
          </span>
        )}
      </label>
      <div className="relative">
        <input
          type={visible ? 'text' : 'password'}
          autoComplete="new-password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            field.set
              ? '••••••• (saved — type to replace)'
              : field.placeholder || ''
          }
          className={cn(
            'h-10 w-full rounded-lg bg-bg-sunken px-3.5 pr-10 text-sm text-ink-primary',
            'border border-line-subtle placeholder:text-ink-tertiary',
            'transition-[border-color,box-shadow] duration-200 hover:border-line-strong',
            'focus-visible:border-accent focus-visible:outline-none',
          )}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? 'Hide value' : 'Show value'}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-tertiary hover:text-ink-secondary focus-visible:focus-ring"
        >
          <EyeIcon className="size-4" aria-hidden="true" />
        </button>
      </div>
      {field.set && (
        <p className="flex items-center gap-1.5 text-[11px] text-ink-tertiary">
          <EyeOff className="size-3" aria-hidden="true" />
          Value hidden. Type to replace it.
        </p>
      )}
      {field.help && (
        <p className="text-xs text-ink-tertiary">{field.help}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider initial chip — shown when no logo is available
// ---------------------------------------------------------------------------
function ProviderChip({ code, name }) {
  // Derive a 2-letter initial from the provider name
  const initials = (name || code || '?')
    .split(/[\s_-]+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() || '')
    .join('');

  return (
    <span
      className="grid size-9 shrink-0 place-items-center rounded-lg bg-fill-strong font-bold text-sm text-ink-secondary"
      aria-hidden="true"
    >
      {initials}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Environment badge helper
// ---------------------------------------------------------------------------
function EnvironmentBadge({ env }) {
  if (!env) return null;
  const tone = env === 'live' ? 'success' : 'info';
  return (
    <Badge tone={tone} size="sm" dot>
      {env === 'live' ? 'Live' : 'Sandbox'}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Single gateway row
// ---------------------------------------------------------------------------
function GatewayRow({ gw }) {
  const update = useUpdatePaymentMethod();

  const [expanded, setExpanded] = useState(false);
  const [dirtyCredentials, setDirtyCredentials] = useState({});
  const [dirtyEnv, setDirtyEnv] = useState(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [savedOk, setSavedOk] = useState(false);

  const missingRequired = (gw.fields || [])
    .filter((f) => f.required && !f.set)
    .map((f) => f.label);

  const effectiveEnv = dirtyEnv ?? gw.environment;

  function handleCredentialChange(key, val) {
    setDirtyCredentials((prev) => ({ ...prev, [key]: val }));
    setSavedOk(false);
  }

  async function handleToggle(newEnabled) {
    setSaveError(null);
    setSavedOk(false);

    if (newEnabled && !gw.ready) {
      setExpanded(true);
      return;
    }

    setSaving(true);
    try {
      await update.mutateAsync({ code: gw.code, payload: { enabled: newEnabled } });
      setSavedOk(true);
    } catch (err) {
      setSaveError(
        err?.response?.data?.detail ||
          err?.response?.data?.error?.message ||
          'Could not update the gateway.',
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveKeys(e) {
    e.preventDefault();
    setSaveError(null);
    setSavedOk(false);

    const credentials = {};
    for (const [k, v] of Object.entries(dirtyCredentials)) {
      credentials[k] = v;
    }

    const payload = {};
    if (Object.keys(credentials).length > 0) payload.credentials = credentials;
    if (dirtyEnv !== null) payload.environment = dirtyEnv;

    if (Object.keys(payload).length === 0) return;

    setSaving(true);
    try {
      await update.mutateAsync({ code: gw.code, payload });
      setDirtyCredentials({});
      setDirtyEnv(null);
      setSavedOk(true);
    } catch (err) {
      setSaveError(
        err?.response?.data?.detail ||
          err?.response?.data?.error?.message ||
          'Could not save credentials.',
      );
    } finally {
      setSaving(false);
    }
  }

  const hasCredentialDirt =
    Object.keys(dirtyCredentials).length > 0 || dirtyEnv !== null;

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border bg-bg-elevated transition-all duration-200',
        expanded ? 'border-accent/30 shadow-md' : 'border-line-subtle hover:shadow-sm',
      )}
    >
      {/* Row header */}
      <div className="flex items-center gap-4 p-4">
        {/* Provider chip */}
        <ProviderChip code={gw.code} name={gw.name} />

        {/* Name + description + badges */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-ink-primary">{gw.name}</p>

            {gw.enabled && gw.implemented && (
              <Badge tone="success" dot size="sm">Enabled</Badge>
            )}
            {!gw.implemented && (
              <Badge tone="neutral" size="sm">Coming soon</Badge>
            )}
            {gw.implemented && gw.enabled && !gw.ready && (
              <Badge tone="warning" size="sm">Keys required</Badge>
            )}
            {gw.environment && gw.enabled && (
              <EnvironmentBadge env={gw.environment} />
            )}
          </div>
          <p className="mt-0.5 text-xs text-ink-secondary">{gw.description}</p>
        </div>

        {/* Toggle + expand */}
        <div className="flex shrink-0 items-center gap-3">
          {savedOk && !hasCredentialDirt && (
            <motion.span
              variants={fadeIn}
              initial="hidden"
              animate="show"
              className="flex items-center gap-1 text-xs text-success"
            >
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
              Saved
            </motion.span>
          )}
          <ToggleSwitch
            checked={gw.enabled}
            onChange={handleToggle}
            disabled={!gw.implemented || saving}
            title={
              !gw.implemented
                ? 'Integration coming soon — keys can be saved now'
                : undefined
            }
          />
          <button
            type="button"
            aria-label={expanded ? 'Collapse credentials' : 'Expand credentials'}
            onClick={() => setExpanded((o) => !o)}
            className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
          >
            {expanded ? (
              <ChevronUp className="size-4" aria-hidden="true" />
            ) : (
              <ChevronDown className="size-4" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>

      {/* Expandable credential form */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="creds"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <form
              onSubmit={handleSaveKeys}
              className="border-t border-line-subtle px-4 pb-5 pt-4"
            >
              {/* Missing-keys hint */}
              {missingRequired.length > 0 && gw.enabled && !gw.ready && (
                <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-warning/40 bg-warning/8 p-3.5 text-xs text-warning">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <span>
                    Required before enabling:{' '}
                    <span className="font-semibold">{missingRequired.join(', ')}</span>
                  </span>
                </div>
              )}

              {/* Environment selector */}
              {gw.supports_environment && (
                <div className="mb-5">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
                    Environment
                  </p>
                  <SegmentedControl
                    value={effectiveEnv}
                    onChange={(v) => {
                      setDirtyEnv(v);
                      setSavedOk(false);
                    }}
                    options={[
                      { value: 'sandbox', label: 'Sandbox' },
                      { value: 'live', label: 'Live' },
                    ]}
                  />
                  {effectiveEnv === 'live' && (
                    <p className="mt-1.5 text-xs text-warning">
                      Live mode — real transactions will be processed.
                    </p>
                  )}
                </div>
              )}

              {/* Dynamic credential fields */}
              {(gw.fields || []).length > 0 ? (
                <>
                  <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
                    API credentials
                  </p>
                  <div className="grid gap-4 sm:grid-cols-2">
                    {gw.fields.map((field) => {
                      if (field.secret) {
                        const val = dirtyCredentials[field.key] ?? '';
                        return (
                          <SecretInput
                            key={field.key}
                            field={field}
                            value={val}
                            onChange={(v) => handleCredentialChange(field.key, v)}
                          />
                        );
                      }
                      return (
                        <Input
                          key={field.key}
                          label={field.label}
                          placeholder={field.placeholder || ''}
                          helper={field.help || undefined}
                          required={field.required}
                          value={
                            field.key in dirtyCredentials
                              ? dirtyCredentials[field.key]
                              : (field.value ?? '')
                          }
                          onChange={(e) =>
                            handleCredentialChange(field.key, e.target.value)
                          }
                        />
                      );
                    })}
                  </div>
                </>
              ) : (
                <p className="rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 text-xs text-ink-tertiary">
                  No credentials required for this gateway.
                </p>
              )}

              {/* Error */}
              {saveError && (
                <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-danger/35 bg-danger/8 p-3.5 text-xs text-danger">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <span>{saveError}</span>
                </div>
              )}

              {/* Save row */}
              <div className="mt-5 flex items-center justify-between gap-3 border-t border-line-subtle pt-4">
                <div className="text-xs">
                  {hasCredentialDirt ? (
                    <span className="inline-flex items-center gap-1.5 text-warning">
                      <span className="size-2 animate-pulseRing rounded-full bg-warning" />
                      Unsaved changes
                    </span>
                  ) : savedOk ? (
                    <motion.span
                      variants={fadeIn}
                      initial="hidden"
                      animate="show"
                      className="flex items-center gap-1.5 text-success"
                    >
                      <Check className="size-3.5" aria-hidden="true" />
                      Saved
                    </motion.span>
                  ) : null}
                </div>
                <Button
                  type="submit"
                  size="sm"
                  loading={saving}
                  disabled={!hasCredentialDirt && !saving}
                >
                  <Save className="size-4" aria-hidden="true" />
                  Save keys
                </Button>
              </div>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter tab bar
// ---------------------------------------------------------------------------
const FILTERS = [
  { id: 'all',         label: 'All' },
  { id: 'enabled',     label: 'Enabled' },
  { id: 'implemented', label: 'Configurable now' },
];

function FilterTabs({ value, onChange }) {
  return (
    <div
      role="tablist"
      className="flex gap-1 rounded-md border border-line-subtle bg-bg-sunken p-0.5"
    >
      {FILTERS.map((f) => (
        <button
          key={f.id}
          type="button"
          role="tab"
          aria-selected={value === f.id}
          onClick={() => onChange(f.id)}
          className={cn(
            'rounded-sm px-3 py-1.5 text-xs font-medium transition-colors focus-visible:focus-ring',
            value === f.id
              ? 'bg-accent/12 font-semibold text-accent'
              : 'text-ink-secondary hover:text-ink-primary',
          )}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function AdminPaymentMethodsPage() {
  const { data, isLoading, isError, refetch } = usePaymentMethods();

  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');

  const gateways = data?.items ?? [];

  // Sort by sort_order ascending
  const sorted = [...gateways].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));

  // Apply filter
  const filtered = sorted.filter((gw) => {
    if (filter === 'enabled') return gw.enabled;
    if (filter === 'implemented') return gw.implemented;
    return true;
  });

  // Apply search
  const query = search.trim().toLowerCase();
  const visible = query
    ? filtered.filter(
        (gw) =>
          gw.name.toLowerCase().includes(query) ||
          gw.code.toLowerCase().includes(query) ||
          gw.description?.toLowerCase().includes(query),
      )
    : filtered;

  const enabledCount = gateways.filter((g) => g.enabled).length;

  return (
    <AdminPage
      title="Payment Methods"
      description="Enable payment gateways and store their API credentials. Credentials are encrypted at rest and never exposed in full after saving."
    >
      {/* ── Toolbar ── */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <FilterTabs value={filter} onChange={setFilter} />
        <div className="relative w-full sm:w-72">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-tertiary"
            aria-hidden="true"
          />
          <input
            type="search"
            placeholder="Search gateways…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className={cn(
              'h-9 w-full rounded-lg bg-bg-sunken pl-9 pr-3.5 text-sm text-ink-primary',
              'border border-line-subtle placeholder:text-ink-tertiary',
              'transition-[border-color] duration-200 hover:border-line-strong',
              'focus-visible:border-accent focus-visible:outline-none',
            )}
          />
        </div>
      </div>

      {/* ── Summary strip ── */}
      {!isLoading && !isError && gateways.length > 0 && (
        <motion.div variants={fadeIn} initial="hidden" animate="show">
          <div className="flex items-center gap-3 rounded-lg border border-line-subtle bg-bg-elevated px-4 py-2.5">
            <span className="text-xs text-ink-tertiary">
              <span className="nums font-semibold text-ink-primary">{gateways.length}</span>{' '}
              gateway{gateways.length === 1 ? '' : 's'}
            </span>
            <span className="text-ink-tertiary">·</span>
            <Badge tone="success" dot>
              {enabledCount} enabled
            </Badge>
            {enabledCount === 0 && (
              <>
                <span className="text-ink-tertiary">·</span>
                <span className="text-xs text-warning">No active gateways — customers cannot pay online</span>
              </>
            )}
          </div>
        </motion.div>
      )}

      {/* ── Content ── */}
      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title="Couldn't load payment methods"
          description="Something went wrong fetching the gateway list."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-[72px] rounded-lg" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={CreditCard}
          title="No gateways match"
          description="Try a different search term or filter."
          size="sm"
          bordered={false}
        />
      ) : (
        <motion.div
          variants={listStagger(0.04)}
          initial="hidden"
          animate="show"
          className="flex flex-col gap-3"
        >
          {visible.map((gw) => (
            <motion.div key={gw.code} variants={fadeUp}>
              <GatewayRow gw={gw} />
            </motion.div>
          ))}
        </motion.div>
      )}
    </AdminPage>
  );
}
