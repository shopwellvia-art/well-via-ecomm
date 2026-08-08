import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  EyeOff,
  Globe,
  HelpCircle,
  MousePointerClick,
  Plug,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  Tags,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { Card, CardHeader, CardBody } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { cn } from '@/lib/utils.js';
import { fadeIn, scaleIn } from '@/lib/motion.js';
import {
  REDACTED,
  integrationsErrorMessage,
  testResultLabel,
  testResultTone,
  useAnalyticsIntegrations,
  useTestAnalyticsProvider,
  useTrackingHealth,
  useUpdateAnalyticsIntegrations,
  warningTone,
} from '@/features/analytics/integrations.js';
import {
  API_SECRET_KEY,
  DELIVERY_GUARD_ERROR_CODE,
  DELIVERY_GUARD_FIXES,
  DELIVERY_KEY,
  deliverySelectionError,
  storedStateUndeliverable,
} from '@/features/tracking/deliveryGuard.js';

/**
 * Analytics integrations + Tracking Health.
 *
 * Follows the AdminSettingsPage convention deliberately — grouped sub-sidebar,
 * per-group save bar, dirty dots, masked secrets — because an operator who has
 * configured SMTP here should not have to relearn anything to configure GTM.
 *
 * Two things it does that the generic settings page cannot:
 *
 * 1. **The schema comes from the server.** `FIELD_META` on the generic page is
 *    a client-side mirror of the seeded rows, and it drifts. Here the labels,
 *    types, options and validation hints all arrive with the values, so a field
 *    added in `services/analytics/integrations.py` renders without a frontend
 *    change and — more to the point — cannot render with a stale label. Only
 *    the icons are local, because an icon is not data.
 *
 * 2. **It refuses to draw a green tick it has not earned.** A connection test
 *    that comes back `cannot_verify_server_side` renders as a warning that says
 *    so in words. See `TestResultPanel`.
 */

// Icons and per-group framing. Everything else about a group — its label, its
// blurb, its fields — is served by the API.
const GROUP_META = {
  gtm: { icon: Tags, section: 'Tracking' },
  ga4: { icon: BarChart3, section: 'Tracking' },
  clarity: { icon: MousePointerClick, section: 'Tracking' },
  consent: { icon: ShieldCheck, section: 'Privacy' },
};

// Sidebar sections, top to bottom. Mirrors CATEGORY_GROUPS on the generic
// settings page; a group whose section is not listed still renders, under
// "Other", so a new server-side group can never become invisible.
const SECTION_ORDER = ['Tracking', 'Privacy', 'Other'];

const VISIBILITY_NOTE = {
  public: 'Sent to every browser — this value is public by nature.',
  admin: 'Admin only. Never sent to a browser.',
  secret: 'Encrypted at rest. Never returned and never sent to a browser.',
};

function truthy(value) {
  return value === true || value === 'true' || value === '1' || value === 'on';
}

// ─── FieldRow ─────────────────────────────────────────────────────────────────

