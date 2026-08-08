/**
 * Cost rules and marketing spend — the screen that lets a human type a number.
 *
 * Why this screen is not a normal settings form
 * ---------------------------------------------
 * `analytics_cost_rules` is effective-dated. Changing a rate does NOT update a
 * row: it closes the current row and inserts a new one from the day the new
 * rate takes effect, so a report for March still resolves March's rate. An
 * admin who believes they are correcting a typo is versioning a rate, and the
 * difference matters — it is the difference between "packaging costs ₹12 from
 * today" and "packaging has always cost ₹12", the second of which silently
 * restates every margin the store has ever reported.
 *
 * So this file spends real estate on saying so. `SupersedeNotice` states, before
 * the button is pressed, exactly which row will be closed, on which date, and
 * which buckets will be queued for recompute. That is not decoration: the
 * failure it prevents has no error message and no symptom until someone tries to
 * reproduce an old number.
 *
 * Marketing spend is the opposite case and is presented as such. A spend figure
 * is a measurement of a period that already happened; correcting it is an
 * ordinary upsert, because June's total was always whatever it was. The screen
 * says "replaces" there rather than "supersedes".
 *
 * What is pure and why
 * --------------------
 * Everything above `useCostRules` is pure and exported: range validation,
 * overlap detection, the monthly-to-daily allocation preview, formatting. The
 * project's vitest config runs in plain node with no jsdom, so components
 * cannot be rendered in a test — which means any logic that is worth asserting
 * has to live outside the component. The allocation preview in particular is a
 * mirror of `allocate_amount_across_days` in
 * `backend/app/models/analytics_spend.py`: it must agree with the server to the
 * paisa, and `__tests__/costRulesAdmin.test.js` asserts that it sums back
 * exactly rather than trusting that it does.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Coins, Megaphone, Plus, RotateCcw } from 'lucide-react';

import { apiClient } from '@/services/apiClient.js';
import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card, CardBody, CardHeader } from '@/components/ui/Card.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { toast } from '@/components/ui/Toaster.jsx';
import { DataTable } from '@/components/analytics/DataTable.jsx';
import { QualityBadge } from '@/components/analytics/QualityBadge.jsx';
import { formatMoney } from '@/features/analytics/format.js';

// ===========================================================================
// Vocabularies
//
// Defaults only. The list endpoints return the authoritative sets (`units`,
// `qualities`, `cost_types`, `grains`) and the selects prefer those, so adding
// a cost line server-side does not need a frontend deploy. These exist so the
// form still renders something usable before the first fetch resolves.
// ===========================================================================
export const DEFAULT_UNITS = ['pct', 'per_order', 'per_unit', 'per_kg', 'per_month'];
export const DEFAULT_QUALITIES = ['actual', 'contracted', 'estimated', 'assumed'];
export const DEFAULT_SCOPES = [
  'product',
  'category',
  'courier',
  'gateway',
  'payment_method',
  'global',
];
export const DEFAULT_GRAINS = ['daily', 'monthly'];

/** How a unit reads next to a number. `pct` is the 100x trap: 2.5 is 2.5%, not 0.025. */
export const UNIT_LABELS = Object.freeze({
  pct: '% of value',
  per_order: 'per order',
  per_unit: 'per unit',
  per_kg: 'per kg',
  per_month: 'per month',
});

/**
 * Backend cost/spend quality -> the `MetricQuality` vocabulary `QualityBadge`
 * speaks.
 *
 * They are deliberately different vocabularies (`actual|contracted|estimated|
 * assumed` describes a *source document*; `ACTUAL|ALLOCATED|ESTIMATED|
 * INCOMPLETE` describes a *number*), and `qualityMeta` renders anything it does
 * not recognise as INCOMPLETE — so a missing mapping here would silently paint
 * every rate as "inputs are missing".
 */
export const QUALITY_BADGE = Object.freeze({
  actual: 'ACTUAL',
  contracted: 'ACTUAL',
  estimated: 'ESTIMATED',
  assumed: 'ESTIMATED',
});

export function badgeQuality(quality) {
  return QUALITY_BADGE[quality] || 'INCOMPLETE';
}

// ===========================================================================
// Pure date / money helpers
// ===========================================================================
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Is this an ISO `YYYY-MM-DD` string that names a real calendar day? */
export function isIsoDate(value) {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  if (m < 1 || m > 12) return false;
  return d >= 1 && d <= daysInMonth(y, m);
}

