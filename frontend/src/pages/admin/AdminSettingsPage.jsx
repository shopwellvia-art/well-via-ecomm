import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Mail,
  MessageSquare,
  ShieldCheck,
  Settings as SettingsIcon,
  Send,
  EyeOff,
  Save,
  CheckCircle2,
  AlertTriangle,
  Bell,
  Truck,
  Wallet,
  CreditCard,
  LogIn,
  PiggyBank,
  AlertOctagon,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { Card, CardHeader, CardBody } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { cn } from '@/lib/utils.js';
import { fadeIn, scaleIn } from '@/lib/motion.js';
import {
  useSettings,
  useUpdateSettings,
  useTestEmail,
  useTestSms,
} from '@/features/settings/hooks.js';

const REDACTED = '***';

const _ICON_OPTIONS = [
  { value: 'star',    label: 'Star' },
  { value: 'shield',  label: 'Shield' },
  { value: 'truck',   label: 'Truck' },
  { value: 'clock',   label: 'Clock' },
  { value: 'package', label: 'Package' },
  { value: 'badge',   label: 'Badge' },
];

function _loginBadgeMeta(n) {
  return {
    [`login.trust_badge_${n}_label`]: {
      label: `Trust badge ${n} — text`,
      type: 'text',
      placeholder: n === 1 ? '5★ Rating from our customers' : '',
    },
    [`login.trust_badge_${n}_icon`]: {
      label: `Trust badge ${n} — icon`,
      type: 'select',
      options: _ICON_OPTIONS,
    },
  };
}