function FieldRow({ field, value, onChange, error }) {
  const { key, label, type, description, placeholder, options } = field;

  if (type === 'bool') {
    return (
      <label
        className={cn(
          'flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 text-sm transition-all duration-150',
          truthy(value)
            ? 'border-accent/30 bg-accent/8'
            : 'border-line-subtle bg-bg-sunken hover:border-line-strong hover:bg-fill',
        )}
      >
        <input
          type="checkbox"
          checked={truthy(value)}
          onChange={(e) => onChange(key, e.target.checked ? 'true' : 'false')}
          className="mt-0.5 size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent"
        />
        <span className="flex-1 min-w-0">
          <span className="block text-sm font-medium text-ink-primary">{label}</span>
          {description && (
            <span className="mt-0.5 block text-xs text-ink-secondary">{description}</span>
          )}
          <span className="mt-0.5 block font-mono text-[10px] text-ink-tertiary">{key}</span>
        </span>
      </label>
    );
  }

  if (type === 'select') {
    return (
      <Select
        label={label}
        value={value ?? ''}
        onChange={(e) => onChange(key, e.target.value)}
        helper={description}
        error={error}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    );
  }

  if (type === 'textarea') {
    // A secret textarea (the service-account JSON) shows the mask like every
    // other secret. Typing over it is what sends a replacement.
    return (
      <div className="sm:col-span-2">
        <Textarea
          label={label}
          value={value ?? ''}
          onChange={(e) => onChange(key, e.target.value)}
          placeholder={placeholder}
          helper={description}
          rows={5}
        />
        {field.is_secret && value === REDACTED && <MaskedHint />}
      </div>
    );
  }

  return (
    <div>
      <Input
        label={label}
        type={type}
        value={value ?? ''}
        onChange={(e) => onChange(key, e.target.value)}
        placeholder={placeholder}
        helper={description}
        error={error}
        autoComplete={field.is_secret ? 'new-password' : undefined}
      />
      {field.is_secret && value === REDACTED && <MaskedHint />}
      {!field.is_secret && field.pattern_hint && (
        <p className="-mt-3 mb-3 text-[11px] text-ink-tertiary">
          Format: {field.pattern_hint}
        </p>
      )}
    </div>
  );
}

function MaskedHint() {
  return (
    <p className="-mt-3 mb-3 flex items-center gap-1.5 text-[11px] text-ink-tertiary">
      <EyeOff className="size-3" aria-hidden="true" />
      Value hidden. Type to replace it — leaving it untouched keeps the saved one.
    </p>
  );
}

// ─── Test connection ──────────────────────────────────────────────────────────

const TONE_STYLES = {
  success: 'border-success/30 bg-success/8 text-success',
  danger: 'border-danger/30 bg-danger/8 text-danger',
  warning: 'border-warning/30 bg-warning/8 text-warning',
  info: 'border-info/30 bg-info/8 text-info',
  neutral: 'border-line-subtle bg-bg-sunken text-ink-secondary',
};

const TONE_ICONS = {
  success: CheckCircle2,
  danger: AlertOctagon,
  warning: HelpCircle,
  info: Activity,
  neutral: Activity,
};

/**
 * The result of a connection test.
 *
 * The tick is gated on `result.verified`, never on the request having
 * succeeded. `cannot_verify_server_side` therefore renders as an explicit
 * "we could not check this" — a green tick that means "we did not look" is
 * worse than no tick at all, because it is the one that stops people looking.
 */