/** Days in a 1-indexed month. `new Date(y, m, 0)` is the day-zero-of-next-month trick. */
export function daysInMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** Add days to an ISO date, in UTC so a DST boundary cannot shift the day. */
export function addDays(iso, days) {
  const [y, m, d] = iso.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

/**
 * First and last day of the month containing `iso`.
 *
 * Mirrors `month_bounds` server-side. An admin who wants "June" picks a date
 * from a picker and gets the 12th; treating that as a one-day period would put
 * a month of spend on a single day.
 */
export function monthBounds(iso) {
  const [y, m] = iso.split('-').map(Number);
  const pad = (n) => String(n).padStart(2, '0');
  return {
    start: `${y}-${pad(m)}-01`,
    end: `${y}-${pad(m)}-${pad(daysInMonth(y, m))}`,
  };
}

/**
 * Spread an amount across an inclusive day range, exactly.
 *
 * The mirror of `allocate_amount_across_days` in
 * `backend/app/models/analytics_spend.py`, and it has to be a mirror: this is
 * the preview an admin reads before saving, and a preview that disagrees with
 * what the server stores is worse than no preview.
 *
 * Arithmetic is in integer paise. ₹40 000 over 30 days is ₹1 333.333...;
 * rounding each day to ₹1 333.33 and summing gives ₹39 999.90, so the month is
 * ten paise short — small, invisible, and permanently wrong in a number that is
 * subtracted from revenue. The remainder is handed to the earliest days, always,
 * so the same input always produces the same split.
 */
export function allocateAcrossDays(amount, fromIso, toIso) {
  const paise = Math.round(Number(amount) * 100);
  if (!Number.isFinite(paise)) return [];
  const days = [];
  for (let cursor = fromIso; cursor <= toIso; cursor = addDays(cursor, 1)) {
    days.push(cursor);
    if (days.length > 400) break; // never let a bad range spin forever
  }
  if (days.length === 0) return [];

  const sign = paise < 0 ? -1 : 1;
  const magnitude = Math.abs(paise);
  const base = Math.floor(magnitude / days.length);
  const remainder = magnitude - base * days.length;

  return days.map((day, index) => ({
    day,
    amount: (sign * (base + (index < remainder ? 1 : 0))) / 100,
  }));
}

/** The daily preview for a spend form, or `[]` when it would not be allocated. */
export function allocationPreview(form) {
  if (form.grain !== 'monthly' || !isIsoDate(form.period)) return [];
  // A blank amount is NOT zero. `Number('')` is 0 and perfectly finite, so
  // relying on `isFinite` alone would render a confident 30-day preview of
  // ₹0.00 a day for a field nobody has typed in yet — the same "missing is not
  // zero" mistake the whole cost subsystem is built to avoid, on the screen
  // that is supposed to teach it.
  if (form.amount === '' || form.amount === null || form.amount === undefined) return [];
  const amount = Number(form.amount);
  if (!Number.isFinite(amount)) return [];
  const { start, end } = monthBounds(form.period);
  return allocateAcrossDays(amount, start, end);
}

/** Sum an allocation in paise then convert back, so the total cannot drift. */
export function allocationTotal(rows) {
  const paise = rows.reduce((acc, row) => acc + Math.round(row.amount * 100), 0);
  return paise / 100;
}

// ===========================================================================
// Effective-range logic
// ===========================================================================
/**
 * Do two inclusive windows intersect? A null end is "still in force" = +infinity.
 *
 * Written as the plain two-predicate form because getting it wrong fails *open*:
 * a missed overlap is not an error, it is two contradictory rules coexisting and
 * a resolved rate that depends on row order.
 */
export function rangesOverlap(aFrom, aTo, bFrom, bTo) {
  const aEnd = aTo || '9999-12-31';
  const bEnd = bTo || '9999-12-31';
  return aFrom <= bEnd && bFrom <= aEnd;
}

/**
 * The first existing rule a proposed window would collide with, or null.
 *
 * Client-side preview of the server's 409, on the same key
 * `(cost_type, scope, scope_value)`. The server is still the authority — this
 * only means the admin finds out while typing rather than after saving.
 */
export function findOverlappingRule(rules, proposed, excludeId = null) {
  return (
    (rules || []).find(
      (rule) =>
        rule.id !== excludeId &&
        rule.cost_type === proposed.cost_type &&
        rule.scope === proposed.scope &&
        rule.scope_value === proposed.scope_value &&
        rangesOverlap(
          rule.effective_from,
          rule.effective_to,
          proposed.effective_from,
          proposed.effective_to,
        ),
    ) || null
  );
}

/** "1 Mar 2018 – 31 Mar 2018" / "1 Mar 2018 – now". */
export function formatEffectiveRange(rule) {
  const from = formatDay(rule.effective_from);
  return rule.effective_to ? `${from} – ${formatDay(rule.effective_to)}` : `${from} – now`;
}

export function formatDay(iso) {
  if (!isIsoDate(iso)) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

/** A rule with no end date is the one currently in force. */
export function isCurrent(rule, today) {
  const now = today || new Date().toISOString().slice(0, 10);
  return rule.effective_from <= now && (!rule.effective_to || rule.effective_to >= now);
}

/**
 * What superseding this rule on this date will actually do.
 *
 * Returned as data rather than rendered inline so the wording is assertable: an
 * admin who thinks they are fixing a typo needs to read "rule #4 will be closed
 * on 10 Mar" before they press the button, not after.
 */
export function describeSupersession(rule, effectiveFrom) {
  if (!rule || !isIsoDate(effectiveFrom)) return null;
  const closesOn = addDays(effectiveFrom, -1);
  const valid = effectiveFrom > rule.effective_from;
  return {
    ruleId: rule.id,
    valid,
    closesOn,
    recomputeFrom: effectiveFrom,
    recomputeTo: rule.effective_to,
    message: valid
      ? `Rule #${rule.id} keeps its value of ${rule.value} and is closed on ` +
        `${formatDay(closesOn)}. A new rule takes effect on ${formatDay(effectiveFrom)}. ` +
        'Reports for any date up to the close date still resolve the old rate.'
      : `The new rate must start after ${formatDay(rule.effective_from)}, the day ` +
        'this rule took effect — otherwise the old rule would cover no days at ' +
        'all, which is an edit of history rather than a new version.',
  };
}

// ===========================================================================
// Form validation
// ===========================================================================
export const EMPTY_RULE_FORM = Object.freeze({
  cost_type: 'packaging',
  scope: 'global',
  scope_value: '-',
  value: '',
  unit: 'per_order',
  quality: 'assumed',
  currency: 'INR',
  effective_from: '',
  effective_to: '',
  source: '',
  note: '',
});

export const EMPTY_SPEND_FORM = Object.freeze({
  grain: 'monthly',
  period: '',
  channel: '',
  campaign: '-',
  amount: '',
  currency: 'INR',
  quality: 'assumed',
  source: 'manual',
  note: '',
});

/** Field-keyed errors, `{}` when the form is submittable. */
export function validateRuleForm(form, { mode = 'create', rules = [], rule = null } = {}) {
  const errors = {};

  if (mode === 'create' && !form.cost_type) errors.cost_type = 'Pick a cost type.';
  if (mode === 'create' && form.scope !== 'global' && (!form.scope_value || form.scope_value === '-')) {
    // A scoped rule whose scope_value is the '-' sentinel is a global rule
    // wearing a scope, and it would never match the bucket it was meant for.
    errors.scope_value = 'A scoped rule needs the courier, gateway, category or SKU it applies to.';
  }

  const value = Number(form.value);
  if (form.value === '' || !Number.isFinite(value)) {
    errors.value = 'Enter the rate.';
  } else if (value < 0) {
    errors.value = 'A cost cannot be negative. Record income as a separate line.';
  } else if (form.unit === 'pct' && value > 100) {
    // 2.5% is 2.5, not 0.025 — and 250 is the shape of someone entering basis
    // points or a rupee amount into a percentage field.
    errors.value = 'A percentage over 100 is almost certainly a unit mix-up — pct means 2.5 for 2.5%.';
  }

  if (!isIsoDate(form.effective_from)) {
    errors.effective_from = 'Pick the day this rate takes effect.';
  }
  if (form.effective_to && !isIsoDate(form.effective_to)) {
    errors.effective_to = 'Not a valid date.';
  }
  if (
    isIsoDate(form.effective_from) &&
    isIsoDate(form.effective_to) &&
    form.effective_to < form.effective_from
  ) {
    errors.effective_to = 'The end date is before the start date.';
  }

  if (mode === 'supersede' && rule && isIsoDate(form.effective_from)) {
    if (form.effective_from <= rule.effective_from) {
      errors.effective_from =
        `Must be after ${formatDay(rule.effective_from)} — superseding on or before ` +
        'the day the rule started would leave it covering no days.';
    }
    if (rule.effective_to && form.effective_from > rule.effective_to) {
      errors.effective_from =
        `Rule #${rule.id} already ended on ${formatDay(rule.effective_to)}. ` +
        'Add a new rule for the uncovered window instead.';
    }
  }

  if (mode === 'create' && !errors.effective_from && !errors.effective_to) {
    const clash = findOverlappingRule(rules, {
      cost_type: form.cost_type,
      scope: form.scope,
      scope_value: form.scope_value,
      effective_from: form.effective_from,
      effective_to: form.effective_to || null,
    });
    if (clash) {
      errors.effective_from =
        `Rule #${clash.id} already covers ${formatEffectiveRange(clash)}. ` +
        'Two overlapping rules would make the resolved rate depend on row order — ' +
        'supersede it instead.';
    }
  }

  return errors;
}

export function validateSpendForm(form) {
  const errors = {};
  if (!isIsoDate(form.period)) errors.period = 'Pick the day or month.';
  if (!form.channel || form.channel.trim() === '') {
    errors.channel = "Name the channel, or use '-' for unattributed marketing.";
  }
  const amount = Number(form.amount);
  if (form.amount === '' || !Number.isFinite(amount)) {
    // Blank is not zero. Zero spend recorded as fact makes acquisition free.
    errors.amount = 'Enter the amount spent.';
  } else if (amount < 0) {
    errors.amount = 'Spend cannot be negative.';
  }
  return errors;
}

export function hasErrors(errors) {
  return Object.keys(errors).length > 0;
}

// ===========================================================================
// Payload builders
// ===========================================================================
export function buildRulePayload(form, { mode = 'create' } = {}) {
  const base = {
    value: String(form.value),
    unit: form.unit,
    quality: form.quality,
    currency: form.currency || 'INR',
    effective_from: form.effective_from,
    // '' from an untouched date input is "open-ended", not "the epoch".
    effective_to: form.effective_to || null,
    source: form.source || null,
    note: form.note || null,
  };
  if (mode === 'supersede') return base;
  return {
    ...base,
    cost_type: form.cost_type,
    scope: form.scope,
    // A global rule carries the '-' sentinel; it is NOT NULL server-side
    // because the UNIQUE key spans it.
    scope_value: form.scope === 'global' ? '-' : form.scope_value,
  };
}

export function buildSpendPayload(form) {
  return {
    grain: form.grain,
    period: form.period,
    channel: form.channel.trim() || '-',
    campaign: (form.campaign || '-').trim() || '-',
    amount: String(form.amount),
    currency: form.currency || 'INR',
    quality: form.quality,
    source: form.source || 'manual',
    note: form.note || null,
  };
}

/** The house error shape, with a caller-supplied fallback. */
export function costAdminError(error, fallback = 'Something went wrong.') {
  return error?.response?.data?.error?.message || error?.message || fallback;
}

// ===========================================================================
// Data layer
// ===========================================================================
export const costAdminApi = {
  listRules: (params = {}) =>
    apiClient.get('/analytics/admin/cost-rules', { params }).then((r) => r.data),
  createRule: (payload) =>
    apiClient.post('/analytics/admin/cost-rules', payload).then((r) => r.data),
  supersedeRule: (id, payload) =>
    apiClient
      .post(`/analytics/admin/cost-rules/${id}/supersede`, payload)
      .then((r) => r.data),
  listSpend: (params = {}) =>
    apiClient.get('/analytics/admin/marketing-spend', { params }).then((r) => r.data),
  upsertSpend: (payload) =>
    apiClient.post('/analytics/admin/marketing-spend', payload).then((r) => r.data),
};

const RULES_KEY = ['analytics', 'cost-rules'];
const SPEND_KEY = ['analytics', 'marketing-spend'];

function useInvalidate(key) {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: key });
}