const FIELD_META = {
  'email.backend':  { label: 'Backend', type: 'select', options: [
    { value: 'console', label: 'Console (dev only — logs the email)' },
    { value: 'smtp',    label: 'SMTP (real delivery)' },
  ]},
  'smtp.host':      { label: 'SMTP host', type: 'text', placeholder: 'smtp.sendgrid.net' },
  'smtp.port':      { label: 'Port', type: 'number', placeholder: '587' },
  'smtp.user':      { label: 'Username', type: 'text', placeholder: 'apikey' },
  'smtp.password':  { label: 'Password / API key', type: 'password' },
  'smtp.use_tls':   { label: 'Use STARTTLS', type: 'bool' },
  'email.from':     { label: 'From address', type: 'text', placeholder: 'orders@yourdomain.com' },

  'sms.backend':         { label: 'Backend', type: 'select', options: [
    { value: 'console', label: 'Console (dev only — logs the SMS)' },
    { value: 'twilio',  label: 'Twilio' },
  ]},
  'twilio.account_sid':  { label: 'Account SID', type: 'text', placeholder: 'ACxxxxxxxx...' },
  'twilio.auth_token':   { label: 'Auth Token', type: 'password' },
  'twilio.from_number':  { label: 'From phone number', type: 'text', placeholder: '+14155551234' },

  'auth.totp_mode': { label: 'Two-factor authentication', type: 'select', options: [
    { value: 'off',      label: 'Off — disabled for everyone' },
    { value: 'optional', label: 'Optional — users may enable on their account' },
  ]},

  'notifications.order_paid':      { label: 'Order paid',      type: 'bool' },
  'notifications.order_shipped':   { label: 'Order shipped',   type: 'bool' },
  'notifications.order_delivered': { label: 'Order delivered', type: 'bool' },
  'notifications.order_cancelled': { label: 'Order cancelled', type: 'bool' },
  'notifications.order_refunded':  { label: 'Order refunded',  type: 'bool' },

  'shipping.provider': { label: 'Provider', type: 'select', options: [
    { value: 'none',      label: 'None — disabled (manual fulfillment)' },
    { value: 'mock',      label: 'Mock — in-process simulator (dev only)' },
    { value: 'delhivery', label: 'Delhivery' },
  ]},
  'shipping.environment': { label: 'Environment', type: 'select', options: [
    { value: 'staging',    label: 'Staging — staging-express.delhivery.com' },
    { value: 'production', label: 'Production — track.delhivery.com' },
  ]},
  'shipping.delhivery.api_token':   { label: 'Delhivery API token', type: 'password',
    placeholder: 'Token from the Delhivery merchant portal' },
  'shipping.delhivery.client_name': { label: 'Delhivery client name', type: 'text',
    placeholder: 'Provided by Delhivery on onboarding' },
  'shipping.warehouse.name':        { label: 'Pickup warehouse name', type: 'text',
    placeholder: 'Must match the warehouse registered with Delhivery' },
  'shipping.warehouse.pincode':     { label: 'Pickup warehouse pincode', type: 'text',
    placeholder: '560001' },
  'shipping.warehouse.address':     { label: 'Pickup warehouse address', type: 'textarea',
    placeholder: 'Street, area, city — printed on shipping labels' },
  'shipping.auto_create_on_paid':   { label: 'Auto-push to carrier on PAID', type: 'bool' },
  'shipping.serviceability_cache_minutes': { label: 'Serviceability cache (minutes)', type: 'number',
    placeholder: '60' },
  'shipping.webhook_secret':        { label: 'Tracking webhook shared secret', type: 'password',
    placeholder: 'Expected on inbound ?token=… query string' },
  'shipping.free_threshold':        { label: 'Free shipping when cart subtotal ≥ (₹)', type: 'number',
    placeholder: '999' },

  ..._loginBadgeMeta(1),
  ..._loginBadgeMeta(2),
  ..._loginBadgeMeta(3),
  ..._loginBadgeMeta(4),

  'cod.enabled':                  { label: 'Allow Cash on Delivery', type: 'bool' },
  'cod.flat_surcharge':           { label: 'COD flat surcharge (₹)', type: 'number',
    placeholder: '40' },
  'cod.min_order_total':          { label: 'Minimum order subtotal for COD (₹)', type: 'number',
    placeholder: '199' },
  'cod.max_order_total':          { label: 'Maximum order subtotal for COD (₹)', type: 'number',
    placeholder: '5000' },
  'cod.block_first_time_customer':{ label: 'Block COD for first-time customers', type: 'bool' },
  'cod.block_rto_customers':      { label: 'Block COD for customers with prior refunded returns', type: 'bool' },
  'cod.split_enabled':            { label: 'Offer Split COD (partial prepaid)', type: 'bool' },
  'cod.split_prepaid_amount':     { label: 'Split COD upfront amount (₹)', type: 'number',
    placeholder: '100' },
  'cod.require_otp':              { label: 'Require SMS OTP for COD orders', type: 'bool' },

  'costs.packing_per_order':  { label: 'Packing cost per order (₹)', type: 'number', placeholder: '20' },
  'costs.handling_per_order': { label: 'Handling cost per order (₹)', type: 'number', placeholder: '10' },
  'costs.gateway_fee_pct':    { label: 'Payment gateway fee (%)', type: 'number', placeholder: '2' },
  'costs.monthly_overheads':  { label: 'Monthly overheads (₹)', type: 'number', placeholder: '20000' },
  'costs.monthly_ad_spend':   { label: 'Monthly ad spend (₹)', type: 'number', placeholder: '8000' },

  'payments.suggested_instrument': { label: 'Suggested instrument', type: 'select', options: [
    { value: 'upi',         label: 'UPI' },
    { value: 'netbanking',  label: 'Netbanking' },
    { value: 'card',        label: 'Card' },
    { value: 'wallet',      label: 'Wallet' },
  ]},
  'payments.instruments.upi.enabled':           { label: 'UPI — enabled', type: 'bool' },
  'payments.instruments.upi.discount_percent':  { label: 'UPI — discount %', type: 'number',
    placeholder: '5' },
  'payments.instruments.netbanking.enabled':    { label: 'Netbanking — enabled', type: 'bool' },
  'payments.instruments.netbanking.discount_percent': { label: 'Netbanking — discount %', type: 'number',
    placeholder: '0' },
  'payments.instruments.card.enabled':          { label: 'Card — enabled', type: 'bool' },
  'payments.instruments.card.discount_percent': { label: 'Card — discount %', type: 'number',
    placeholder: '0' },
  'payments.instruments.wallet.enabled':        { label: 'Wallet — enabled', type: 'bool' },
  'payments.instruments.wallet.discount_percent': { label: 'Wallet — discount %', type: 'number',
    placeholder: '0' },
};