function TestResultPanel({ pending, error, result }) {
  if (pending) {
    return <p className="mt-3 text-xs text-ink-tertiary">Testing…</p>;
  }
  if (error) {
    return (
      <div className={cn('mt-3 rounded-lg border px-3.5 py-3 text-xs', TONE_STYLES.danger)}>
        <AlertTriangle className="mr-1.5 inline size-4 align-text-bottom" aria-hidden="true" />
        {integrationsErrorMessage(error, 'The test could not be run.')}
      </div>
    );
  }
  if (!result) return null;

  const tone = testResultTone(result);
  const Icon = TONE_ICONS[tone];
  const unverifiable = result.status === 'cannot_verify_server_side';

  return (
    <motion.div
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className={cn('mt-3 rounded-lg border px-3.5 py-3 text-xs', TONE_STYLES[tone])}
    >
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">
            {unverifiable ? 'Not verifiable from the server' : testResultLabel(result)}
          </p>
          <p className="mt-1 leading-relaxed opacity-90">{result.message}</p>

          <dl className="mt-3 space-y-2 border-t border-current/15 pt-2.5 opacity-80">
            <div>
              <dt className="font-medium">What was checked</dt>
              <dd className="mt-0.5 leading-relaxed">{result.checked}</dd>
            </div>
            {result.not_checked?.length > 0 && (
              <div>
                <dt className="font-medium">What this does not prove</dt>
                <dd className="mt-0.5">
                  <ul className="space-y-1">
                    {result.not_checked.map((item) => (
                      <li key={item} className="flex items-start gap-1.5 leading-relaxed">
                        <span className="mt-1.5 size-1 shrink-0 rounded-full bg-current" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </motion.div>
  );
}

function TestConnectionCard({ group, onTest, pending, error, result }) {
  return (
    <div className="mt-6 rounded-sm border border-line-subtle bg-bg-sunken p-5">
      <CardHeader
        title={`Test ${group.label}`}
        className="mb-4 border-none px-0 py-0"
        action={<Badge tone="info" size="sm">Test</Badge>}
      />
      <p className="mb-4 text-xs text-ink-secondary">
        {group.provider === 'ga4'
          ? 'Validates the saved measurement ID and API secret against Google’s Measurement Protocol debug endpoint. It validates and reports — no event is written to your property. Save changes first if you just edited them.'
          : 'There is no server-side way to confirm this integration. Running the test reports exactly what can and cannot be established from here.'}
      </p>
      <Button variant="outline" onClick={onTest} loading={pending}>
        <Plug className="size-4" aria-hidden="true" />
        Test connection
      </Button>
      <TestResultPanel pending={pending} error={error} result={result} />
    </div>
  );
}

// ─── Save bar ─────────────────────────────────────────────────────────────────

function SaveBar({ dirty, saving, saved, error, onSave }) {
  return (
    <div className="mt-6 border-t border-line-subtle pt-5">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs">
          {dirty ? (
            <span className="inline-flex items-center gap-1.5 text-warning">
              <span className="size-2 animate-pulseRing rounded-full bg-warning" />
              Unsaved changes
            </span>
          ) : saved ? (
            <motion.span
              variants={fadeIn}
              initial="hidden"
              animate="show"
              className="inline-flex items-center gap-1.5 text-success"
            >
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
              Saved
            </motion.span>
          ) : (
            <span className="text-ink-tertiary">All changes saved</span>
          )}
        </div>
        <Button onClick={onSave} loading={saving} disabled={!dirty}>
          <Save className="size-4" aria-hidden="true" />
          Save changes
        </Button>
      </div>
      {error && (
        <div className={cn('mt-3 rounded-lg border px-3.5 py-3 text-xs', TONE_STYLES.danger)}>
          <AlertTriangle className="mr-1.5 inline size-4 align-text-bottom" aria-hidden="true" />
          {integrationsErrorMessage(error, 'Save failed.')}
        </div>
      )}
    </div>
  );
}

// ─── Delivery guard banner ────────────────────────────────────────────────────

/**
 * Shown while the STORED state is the undeliverable one: purchase delivery in
 * a server mode with GA4 enabled and no Measurement Protocol API secret saved.
 *
 * The backend now refuses to create this state (422, same code as the
 * tracking-health warning), so the banner exists for deployments configured
 * before the guard did — including production right now. It partially
 * duplicates the health panel's warning on purpose: health needs
 * `analytics.control_centre.view` and this page only needs manage, so the
 * operator who can actually fix the state must see it without the extra
 * permission.
 */
function DeliveryGuardBanner() {
  return (
    <div
      role="alert"
      className={cn('rounded-lg border px-4 py-3.5 text-xs leading-relaxed', TONE_STYLES.danger)}
    >
      <div className="flex items-start gap-2">
        <AlertOctagon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">
            Purchases are not reaching GA4 — server-side delivery has no API secret
          </p>
          <p className="mt-1 opacity-90">
            Purchase delivery is set to a server mode, but no Measurement Protocol API
            secret is saved. Every purchase is queued in the server outbox and cannot be
            delivered, so GA4 reports zero ecommerce revenue while the queue grows
            silently (tracking-health warning{' '}
            <code className="font-mono">{DELIVERY_GUARD_ERROR_CODE}</code>). Two ways to
            fix it, in the GA4 group below:
          </p>
          <ul className="mt-2 space-y-1">
            {DELIVERY_GUARD_FIXES.map((fix) => (
              <li key={fix} className="flex items-start gap-1.5">
                <span className="mt-1.5 size-1 shrink-0 rounded-full bg-current" />
                <span>{fix}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

// ─── Tracking Health ──────────────────────────────────────────────────────────

function StatusDot({ tone }) {
  return (
    <span
      className={cn(
        'size-2 shrink-0 rounded-full',
        tone === 'success' && 'bg-success',
        tone === 'warning' && 'bg-warning',
        tone === 'danger' && 'bg-danger',
        tone === 'neutral' && 'bg-ink-tertiary',
      )}
    />
  );
}

function providerTone(state) {
  if (!state.enabled) return 'neutral';
  if (!state.configured || !state.id_format_ok) return 'danger';
  return 'success';
}

function providerSummary(state) {
  if (!state.enabled) return 'Off — no tag is served';
  if (!state.configured) return 'On, but no ID saved — collecting nothing';
  if (!state.id_format_ok) return `Malformed ID (${state.id}) — the tag will not load`;
  return state.id;
}

function Stat({ label, value, tone = 'neutral', hint }) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-sunken px-3.5 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
        {label}
      </p>
      <p
        className={cn(
          'mt-1 text-lg font-semibold tabular-nums',
          tone === 'danger' && 'text-danger',
          tone === 'warning' && 'text-warning',
          tone === 'success' && 'text-success',
          tone === 'neutral' && 'text-ink-primary',
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[11px] leading-snug text-ink-tertiary">{hint}</p>}
    </div>
  );
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

/**
 * The Tracking Health view.
 *
 * Every panel here exists because its absence is indistinguishable from health:
 * a disabled tag, a staging measurement ID, a stalled outbox and a frozen
 * watermark all look exactly like a quiet trading day.
 */
function TrackingHealthPanel({ query }) {
  const { data, isLoading, error, refetch, isFetching } = query;

  if (isLoading) {
    return (
      <Card>
        <CardHeader title="Tracking Health" />
        <CardBody className="p-6">
          <div className="grid gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-20 rounded-sm" />
            ))}
          </div>
        </CardBody>
      </Card>
    );
  }

  if (error) {
    const forbidden = error?.response?.status === 403;
    return (
      <Card>
        <CardHeader title="Tracking Health" />
        <CardBody className="p-6">
          <p className="text-sm text-ink-secondary">
            {forbidden
              ? 'You do not have the analytics.control_centre.view permission, so tracking health is hidden. Integration settings above are unaffected.'
              : integrationsErrorMessage(error, 'Tracking health could not be loaded.')}
          </p>
        </CardBody>
      </Card>
    );
  }

  if (!data) return null;

  const { environment, providers, consent, outbox, rollups, warnings } = data;
  const counts = outbox.counts || {};

  return (
    <Card>
      <CardHeader
        title="Tracking Health"
        action={
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-ink-tertiary">
              {fmtTime(data.generated_at)}
            </span>
            <Button
              variant="ghost"
              size="sm"
              iconOnly
              onClick={() => refetch()}
              loading={isFetching}
              aria-label="Refresh tracking health"
            >
              <RefreshCw className="size-4" aria-hidden="true" />
            </Button>
          </div>
        }
      />
      <CardBody className="space-y-5 p-6">
        {/* Warnings first — these are the conditions where every other signal
            on this page still looks green. */}
        {warnings?.length > 0 && (
          <ul className="space-y-2">
            {warnings.map((w) => (
              <li
                key={w.code}
                className={cn(
                  'flex items-start gap-2 rounded-lg border px-3.5 py-3 text-xs leading-relaxed',
                  TONE_STYLES[warningTone(w.severity)],
                )}
              >
                <AlertOctagon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>{w.message}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Environment */}
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            Environment
          </p>
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
            <Globe className="size-4 text-ink-tertiary" aria-hidden="true" />
            <span>
              Running as <strong className="text-ink-primary">{environment.app_environment}</strong>
              {environment.frontend_host ? ` (${environment.frontend_host})` : ''}
            </span>
            <span className="text-ink-tertiary">·</span>
            <span>
              IDs declared as{' '}
              <strong className="text-ink-primary">
                {environment.declared_property_environment || 'not declared'}
              </strong>
            </span>
            <Badge tone={environment.mismatch ? 'danger' : 'success'} size="sm">
              {environment.mismatch ? 'Mismatch' : 'Consistent'}
            </Badge>
          </div>
        </div>

        {/* Providers */}
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            Providers
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            {['gtm', 'ga4', 'clarity'].map((name) => {
              const state = providers[name];
              if (!state) return null;
              const meta = GROUP_META[name];
              const Icon = meta?.icon || Activity;
              return (
                <div
                  key={name}
                  className="flex items-start gap-2.5 rounded-sm border border-line-subtle bg-bg-sunken px-3.5 py-3"
                >
                  <Icon className="mt-0.5 size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 text-sm font-medium text-ink-primary">
                      <StatusDot tone={providerTone(state)} />
                      {name.toUpperCase()}
                    </p>
                    <p className="mt-0.5 break-all font-mono text-[11px] text-ink-secondary">
                      {providerSummary(state)}
                    </p>
                    {name === 'ga4' && (
                      <p className="mt-1 text-[11px] text-ink-tertiary">
                        Purchases from {state.purchase_delivery || '—'} · API secret{' '}
                        {state.api_secret_state}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Consent */}
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            Consent
          </p>
          <div className="flex flex-wrap gap-2 text-xs text-ink-secondary">
            <Badge tone="neutral" size="sm">Mode: {consent.mode || '—'}</Badge>
            <Badge tone="neutral" size="sm">
              analytics_storage: {consent.default_analytics_storage || '—'}
            </Badge>
            <Badge tone="neutral" size="sm">
              ad_storage: {consent.default_ad_storage || '—'}
            </Badge>
            <Badge tone="neutral" size="sm">
              wait_for_update: {consent.wait_for_update_ms || '—'}ms
            </Badge>
          </div>
        </div>

        {/* Server-side delivery outbox */}
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            <Server className="size-3.5" aria-hidden="true" />
            Server-side purchase delivery
          </p>
          <div className="grid gap-2 sm:grid-cols-4">
            <Stat label="Delivered" value={counts.delivered ?? 0} tone="success" />
            <Stat
              label="Pending"
              value={counts.pending ?? 0}
              tone={counts.pending ? 'warning' : 'neutral'}
            />
            <Stat
              label="Failed"
              value={counts.failed ?? 0}
              tone={counts.failed ? 'danger' : 'neutral'}
              hint={counts.failed ? 'Attribution permanently lost unless replayed' : undefined}
            />
            <Stat
              label="Suppressed"
              value={counts.suppressed_no_consent ?? 0}
              hint="Recorded internally, deliberately not sent — no consent"
            />
          </div>
          <dl className="mt-3 grid gap-x-6 gap-y-1 text-[11px] text-ink-secondary sm:grid-cols-2">
            <div className="flex justify-between gap-3">
              <dt className="text-ink-tertiary">Last successful delivery</dt>
              <dd className="tabular-nums">{fmtTime(outbox.last_delivered_at)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-tertiary">Oldest pending event</dt>
              <dd className="tabular-nums">{fmtTime(outbox.oldest_pending_occurred_at)}</dd>
            </div>
          </dl>
          {outbox.last_error && (
            <p className="mt-2 rounded-lg border border-danger/30 bg-danger/8 px-3.5 py-2.5 text-[11px] text-danger">
              Last delivery error ({fmtTime(outbox.last_error_at)}): {outbox.last_error}
            </p>
          )}
        </div>

        {/* Rollup watermarks */}
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            Rollup watermarks
          </p>
          <p className="mb-2 text-[11px] leading-relaxed text-ink-tertiary">
            The last reporting day each job has fully computed — “the numbers
            include everything through here”, which is a different claim from
            “the job ran at 04:00”.
          </p>
          {rollups.jobs?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-left text-xs">
                <thead>
                  <tr className="text-ink-tertiary">
                    <th className="py-1.5 pr-4 font-medium">Job</th>
                    <th className="py-1.5 pr-4 font-medium">Watermark</th>
                    <th className="py-1.5 font-medium">Last success</th>
                  </tr>
                </thead>
                <tbody className="text-ink-secondary">
                  {rollups.jobs.map((job) => (
                    <tr key={job.job} className="border-t border-line-subtle">
                      <td className="py-1.5 pr-4 font-mono">{job.job}</td>
                      <td className="py-1.5 pr-4 tabular-nums">
                        {job.watermark_date || '—'}
                      </td>
                      <td className="py-1.5 tabular-nums">{fmtTime(job.last_success_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-ink-tertiary">
              No pipeline runs recorded yet — nothing has computed a watermark.
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AdminAnalyticsSettingsPage() {
  const { data, isLoading, error } = useAnalyticsIntegrations();
  const update = useUpdateAnalyticsIntegrations();
  const test = useTestAnalyticsProvider();
  const health = useTrackingHealth();

  const [activeGroup, setActiveGroup] = useState('gtm');
  const [draft, setDraft] = useState({});
  const [savedGroup, setSavedGroup] = useState(null);
  // Keyed per provider so three tests do not overwrite each other's banner.
  const [testResults, setTestResults] = useState({});
  const [testErrors, setTestErrors] = useState({});
  const [testingProvider, setTestingProvider] = useState(null);

  const groups = useMemo(() => data?.groups || [], [data]);

  // The deliverability guard's inputs. `has_value` is the API's "a secret is
  // stored" flag — the secret's value never reaches the browser and is never
  // needed to answer "is one saved".
  const fieldByKey = useMemo(() => {
    const map = {};
    for (const g of groups) for (const f of g.fields) map[f.key] = f;
    return map;
  }, [groups]);

  // Seed the draft from the server exactly once per key. Re-seeding on every
  // response would discard whatever the admin is typing when the health poll
  // happens to land — which is why the settings query does not poll at all.
  useEffect(() => {
    if (!data) return;
    setDraft((cur) => {
      const next = { ...cur };
      for (const g of data.groups) {
        for (const f of g.fields) {
          if (next[f.key] === undefined) next[f.key] = f.value ?? '';
        }
      }
      return next;
    });
  }, [data]);

  function setField(key, value) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  /**
   * The payload for one group.
   *
   * The secret rule lives here and nowhere else: a secret whose draft value is
   * still the mask is *omitted entirely*. Sending `***` back would either
   * overwrite the credential with three asterisks or rely on the server to
   * recognise its own sentinel — and only one of those two systems should have
   * to be right for a credential to survive a save.
   */
  function buildUpdates(groupId) {
    const group = groups.find((g) => g.id === groupId);
    const updates = {};
    for (const f of group?.fields || []) {
      const current = draft[f.key];
      if (current === undefined) continue;
      if (f.is_secret && current === REDACTED) continue;
      if (current !== (f.value ?? '')) updates[f.key] = current;
    }
    return updates;
  }

  function isGroupDirty(groupId) {
    return Object.keys(buildUpdates(groupId)).length > 0;
  }

  async function saveGroup(groupId) {
    const updates = buildUpdates(groupId);
    if (Object.keys(updates).length === 0) return;
    try {
      const fresh = await update.mutateAsync(updates);
      // Re-seed only the keys that were part of this save, so a secret comes
      // back to its mask and everything else stays as typed.
      setDraft((d) => {
        const next = { ...d };
        for (const g of fresh.integrations.groups) {
          for (const f of g.fields) {
            if (Object.prototype.hasOwnProperty.call(updates, f.key)) {
              next[f.key] = f.value ?? '';
            }
          }
        }
        return next;
      });
      setSavedGroup(groupId);
      setTimeout(() => setSavedGroup((g) => (g === groupId ? null : g)), 2000);
    } catch {
      // The mutation carries the error; SaveBar renders it.
    }
  }

  async function runTest(provider) {
    setTestingProvider(provider);
    setTestErrors((e) => ({ ...e, [provider]: null }));
    try {
      const result = await test.mutateAsync(provider);
      setTestResults((r) => ({ ...r, [provider]: result }));
    } catch (err) {
      setTestResults((r) => ({ ...r, [provider]: null }));
      setTestErrors((e) => ({ ...e, [provider]: err }));
    } finally {
      setTestingProvider(null);
    }
  }

  // Inline refusal on the delivery selector, mirroring the backend guard: a
  // server mode with no secret on file — and none arriving with this save —
  // is an inline error before it becomes a 422.
  const deliveryError = deliverySelectionError({
    deliveryDraft: draft[DELIVERY_KEY] ?? fieldByKey[DELIVERY_KEY]?.value,
    hasStoredSecret: Boolean(fieldByKey[API_SECRET_KEY]?.has_value),
    secretDraft: draft[API_SECRET_KEY],
  });

  // Banner condition — the STORED state, not the draft: it reports what the
  // deployment is doing right now, not what the operator is about to try.
  const storedUndeliverable = storedStateUndeliverable({
    enabled: fieldByKey['analytics.ga4_enabled']?.value,
    delivery: fieldByKey[DELIVERY_KEY]?.value,
    hasStoredSecret: Boolean(fieldByKey[API_SECRET_KEY]?.has_value),
  });

  if (isLoading) {
    return (
      <AdminPage title="Analytics integrations" description="Loading…">
        <Skeleton className="h-48 w-full rounded-sm" />
        <div className="grid gap-6 lg:grid-cols-[248px_1fr]">
          <div className="hidden flex-col gap-2 rounded-sm border border-line-subtle bg-bg-elevated p-3 lg:flex">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full rounded-sm" />
            ))}
          </div>
          <Skeleton className="h-96 w-full rounded-sm" />
        </div>
      </AdminPage>
    );
  }

  if (error) {
    return (
      <AdminPage title="Analytics integrations">
        <Card>
          <CardBody className="p-6">
            <p className="text-sm text-danger">
              {integrationsErrorMessage(error, 'Integration settings could not be loaded.')}
            </p>
          </CardBody>
        </Card>
      </AdminPage>
    );
  }

  // Build the sidebar sections. A group whose section is unknown lands under
  // "Other" rather than disappearing — a server-side group the frontend has
  // never heard of must still be editable.
  const sections = SECTION_ORDER.map((label) => ({
    label,
    groups: groups.filter((g) => (GROUP_META[g.id]?.section || 'Other') === label),
  })).filter((s) => s.groups.length > 0);

  const allIds = sections.flatMap((s) => s.groups.map((g) => g.id));
  const currentId = allIds.includes(activeGroup) ? activeGroup : allIds[0];
  const currentGroup = groups.find((g) => g.id === currentId);
  const CurrentIcon = GROUP_META[currentId]?.icon || Activity;

  const boolFields = (currentGroup?.fields || []).filter((f) => f.type === 'bool');
  const inputFields = (currentGroup?.fields || []).filter((f) => f.type !== 'bool');

  return (
    <AdminPage
      title="Analytics integrations"
      description="Tag IDs, credentials and consent defaults — plus whether any of it is actually working. Credentials are encrypted at rest and never returned."
    >
      <TrackingHealthPanel query={health} />

      {storedUndeliverable && <DeliveryGuardBanner />}

      <div className="grid gap-6 lg:grid-cols-[248px_1fr]">
        {/* ── Sub-sidebar ── */}
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <div className="lg:hidden">
            <Select
              label="Integration"
              value={currentId}
              onChange={(e) => setActiveGroup(e.target.value)}
            >
              {sections.map((s) => (
                <optgroup key={s.label} label={s.label}>
                  {s.groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.label + (isGroupDirty(g.id) ? ' •' : '')}
                    </option>
                  ))}
                </optgroup>
              ))}
            </Select>
          </div>

          <Card flat className="hidden p-2 lg:block">
            <nav className="flex flex-col gap-4">
              {sections.map((s) => (
                <div key={s.label}>
                  <p className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
                    {s.label}
                  </p>
                  <div className="flex flex-col gap-0.5">
                    {s.groups.map((g) => {
                      const Icon = GROUP_META[g.id]?.icon || Activity;
                      const active = currentId === g.id;
                      const dirty = isGroupDirty(g.id);
                      const state = health.data?.providers?.[g.id];
                      return (
                        <button
                          key={g.id}
                          type="button"
                          onClick={() => setActiveGroup(g.id)}
                          aria-current={active ? 'page' : undefined}
                          className={cn(
                            'flex items-center gap-2.5 rounded-sm px-3 py-2 text-sm transition-colors focus-visible:focus-ring',
                            active
                              ? 'bg-accent/12 font-medium text-accent'
                              : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                          )}
                        >
                          <Icon className="size-4 shrink-0" aria-hidden="true" />
                          <span className="flex-1 text-left">{g.label}</span>
                          {dirty && (
                            <span
                              className="size-1.5 shrink-0 rounded-full bg-warning"
                              aria-label="unsaved changes"
                            />
                          )}
                          {!dirty && state && <StatusDot tone={providerTone(state)} />}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
          </Card>
        </aside>

        {/* ── Content ── */}
        <Card>
          <CardHeader>
            <div className="flex min-w-0 items-center gap-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-accent/12 text-accent">
                <CurrentIcon className="size-5" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-ink-primary">
                  {currentGroup?.label}
                </p>
                {currentGroup?.blurb && (
                  <p className="truncate text-xs text-ink-tertiary">{currentGroup.blurb}</p>
                )}
              </div>
            </div>
          </CardHeader>
          <CardBody className="p-6">
            <motion.div key={currentId} variants={fadeIn} initial="hidden" animate="show">
              {inputFields.length > 0 && (
                <div className="mb-5 grid gap-4 sm:grid-cols-2">
                  {inputFields.map((f) => (
                    <FieldRow
                      key={f.key}
                      field={f}
                      value={draft[f.key]}
                      onChange={setField}
                      // The delivery selector carries the guard's inline
                      // refusal — the same condition the backend 422s on.
                      error={f.key === DELIVERY_KEY ? deliveryError : undefined}
                    />
                  ))}
                </div>
              )}

              {boolFields.length > 0 && (
                <>
                  {inputFields.length > 0 && (
                    <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
                      Toggles
                    </p>
                  )}
                  <div className="mb-5 grid gap-2 sm:grid-cols-2">
                    {boolFields.map((f) => (
                      <FieldRow
                        key={f.key}
                        field={f}
                        value={draft[f.key]}
                        onChange={setField}
                      />
                    ))}
                  </div>
                </>
              )}

              {/* Where each value in this group is allowed to travel. Operators
                  routinely assume every "API" field is a secret; saying so per
                  group is what stops a public ID being treated as one — and,
                  more usefully, the reverse. */}
              <ul className="mb-1 space-y-1 rounded-sm border border-line-subtle bg-bg-sunken px-4 py-3 text-[11px] text-ink-tertiary">
                {[...new Set((currentGroup?.fields || []).map((f) => f.visibility))].map(
                  (visibility) => (
                    <li key={visibility} className="flex items-start gap-1.5">
                      <span className="mt-1.5 size-1 shrink-0 rounded-full bg-ink-tertiary" />
                      <span>
                        <strong className="text-ink-secondary">{visibility}</strong>{' '}
                        — {VISIBILITY_NOTE[visibility] || ''}
                      </span>
                    </li>
                  ),
                )}
              </ul>

              <SaveBar
                dirty={isGroupDirty(currentId)}
                saving={update.isPending}
                saved={savedGroup === currentId}
                error={update.isError ? update.error : null}
                onSave={() => saveGroup(currentId)}
              />

              {currentGroup?.provider && (
                <TestConnectionCard
                  group={currentGroup}
                  onTest={() => runTest(currentGroup.provider)}
                  pending={testingProvider === currentGroup.provider}
                  error={testErrors[currentGroup.provider]}
                  result={testResults[currentGroup.provider]}
                />
              )}
            </motion.div>
          </CardBody>
        </Card>
      </div>
    </AdminPage>
  );
}