export function useCostRules() {
  return useQuery({
    queryKey: RULES_KEY,
    queryFn: () => costAdminApi.listRules(),
    // This is an edit surface: a background refetch landing mid-form would
    // repaint the table under the admin's cursor while they are reading the
    // window they are about to supersede.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

export function useMarketingSpend() {
  return useQuery({
    queryKey: SPEND_KEY,
    queryFn: () => costAdminApi.listSpend({ include_daily: false }),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

export function useCreateRule() {
  const invalidate = useInvalidate(RULES_KEY);
  return useMutation({ mutationFn: costAdminApi.createRule, onSuccess: invalidate });
}

export function useSupersedeRule() {
  const invalidate = useInvalidate(RULES_KEY);
  return useMutation({
    mutationFn: ({ id, payload }) => costAdminApi.supersedeRule(id, payload),
    onSuccess: invalidate,
  });
}

export function useUpsertSpend() {
  const invalidate = useInvalidate(SPEND_KEY);
  return useMutation({ mutationFn: costAdminApi.upsertSpend, onSuccess: invalidate });
}

// ===========================================================================
// Presentation
// ===========================================================================
const RULE_COLUMNS = [
  { key: 'cost_type', label: 'Cost', sortable: true },
  { key: 'scope_label', label: 'Applies to' },
  { key: 'rate', label: 'Rate', align: 'right' },
  { key: 'range', label: 'In force', sortable: true },
  { key: 'quality', label: 'Confidence' },
  { key: 'source', label: 'Source' },
  { key: 'actions', label: '' },
];

const SPEND_COLUMNS = [
  { key: 'period', label: 'Period', sortable: true },
  { key: 'channel', label: 'Channel', sortable: true },
  { key: 'campaign', label: 'Campaign' },
  { key: 'amount', label: 'Spend', format: 'money', align: 'right', sortable: true },
  { key: 'grain', label: 'Grain' },
  { key: 'quality', label: 'Confidence' },
  { key: 'source', label: 'Entered by' },
];

/** The standing explanation. Deliberately above the table, not inside a tooltip. */
function VersioningNotice() {
  return (
    <Card flat>
      <CardBody className="flex gap-3">
        <CalendarClock className="mt-0.5 h-5 w-5 shrink-0 text-accent" aria-hidden="true" />
        <div className="space-y-1 text-sm">
          <p className="font-medium">Rates are versioned, not edited.</p>
          <p className="text-muted-foreground">
            Changing a rate does not overwrite it. The current rule is closed on the
            day before the new rate starts and a new dated rule is added, so a report
            for any earlier date still resolves the old rate — last quarter&apos;s margin
            keeps reproducing. Saving also queues the affected days for recompute, so
            the change reaches the dashboards deliberately rather than whenever
            something else happens to rebuild them.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

/** Says what the pending supersession will do, before it is done. */
function SupersedeNotice({ plan }) {
  if (!plan) return null;
  return (
    <p
      className={
        plan.valid
          ? 'rounded-md border border-accent/30 bg-accent/8 px-3 py-2 text-xs text-foreground'
          : 'rounded-md border border-danger/30 bg-danger/8 px-3 py-2 text-xs text-danger'
      }
    >
      {plan.message}
    </p>
  );
}

function RuleForm({ mode, rule, rules, form, setForm, vocab, onSubmit, onCancel, pending }) {
  const errors = validateRuleForm(form, { mode, rules, rule });
  const plan = mode === 'supersede' ? describeSupersession(rule, form.effective_from) : null;
  const set = (field) => (event) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!hasErrors(errors)) onSubmit();
      }}
    >
      {mode === 'create' ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <Select label="Cost type" value={form.cost_type} onChange={set('cost_type')} error={errors.cost_type}>
            {vocab.cost_types.map((option) => (
              <option key={option} value={option}>
                {option.replace(/_/g, ' ')}
              </option>
            ))}
          </Select>
          <Select label="Scope" value={form.scope} onChange={set('scope')}>
            {vocab.scopes.map((option) => (
              <option key={option} value={option}>
                {option.replace(/_/g, ' ')}
              </option>
            ))}
          </Select>
          <Input
            label="Applies to"
            value={form.scope === 'global' ? '-' : form.scope_value}
            onChange={set('scope_value')}
            disabled={form.scope === 'global'}
            helper={form.scope === 'global' ? 'Every order' : 'Courier, gateway, category or SKU'}
            error={errors.scope_value}
          />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          Superseding <span className="font-medium text-foreground">#{rule?.id}</span>{' '}
          {rule?.cost_type?.replace(/_/g, ' ')} ({formatEffectiveRange(rule || {})}), currently{' '}
          {rule?.value} {UNIT_LABELS[rule?.unit] || rule?.unit}.
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Input
          label="Rate"
          type="number"
          step="0.0001"
          value={form.value}
          onChange={set('value')}
          error={errors.value}
          helper={form.unit === 'pct' ? '2.5 means 2.5%' : 'Amount in rupees'}
          required
        />
        <Select label="Unit" value={form.unit} onChange={set('unit')}>
          {vocab.units.map((option) => (
            <option key={option} value={option}>
              {UNIT_LABELS[option] || option}
            </option>
          ))}
        </Select>
        <Select
          label="Confidence"
          value={form.quality}
          onChange={set('quality')}
          helper="Rides through to the margin's quality label"
        >
          {vocab.qualities.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Select>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label={mode === 'supersede' ? 'New rate takes effect' : 'In force from'}
          type="date"
          value={form.effective_from}
          onChange={set('effective_from')}
          error={errors.effective_from}
          required
        />
        <Input
          label="In force until"
          type="date"
          value={form.effective_to}
          onChange={set('effective_to')}
          error={errors.effective_to}
          helper="Leave blank while the rate is still current"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="Source"
          value={form.source}
          onChange={set('source')}
          helper="e.g. razorpay_settlement_2026_03"
        />
        <Input label="Note" value={form.note} onChange={set('note')} />
      </div>

      <SupersedeNotice plan={plan} />

      <div className="flex gap-2">
        <Button type="submit" loading={pending} disabled={hasErrors(errors)}>
          {mode === 'supersede' ? 'Close and version this rate' : 'Add rule'}
        </Button>
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}

function SpendForm({ form, setForm, vocab, onSubmit, pending }) {
  const errors = validateSpendForm(form);
  const preview = useMemo(() => allocationPreview(form), [form]);
  const set = (field) => (event) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!hasErrors(errors)) onSubmit();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-4">
        <Select label="Grain" value={form.grain} onChange={set('grain')}>
          {vocab.grains.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Select>
        <Input
          label={form.grain === 'monthly' ? 'Any day in the month' : 'Day'}
          type="date"
          value={form.period}
          onChange={set('period')}
          error={errors.period}
          required
        />
        <Input
          label="Channel"
          value={form.channel}
          onChange={set('channel')}
          error={errors.channel}
          helper="meta, google… or - for all"
          required
        />
        <Input
          label="Amount spent"
          type="number"
          step="0.01"
          value={form.amount}
          onChange={set('amount')}
          error={errors.amount}
          required
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Input label="Campaign" value={form.campaign} onChange={set('campaign')} helper="- for the channel total" />
        <Select label="Confidence" value={form.quality} onChange={set('quality')}>
          {vocab.qualities.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Select>
        <Input label="Note" value={form.note} onChange={set('note')} helper="Invoice number, agency…" />
      </div>

      {preview.length > 0 ? (
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          A monthly figure is spread over {preview.length} days —{' '}
          {formatMoney(preview[0].amount)} a day, summing back to{' '}
          {formatMoney(allocationTotal(preview))} to the paisa. Those daily values are
          labelled <Badge tone="info" size="sm">ALLOCATED</Badge>, never actual: the
          invoice is evidence about the month, not about any one day.
        </p>
      ) : null}

      <p className="text-xs text-muted-foreground">
        Re-entering the same channel and period <span className="font-medium">replaces</span>{' '}
        the figure rather than adding to it, so saving twice is harmless. Unlike a
        rate, spend is not versioned — the month&apos;s total was always whatever it
        was, and an earlier figure was simply wrong.
      </p>

      <Button type="submit" loading={pending} disabled={hasErrors(errors)}>
        Record spend
      </Button>
    </form>
  );
}

export function CostRulesAdmin() {
  const rulesQuery = useCostRules();
  const spendQuery = useMarketingSpend();
  const createRule = useCreateRule();
  const supersedeRule = useSupersedeRule();
  const upsertSpend = useUpsertSpend();

  const [ruleMode, setRuleMode] = useState('create');
  const [targetRule, setTargetRule] = useState(null);
  const [ruleForm, setRuleForm] = useState({ ...EMPTY_RULE_FORM });
  const [spendForm, setSpendForm] = useState({ ...EMPTY_SPEND_FORM });

  // Memoised, not just defaulted. `?? []` mints a NEW array on every render
  // when the query has no data, which would re-run the row-shaping useMemos
  // below on every keystroke in the forms — and the rules table is what the
  // overlap check reads while the admin is typing a date into it.
  const rules = useMemo(() => rulesQuery.data?.rules || [], [rulesQuery.data]);
  const spend = useMemo(() => spendQuery.data?.spend || [], [spendQuery.data]);

  const ruleVocab = {
    cost_types: rulesQuery.data?.cost_types || ['packaging'],
    scopes: rulesQuery.data?.scopes || DEFAULT_SCOPES,
    units: rulesQuery.data?.units || DEFAULT_UNITS,
    qualities: rulesQuery.data?.qualities || DEFAULT_QUALITIES,
  };
  const spendVocab = {
    grains: spendQuery.data?.grains || DEFAULT_GRAINS,
    qualities: spendQuery.data?.qualities || DEFAULT_QUALITIES,
  };

  const ruleRows = useMemo(
    () =>
      rules.map((rule) => ({
        ...rule,
        scope_label: rule.scope === 'global' ? 'All orders' : `${rule.scope}: ${rule.scope_value}`,
        rate: `${rule.value} ${UNIT_LABELS[rule.unit] || rule.unit}`,
        range: formatEffectiveRange(rule),
      })),
    [rules],
  );

  const spendRows = useMemo(
    () =>
      spend.map((row) => ({
        ...row,
        period:
          row.grain === 'monthly'
            ? `${formatDay(row.period_start)} – ${formatDay(row.period_end)}`
            : formatDay(row.period_start),
        amount: Number(row.amount),
      })),
    [spend],
  );

  function startSupersede(rule) {
    setRuleMode('supersede');
    setTargetRule(rule);
    setRuleForm({
      ...EMPTY_RULE_FORM,
      cost_type: rule.cost_type,
      scope: rule.scope,
      scope_value: rule.scope_value,
      value: rule.value,
      unit: rule.unit,
      quality: rule.quality,
      currency: rule.currency,
      effective_from: '',
      effective_to: rule.effective_to || '',
    });
  }

  function resetRuleForm() {
    setRuleMode('create');
    setTargetRule(null);
    setRuleForm({ ...EMPTY_RULE_FORM });
  }

  async function submitRule() {
    try {
      const payload = buildRulePayload(ruleForm, { mode: ruleMode });
      const result =
        ruleMode === 'supersede'
          ? await supersedeRule.mutateAsync({ id: targetRule.id, payload })
          : await createRule.mutateAsync(payload);
      // The queued count is the answer to "did my change take effect?", so it
      // goes in the toast rather than being left for the operator to infer.
      toast.success(
        ruleMode === 'supersede'
          ? `Rate versioned. ${result.recompute_queued} buckets queued for recompute.`
          : `Rule added. ${result.recompute_queued} buckets queued for recompute.`,
      );
      (result.warnings || []).forEach((warning) => toast.info(warning));
      resetRuleForm();
    } catch (error) {
      toast.error(costAdminError(error, 'Could not save the cost rule.'));
    }
  }

  async function submitSpend() {
    try {
      const result = await upsertSpend.mutateAsync(buildSpendPayload(spendForm));
      toast.success(
        result.created
          ? `Spend recorded. ${result.recompute_queued} buckets queued.`
          : `Spend revised from ${result.previous_amount}. ${result.recompute_queued} buckets queued.`,
      );
      setSpendForm({ ...EMPTY_SPEND_FORM, grain: spendForm.grain });
    } catch (error) {
      toast.error(costAdminError(error, 'Could not record the spend.'));
    }
  }

  return (
    <>
      <VersioningNotice />

      <Card>
        <CardHeader
          title="Cost rules"
          action={
            ruleMode === 'supersede' ? (
              <Button size="sm" variant="ghost" onClick={resetRuleForm}>
                <RotateCcw className="h-4 w-4" aria-hidden="true" /> New rule instead
              </Button>
            ) : null
          }
        />
        <CardBody className="space-y-6">
          {rulesQuery.isError ? (
            <EmptyState
              icon={Coins}
              iconTone="danger"
              title="Couldn't load cost rules"
              description={costAdminError(rulesQuery.error, 'Something went wrong.')}
              action={
                <Button size="sm" onClick={() => rulesQuery.refetch()}>
                  Retry
                </Button>
              }
            />
          ) : rulesQuery.isLoading ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 3 }).map((_, index) => (
                <Skeleton key={index} className="h-14" />
              ))}
            </div>
          ) : ruleRows.length === 0 ? (
            <EmptyState
              icon={Coins}
              title="No cost rules yet"
              description="Until a rule exists, every margin metric reports as incomplete — a missing cost is never treated as zero. Add the first rate below."
            />
          ) : (
            <DataTable
              columns={RULE_COLUMNS}
              rows={ruleRows}
              defaultSort="range"
              getRowKey={(row) => row.id}
              renderCell={(row, column) => {
                if (column.key === 'quality') {
                  return <QualityBadge quality={badgeQuality(row.quality)} />;
                }
                if (column.key === 'range') {
                  return (
                    <span className="flex items-center gap-2">
                      {row.range}
                      {isCurrent(row) ? (
                        <Badge tone="success" size="sm">
                          in force
                        </Badge>
                      ) : (
                        <Badge tone="neutral" size="sm" outline>
                          superseded
                        </Badge>
                      )}
                    </span>
                  );
                }
                if (column.key === 'actions') {
                  return (
                    <Button size="sm" variant="outline" onClick={() => startSupersede(row)}>
                      Change rate
                    </Button>
                  );
                }
                return undefined;
              }}
            />
          )}

          <div className="border-t border-border pt-6">
            <h3 className="mb-4 flex items-center gap-2 text-sm font-medium">
              {ruleMode === 'supersede' ? (
                <>
                  <CalendarClock className="h-4 w-4" aria-hidden="true" /> Version this rate
                </>
              ) : (
                <>
                  <Plus className="h-4 w-4" aria-hidden="true" /> Add a cost rule
                </>
              )}
            </h3>
            <RuleForm
              mode={ruleMode}
              rule={targetRule}
              rules={rules}
              form={ruleForm}
              setForm={setRuleForm}
              vocab={ruleVocab}
              onSubmit={submitRule}
              onCancel={ruleMode === 'supersede' ? resetRuleForm : null}
              pending={createRule.isPending || supersedeRule.isPending}
            />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Marketing spend" />
        <CardBody className="space-y-6">
          {spendQuery.isError ? (
            <EmptyState
              icon={Megaphone}
              iconTone="danger"
              title="Couldn't load marketing spend"
              description={costAdminError(spendQuery.error, 'Something went wrong.')}
              action={
                <Button size="sm" onClick={() => spendQuery.refetch()}>
                  Retry
                </Button>
              }
            />
          ) : spendQuery.isLoading ? (
            <Skeleton className="h-32" />
          ) : spendRows.length === 0 ? (
            <EmptyState
              icon={Megaphone}
              title="No spend recorded"
              description="CAC, ROAS and payback stay unavailable until the store says what it spent. A monthly total per channel is enough to start."
            />
          ) : (
            <DataTable
              columns={SPEND_COLUMNS}
              rows={spendRows}
              defaultSort="period"
              pageSize={12}
              getRowKey={(row) => row.id}
              renderCell={(row, column) =>
                column.key === 'quality' ? (
                  <QualityBadge quality={badgeQuality(row.quality)} />
                ) : column.key === 'grain' ? (
                  <Badge tone={row.grain === 'monthly' ? 'info' : 'neutral'} size="sm" outline>
                    {row.grain}
                  </Badge>
                ) : undefined
              }
            />
          )}

          <div className="border-t border-border pt-6">
            <h3 className="mb-4 flex items-center gap-2 text-sm font-medium">
              <Plus className="h-4 w-4" aria-hidden="true" /> Record spend
            </h3>
            <SpendForm
              form={spendForm}
              setForm={setSpendForm}
              vocab={spendVocab}
              onSubmit={submitSpend}
              pending={upsertSpend.isPending}
            />
          </div>
        </CardBody>
      </Card>
    </>
  );
}