// Tabs that carry a danger-zone extra section (visual cue)
const DANGER_ZONE_CATEGORIES = new Set(['security', 'cod']);

const CATEGORY_ORDER = ['costs', 'payments', 'shipping', 'cod', 'login', 'email', 'sms', 'notifications', 'security', 'general'];
const TAB_META = {
  costs:         { label: 'Costs',         icon: PiggyBank  },
  payments:      { label: 'Payments',      icon: CreditCard },
  shipping:      { label: 'Shipping',      icon: Truck      },
  cod:           { label: 'Cash on Delivery', icon: Wallet  },
  login:         { label: 'Login Page',    icon: LogIn      },
  email:         { label: 'Email & SMTP',  icon: Mail       },
  sms:           { label: 'SMS',           icon: MessageSquare },
  notifications: { label: 'Notifications', icon: Bell       },
  security:      { label: 'Security',      icon: ShieldCheck },
  general:       { label: 'General',       icon: SettingsIcon },
};

function fieldType(key) {
  return FIELD_META[key]?.type || 'text';
}

// ─── FieldRow ─────────────────────────────────────────────────────────────────

function FieldRow({ item, draft, onChange }) {
  const meta = FIELD_META[item.key];
  const label = meta?.label || item.key;
  const type = fieldType(item.key);
  const value = draft[item.key];
  const placeholder = meta?.placeholder;

  if (type === 'bool') {
    return (
      <label
        className={cn(
          'flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 text-sm transition-all duration-150',
          (value === 'true' || value === '1' || value === 'on' || value === true)
            ? 'border-accent/30 bg-accent/8'
            : 'border-line-subtle bg-bg-sunken hover:border-line-strong hover:bg-fill',
        )}
      >
        <input
          type="checkbox"
          checked={value === 'true' || value === '1' || value === 'on' || value === true}
          onChange={(e) => onChange(item.key, e.target.checked ? 'true' : 'false')}
          className="mt-0.5 size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent"
        />
        <span className="flex-1 min-w-0">
          <span className="block text-sm font-medium text-ink-primary">{label}</span>
          {item.description && (
            <span className="mt-0.5 block text-xs text-ink-secondary">{item.description}</span>
          )}
          <span className="mt-0.5 block font-mono text-[10px] text-ink-tertiary">{item.key}</span>
        </span>
      </label>
    );
  }

  if (type === 'select') {
    return (
      <Select
        label={label}
        value={value || ''}
        onChange={(e) => onChange(item.key, e.target.value)}
        helper={item.description}
      >
        {meta.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    );
  }

  if (type === 'textarea') {
    return (
      <label className="block">
        <span className="mb-1.5 block text-sm font-medium text-ink-primary">{label}</span>
        <textarea
          value={value ?? ''}
          onChange={(e) => onChange(item.key, e.target.value)}
          placeholder={meta?.placeholder}
          rows={3}
          className="block w-full rounded-lg border border-line-subtle bg-bg-elevated px-3 py-2.5 text-sm text-ink-primary placeholder:text-ink-tertiary transition-colors hover:border-line-strong"
        />
        {item.description && (
          <span className="mt-1 block text-xs text-ink-tertiary">{item.description}</span>
        )}
      </label>
    );
  }

  // text / number / password
  const isSecret = item.is_secret;
  return (
    <div>
      <Input
        label={label}
        type={type}
        value={value ?? ''}
        onChange={(e) => onChange(item.key, e.target.value)}
        placeholder={placeholder}
        helper={item.description}
        autoComplete={isSecret ? 'new-password' : undefined}
      />
      {isSecret && value === REDACTED && (
        <p className="-mt-3 mb-3 flex items-center gap-1.5 text-[11px] text-ink-tertiary">
          <EyeOff className="size-3" aria-hidden="true" />
          Value hidden. Type to replace it.
        </p>
      )}
    </div>
  );
}

// ─── TestSendBanner ───────────────────────────────────────────────────────────

function TestSendBanner({ pending, result, kind }) {
  if (pending) {
    return (
      <p className="mt-3 text-xs text-ink-tertiary">Sending test {kind}…</p>
    );
  }
  if (!result) return null;
  return (
    <motion.div
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className={cn(
        'mt-3 flex items-start gap-2 rounded-lg border px-3.5 py-3 text-xs',
        result.ok
          ? 'border-success/30 bg-success/8 text-success'
          : 'border-danger/30 bg-danger/8 text-danger',
      )}
    >
      {result.ok ? (
        <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      ) : (
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      )}
      <span>{result.message}</span>
    </motion.div>
  );
}

// ─── SaveBar ──────────────────────────────────────────────────────────────────

function SaveBar({ dirty, saving, savedAt, onSave }) {
  return (
    <div className="mt-6 flex items-center justify-between gap-3 border-t border-line-subtle pt-5">
      <div className="text-xs">
        {dirty ? (
          <span className="inline-flex items-center gap-1.5 text-warning">
            <span className="size-2 animate-pulseRing rounded-full bg-warning" />
            Unsaved changes
          </span>
        ) : savedAt ? (
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
  );
}

// ─── CategoryEditor ───────────────────────────────────────────────────────────

function CategoryEditor({ category, items, draft, dirty, onChange, onSave, saving, savedAt, extras }) {
  // Separate bool fields from input fields — bools span full width in their own group
  const boolItems = items.filter((it) => fieldType(it.key) === 'bool');
  const inputItems = items.filter((it) => fieldType(it.key) !== 'bool');

  return (
    <motion.div variants={fadeIn} initial="hidden" animate="show">
      {/* Input / select fields in a 2-col grid */}
      {inputItems.length > 0 && (
        <div className="mb-5 grid gap-4 sm:grid-cols-2">
          {inputItems.map((it) => (
            <FieldRow key={it.key} item={it} draft={draft} onChange={onChange} />
          ))}
        </div>
      )}

      {/* Bool toggles in a separate section */}
      {boolItems.length > 0 && (
        <>
          {inputItems.length > 0 && (
            <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
              Toggles
            </p>
          )}
          <div className="mb-5 grid gap-2 sm:grid-cols-2">
            {boolItems.map((it) => (
              <FieldRow key={it.key} item={it} draft={draft} onChange={onChange} />
            ))}
          </div>
        </>
      )}

      <SaveBar dirty={dirty} saving={saving} savedAt={savedAt} onSave={onSave} />

      {extras}
    </motion.div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AdminSettingsPage() {
  const { data, isLoading } = useSettings();
  const update = useUpdateSettings();
  const [activeTab, setActiveTab] = useState('email');
  const [draft, setDraft] = useState({});
  const [savedAt, setSavedAt] = useState(null);

  useEffect(() => {
    if (!data) return;
    setDraft((cur) => {
      const next = { ...cur };
      for (const it of data.items) {
        if (next[it.key] === undefined) {
          next[it.key] = it.value ?? '';
        }
      }
      return next;
    });
  }, [data]);

  const itemsByCategory = useMemo(() => {
    const out = {};
    for (const it of data?.items || []) {
      (out[it.category] ||= []).push(it);
    }
    return out;
  }, [data]);

  function setField(key, value) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function buildPayload(category) {
    const items = itemsByCategory[category] || [];
    const updates = {};
    for (const it of items) {
      const cur = draft[it.key];
      const server = it.value ?? '';
      if (cur === undefined) continue;
      if (it.is_secret && cur === REDACTED) continue;
      if (cur !== server) updates[it.key] = cur;
    }
    return updates;
  }

  function isCategoryDirty(category) {
    return Object.keys(buildPayload(category)).length > 0;
  }

  async function saveCategory(category) {
    const updates = buildPayload(category);
    if (Object.keys(updates).length === 0) return;
    try {
      const fresh = await update.mutateAsync(updates);
      setDraft((d) => {
        const next = { ...d };
        for (const it of fresh.items) {
          if (Object.prototype.hasOwnProperty.call(updates, it.key)) {
            next[it.key] = it.value ?? '';
          }
        }
        return next;
      });
      setSavedAt(Date.now());
      setTimeout(() => setSavedAt(null), 2000);
    } catch (_err) {
      // useMutation surfaces the error; UI shows the Save button stop spinning
    }
  }

  // Test send
  const testEmail = useTestEmail();
  const testSms = useTestSms();
  const [testEmailTo, setTestEmailTo] = useState('');
  const [testSmsTo, setTestSmsTo] = useState('');

  function emailResult() {
    if (testEmail.isError) {
      return { ok: false, message: testEmail.error?.response?.data?.error?.message || 'Send failed' };
    }
    if (testEmail.isSuccess) return { ok: true, message: testEmail.data?.detail || 'Sent.' };
    return null;
  }
  function smsResult() {
    if (testSms.isError) {
      return { ok: false, message: testSms.error?.response?.data?.error?.message || 'Send failed' };
    }
    if (testSms.isSuccess) return { ok: true, message: testSms.data?.detail || 'Sent.' };
    return null;
  }

  if (isLoading) {
    return (
      <AdminPage title="Settings" description="Loading…">
        <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <div className="flex flex-wrap gap-1 border-b border-line-subtle bg-bg-sunken/60 px-4 py-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-24 rounded-md" />
            ))}
          </div>
          <div className="p-6">
            <div className="grid gap-4 sm:grid-cols-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i}>
                  <Skeleton variant="text" lines={1} className="mb-2 w-28" />
                  <Skeleton className="h-10 rounded-lg" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </AdminPage>
    );
  }

  const tabs = CATEGORY_ORDER.filter((c) => itemsByCategory[c]?.length > 0);

  return (
    <AdminPage
      title="Settings"
      description="Runtime configuration — changes take effect immediately. Sensitive values are masked once saved."
    >
      {/* Tab bar + content card */}
      <Card>
        {/* Tab strip */}
        <div
          role="tablist"
          className="flex flex-wrap gap-1 border-b border-line-subtle bg-bg-sunken/40 px-4 py-3"
        >
          {tabs.map((cat) => {
            const meta = TAB_META[cat] || { label: cat, icon: SettingsIcon };
            const Icon = meta.icon;
            const isDirty = isCategoryDirty(cat);
            return (
              <button
                key={cat}
                type="button"
                role="tab"
                aria-selected={activeTab === cat}
                onClick={() => setActiveTab(cat)}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors focus-visible:focus-ring',
                  activeTab === cat
                    ? 'bg-accent/12 font-medium text-accent'
                    : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                )}
              >
                <Icon className="size-4" aria-hidden="true" />
                {meta.label}
                {isDirty && activeTab !== cat && (
                  <span className="size-1.5 rounded-full bg-warning" aria-label="unsaved changes" />
                )}
              </button>
            );
          })}
        </div>

        {/* Active tab content */}
        <CardBody className="p-6">
          {tabs.map((cat) =>
            activeTab !== cat ? null : (
              <CategoryEditor
                key={cat}
                category={cat}
                items={itemsByCategory[cat] || []}
                draft={draft}
                dirty={isCategoryDirty(cat)}
                onChange={setField}
                onSave={() => saveCategory(cat)}
                saving={update.isPending}
                savedAt={savedAt}
                extras={
                  cat === 'email' ? (
                    <div className="mt-6 rounded-xl border border-line-subtle bg-bg-sunken p-5">
                      <CardHeader
                        title="Send a test email"
                        className="mb-4 border-none px-0 py-0"
                        action={
                          <Badge tone="info" size="sm">Test</Badge>
                        }
                      />
                      <p className="mb-4 text-xs text-ink-secondary">
                        Uses your saved SMTP credentials. Save changes first if you just edited them.
                      </p>
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <Input
                          type="email"
                          placeholder="you@example.com"
                          value={testEmailTo}
                          onChange={(e) => setTestEmailTo(e.target.value)}
                          aria-label="Test email recipient address"
                        />
                        <Button
                          variant="outline"
                          onClick={() => testEmail.mutate(testEmailTo)}
                          loading={testEmail.isPending}
                          disabled={!testEmailTo}
                        >
                          <Send className="size-4" aria-hidden="true" />
                          Send test
                        </Button>
                      </div>
                      <TestSendBanner
                        pending={testEmail.isPending}
                        result={emailResult()}
                        kind="email"
                      />
                    </div>
                  ) : cat === 'sms' ? (
                    <div className="mt-6 rounded-xl border border-line-subtle bg-bg-sunken p-5">
                      <CardHeader
                        title="Send a test SMS"
                        className="mb-4 border-none px-0 py-0"
                        action={
                          <Badge tone="info" size="sm">Test</Badge>
                        }
                      />
                      <p className="mb-4 text-xs text-ink-secondary">
                        Sends "Test SMS from your Lumen admin panel." via the active backend.
                      </p>
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <Input
                          placeholder="+14155551234"
                          value={testSmsTo}
                          onChange={(e) => setTestSmsTo(e.target.value)}
                          aria-label="Test SMS recipient phone number"
                        />
                        <Button
                          variant="outline"
                          onClick={() =>
                            testSms.mutate({
                              to: testSmsTo,
                              body: 'Test SMS from your Lumen admin panel.',
                            })
                          }
                          loading={testSms.isPending}
                          disabled={!testSmsTo}
                        >
                          <Send className="size-4" aria-hidden="true" />
                          Send test
                        </Button>
                      </div>
                      <TestSendBanner
                        pending={testSms.isPending}
                        result={smsResult()}
                        kind="SMS"
                      />
                    </div>
                  ) : cat === 'security' ? (
                    <div className="mt-6 rounded-xl border border-danger/30 bg-danger/4 p-5">
                      <div className="mb-3 flex items-center gap-2">
                        <AlertOctagon className="size-4 text-danger" aria-hidden="true" />
                        <p className="text-sm font-semibold text-danger">
                          Two-factor authentication notes
                        </p>
                      </div>
                      <ul className="space-y-2 pl-1 text-xs text-ink-secondary">
                        <li className="flex items-start gap-2">
                          <span className="mt-1 size-1.5 shrink-0 rounded-full bg-ink-tertiary" />
                          <span>
                            <strong className="text-ink-primary">Off</strong>: 2FA is unavailable.
                            Existing enrolled users log in with just their password.
                          </span>
                        </li>
                        <li className="flex items-start gap-2">
                          <span className="mt-1 size-1.5 shrink-0 rounded-full bg-ink-tertiary" />
                          <span>
                            <strong className="text-ink-primary">Optional</strong>: each user can
                            enable it on their Account → Security page.
                          </span>
                        </li>
                      </ul>
                    </div>
                  ) : null
                }
              />
            ),
          )}
        </CardBody>
      </Card>
    </AdminPage>
  );
}
