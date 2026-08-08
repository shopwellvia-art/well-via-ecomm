/**
 * Unit tests for the cost-rule / marketing-spend screen's pure logic.
 *
 * The suite runs in plain node with no jsdom (see `vitest.config.js`), so
 * nothing here renders a component. That constraint is why the logic worth
 * asserting — effective-range validation, overlap detection, the monthly
 * allocation preview — lives outside the component in the first place.
 *
 * Three things are actually being defended:
 *
 * 1. **The allocation preview matches the server to the paisa.** It is a mirror
 *    of `allocate_amount_across_days` in `backend/app/models/analytics_spend.py`.
 *    A preview that disagrees with what gets stored is worse than no preview,
 *    and the failure is silent: the admin reads ₹1 333.33 × 30 and the month is
 *    ten paise short forever.
 * 2. **Overlap detection does not fail open.** A missed overlap is not an error
 *    — it is two contradictory rules coexisting and a resolved rate that depends
 *    on row order. The null-end (`still in force` = +infinity) cases are the ones
 *    that get this wrong.
 * 3. **Supersession is described before it happens.** An admin who thinks they
 *    are fixing a typo is versioning a rate; `describeSupersession` is the text
 *    that tells them, so its content is asserted rather than assumed.
 */
import { describe, it, expect } from 'vitest';

import {
  addDays,
  allocateAcrossDays,
  allocationPreview,
  allocationTotal,
  badgeQuality,
  buildRulePayload,
  buildSpendPayload,
  costAdminError,
  daysInMonth,
  describeSupersession,
  EMPTY_RULE_FORM,
  EMPTY_SPEND_FORM,
  findOverlappingRule,
  formatEffectiveRange,
  hasErrors,
  isCurrent,
  isIsoDate,
  monthBounds,
  rangesOverlap,
  validateRuleForm,
  validateSpendForm,
} from '@/features/analytics/CostRulesAdmin.jsx';

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------
describe('date helpers', () => {
  it('rejects anything that is not a real calendar day', () => {
    expect(isIsoDate('2026-07-29')).toBe(true);
    expect(isIsoDate('2026-02-29')).toBe(false); // 2026 is not a leap year
    expect(isIsoDate('2024-02-29')).toBe(true);
    expect(isIsoDate('2026-13-01')).toBe(false);
    expect(isIsoDate('2026-7-9')).toBe(false);
    expect(isIsoDate('')).toBe(false);
    expect(isIsoDate(null)).toBe(false);
  });

  it('counts month lengths including February in a leap year', () => {
    expect(daysInMonth(2026, 6)).toBe(30);
    expect(daysInMonth(2026, 2)).toBe(28);
    expect(daysInMonth(2024, 2)).toBe(29);
    expect(daysInMonth(2026, 12)).toBe(31);
  });

  it('adds days across month and year boundaries', () => {
    expect(addDays('2026-06-30', 1)).toBe('2026-07-01');
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31');
    expect(addDays('2024-02-28', 1)).toBe('2024-02-29');
  });

  it('normalises any day in a month to the whole month', () => {
    // An admin who means "June" picks a date from a picker and gets the 12th.
    // Treating that as a one-day period would put a month of spend on one day.
    expect(monthBounds('2026-06-12')).toEqual({ start: '2026-06-01', end: '2026-06-30' });
    expect(monthBounds('2024-02-05')).toEqual({ start: '2024-02-01', end: '2024-02-29' });
  });
});

// ---------------------------------------------------------------------------
// Allocation — the paisa-exactness property
// ---------------------------------------------------------------------------
describe('allocateAcrossDays', () => {
  it.each([
    ['40000', '2026-06-01', '2026-06-30', 30],
    ['40000', '2026-07-01', '2026-07-31', 31],
    ['100', '2026-02-01', '2026-02-28', 28],
    ['0.01', '2026-07-01', '2026-07-31', 31],
    ['12345.67', '2026-06-01', '2026-06-30', 30],
    ['1', '2026-06-01', '2026-06-01', 1],
  ])('spreads %s over %s..%s and sums back exactly', (amount, from, to, days) => {
    const rows = allocateAcrossDays(amount, from, to);
    expect(rows).toHaveLength(days);
    // Summed in paise, because summing the rupee floats is the exact bug this
    // function exists to avoid.
    expect(allocationTotal(rows)).toBe(Number(amount));
    // Every share is a real money amount, not a fraction of a paisa.
    for (const row of rows) {
      expect(Math.round(row.amount * 100)).toBe(row.amount * 100);
    }
  });

  it('gives the remainder to the earliest days, deterministically', () => {
    // ₹40 000 / 30 = ₹1 333.3333; 4 000 000 paise = 133333 × 30 + 10, so the
    // first ten days carry one extra paisa. Fixed ordering matters more than
    // which order: two recomputes must never produce different daily charts.
    const rows = allocateAcrossDays('40000', '2026-06-01', '2026-06-30');
    expect(rows[0].amount).toBe(1333.34);
    expect(rows[9].amount).toBe(1333.34);
    expect(rows[10].amount).toBe(1333.33);
    expect(rows).toEqual(allocateAcrossDays('40000', '2026-06-01', '2026-06-30'));
  });

  it('returns nothing for an inverted range rather than inventing a day', () => {
    expect(allocateAcrossDays('100', '2026-06-30', '2026-06-01')).toEqual([]);
  });

  it('only previews an allocation for a monthly entry with a usable amount', () => {
    expect(allocationPreview({ ...EMPTY_SPEND_FORM, grain: 'daily', period: '2026-06-12', amount: '500' })).toEqual([]);
    expect(allocationPreview({ ...EMPTY_SPEND_FORM, grain: 'monthly', period: '', amount: '500' })).toEqual([]);
    expect(allocationPreview({ ...EMPTY_SPEND_FORM, grain: 'monthly', period: '2026-06-12', amount: '' })).toEqual([]);

    const preview = allocationPreview({
      ...EMPTY_SPEND_FORM,
      grain: 'monthly',
      period: '2026-06-12',
      amount: '40000',
    });
    expect(preview).toHaveLength(30);
    expect(preview[0].day).toBe('2026-06-01');
    expect(allocationTotal(preview)).toBe(40000);
  });
});

// ---------------------------------------------------------------------------
// Effective ranges
// ---------------------------------------------------------------------------
describe('rangesOverlap', () => {
  it('detects an intersection', () => {
    expect(rangesOverlap('2026-01-01', '2026-01-31', '2026-01-15', '2026-02-15')).toBe(true);
  });

  it('allows back-to-back windows that only touch', () => {
    // The normal shape of a rate history: one ends, the next begins the day
    // after. Rejecting this would make supersession impossible.
    expect(rangesOverlap('2026-01-01', '2026-01-31', '2026-02-01', '2026-02-28')).toBe(false);
  });

  it('treats a null end as still in force, not as unbounded-below', () => {
    // The failure mode that matters: reading null as "no upper bound to
    // compare" makes the check pass and two live rules coexist.
    expect(rangesOverlap('2026-01-01', null, '2030-01-01', null)).toBe(true);
    expect(rangesOverlap('2030-01-01', null, '2026-01-01', '2026-12-31')).toBe(false);
  });
});

const RULES = [
  {
    id: 4,
    cost_type: 'packaging',
    scope: 'global',
    scope_value: '-',
    value: '8.0000',
    unit: 'per_order',
    quality: 'assumed',
    effective_from: '2026-03-01',
    effective_to: null,
  },
  {
    id: 5,
    cost_type: 'forward_shipping',
    scope: 'courier',
    scope_value: 'bluedart',
    value: '55.0000',
    unit: 'per_order',
    quality: 'contracted',
    effective_from: '2026-01-01',
    effective_to: '2026-02-28',
  },
];

describe('findOverlappingRule', () => {
  it('finds the live rule a new window would collide with', () => {
    const clash = findOverlappingRule(RULES, {
      cost_type: 'packaging',
      scope: 'global',
      scope_value: '-',
      effective_from: '2026-06-01',
      effective_to: null,
    });
    expect(clash?.id).toBe(4);
  });

  it('keys on the full (cost_type, scope, scope_value) triple', () => {
    // A per-courier rate applies to that courier and nothing else, so a
    // different courier is not a conflict.
    expect(
      findOverlappingRule(RULES, {
        cost_type: 'forward_shipping',
        scope: 'courier',
        scope_value: 'delhivery',
        effective_from: '2026-01-15',
        effective_to: null,
      }),
    ).toBeNull();
    expect(
      findOverlappingRule(RULES, {
        cost_type: 'handling',
        scope: 'global',
        scope_value: '-',
        effective_from: '2026-06-01',
        effective_to: null,
      }),
    ).toBeNull();
  });

  it('ignores the rule being superseded', () => {
    expect(
      findOverlappingRule(
        RULES,
        {
          cost_type: 'packaging',
          scope: 'global',
          scope_value: '-',
          effective_from: '2026-06-01',
          effective_to: null,
        },
        4,
      ),
    ).toBeNull();
  });
});

describe('formatEffectiveRange / isCurrent', () => {
  it('says "now" for an open-ended rule', () => {
    expect(formatEffectiveRange(RULES[0])).toBe('1 Mar 2026 – now');
    expect(formatEffectiveRange(RULES[1])).toBe('1 Jan 2026 – 28 Feb 2026');
  });

  it('judges currency by a supplied day, never by the clock', () => {
    // Passing the day in is what makes this assertable at all — a helper that
    // read `new Date()` would be untestable and would silently change meaning
    // when the suite runs at a month boundary.
    expect(isCurrent(RULES[0], '2026-07-29')).toBe(true);
    expect(isCurrent(RULES[0], '2026-02-01')).toBe(false);
    expect(isCurrent(RULES[1], '2026-07-29')).toBe(false);
    expect(isCurrent(RULES[1], '2026-02-01')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Supersession copy
// ---------------------------------------------------------------------------
describe('describeSupersession', () => {
  it('states which row closes, on which day, and that history survives', () => {
    const plan = describeSupersession(RULES[0], '2026-08-01');
    expect(plan.valid).toBe(true);
    expect(plan.closesOn).toBe('2026-07-31');
    expect(plan.recomputeFrom).toBe('2026-08-01');
    expect(plan.message).toContain('#4');
    expect(plan.message).toContain('keeps its value');
    expect(plan.message).toContain('still resolve the old rate');
  });

  it('refuses a start date on or before the rule it supersedes', () => {
    const plan = describeSupersession(RULES[0], '2026-03-01');
    expect(plan.valid).toBe(false);
    expect(plan.message).toContain('must start after');
  });

  it('is null without a rule or a usable date', () => {
    expect(describeSupersession(null, '2026-08-01')).toBeNull();
    expect(describeSupersession(RULES[0], '')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------
describe('validateRuleForm', () => {
  const good = {
    ...EMPTY_RULE_FORM,
    cost_type: 'handling',
    value: '10',
    effective_from: '2026-09-01',
  };

  it('accepts a well-formed new rule', () => {
    expect(hasErrors(validateRuleForm(good, { rules: RULES }))).toBe(false);
  });

  it('requires a rate and rejects a negative one', () => {
    expect(validateRuleForm({ ...good, value: '' }, {}).value).toBeTruthy();
    expect(validateRuleForm({ ...good, value: '-1' }, {}).value).toBeTruthy();
  });

  it('catches the pct unit mix-up', () => {
    // 2.5% is 2.5, not 0.025 and not 250. A three-digit percentage is the
    // shape of a rupee amount typed into a percentage field, which is a 100x
    // error in the margin and produces no error anywhere else.
    expect(validateRuleForm({ ...good, unit: 'pct', value: '250' }, {}).value).toBeTruthy();
    expect(validateRuleForm({ ...good, unit: 'pct', value: '2.5' }, {}).value).toBeUndefined();
  });

  it('rejects an inverted window', () => {
    const errors = validateRuleForm(
      { ...good, effective_from: '2026-09-30', effective_to: '2026-09-01' },
      {},
    );
    expect(errors.effective_to).toBeTruthy();
  });

  it('rejects a scoped rule with no scope value', () => {
    // A scoped rule carrying the '-' sentinel is a global rule wearing a scope
    // and would never match the bucket it was meant for.
    const errors = validateRuleForm({ ...good, scope: 'courier', scope_value: '-' }, {});
    expect(errors.scope_value).toBeTruthy();
  });

  it('previews the server 409 for an overlapping window', () => {
    const errors = validateRuleForm(
      { ...good, cost_type: 'packaging', scope: 'global', scope_value: '-', effective_from: '2026-06-01' },
      { rules: RULES },
    );
    expect(errors.effective_from).toContain('#4');
    expect(errors.effective_from).toContain('supersede');
  });

  it('in supersede mode, requires a date after the superseded rule started', () => {
    const errors = validateRuleForm(
      { ...good, effective_from: '2026-02-01' },
      { mode: 'supersede', rule: RULES[0] },
    );
    expect(errors.effective_from).toContain('Must be after');
  });

  it('in supersede mode, refuses a rule that has already ended', () => {
    const errors = validateRuleForm(
      { ...good, effective_from: '2026-06-01' },
      { mode: 'supersede', rule: RULES[1] },
    );
    expect(errors.effective_from).toContain('already ended');
  });
});

describe('validateSpendForm', () => {
  const good = { ...EMPTY_SPEND_FORM, period: '2026-06-12', channel: 'meta', amount: '40000' };

  it('accepts a well-formed entry', () => {
    expect(hasErrors(validateSpendForm(good))).toBe(false);
  });

  it('treats a blank amount as missing, not as zero', () => {
    // Zero spend recorded as fact makes acquisition look free — the one
    // direction of error nobody investigates.
    expect(validateSpendForm({ ...good, amount: '' }).amount).toBeTruthy();
    expect(validateSpendForm({ ...good, amount: '0' }).amount).toBeUndefined();
  });

  it('requires a channel and a real date', () => {
    expect(validateSpendForm({ ...good, channel: '  ' }).channel).toBeTruthy();
    expect(validateSpendForm({ ...good, period: '2026-02-30' }).period).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Payloads
// ---------------------------------------------------------------------------
describe('payload builders', () => {
  it('sends an empty end date as null, not as an empty string', () => {
    const payload = buildRulePayload({
      ...EMPTY_RULE_FORM,
      cost_type: 'packaging',
      value: '8',
      effective_from: '2026-09-01',
      effective_to: '',
    });
    expect(payload.effective_to).toBeNull();
    expect(payload.value).toBe('8');
    expect(payload.scope_value).toBe('-');
  });

  it('forces the global sentinel rather than trusting a stale scope value', () => {
    const payload = buildRulePayload({
      ...EMPTY_RULE_FORM,
      scope: 'global',
      scope_value: 'bluedart',
      value: '8',
      effective_from: '2026-09-01',
    });
    expect(payload.scope_value).toBe('-');
  });

  it('omits the identity fields when superseding', () => {
    // A supersession is the same rate line continuing. Letting the caller move
    // it to another scope would orphan the old row's coverage.
    const payload = buildRulePayload(
      { ...EMPTY_RULE_FORM, value: '12', effective_from: '2026-09-01' },
      { mode: 'supersede' },
    );
    expect(payload).not.toHaveProperty('cost_type');
    expect(payload).not.toHaveProperty('scope');
    expect(payload).not.toHaveProperty('scope_value');
  });

  it('defaults blank spend dimensions to the NOT NULL sentinel', () => {
    const payload = buildSpendPayload({
      ...EMPTY_SPEND_FORM,
      period: '2026-06-12',
      channel: ' meta ',
      campaign: '  ',
      amount: '40000',
    });
    expect(payload.channel).toBe('meta');
    expect(payload.campaign).toBe('-');
    expect(payload.amount).toBe('40000');
  });
});

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------
describe('badgeQuality', () => {
  it('maps the cost vocabulary onto the metric-quality vocabulary', () => {
    // Two different vocabularies on purpose: one describes a source document,
    // the other describes a number. An unmapped value renders as INCOMPLETE,
    // so a gap here would paint every rate as "inputs are missing".
    expect(badgeQuality('actual')).toBe('ACTUAL');
    expect(badgeQuality('contracted')).toBe('ACTUAL');
    expect(badgeQuality('estimated')).toBe('ESTIMATED');
    expect(badgeQuality('assumed')).toBe('ESTIMATED');
    expect(badgeQuality('nonsense')).toBe('INCOMPLETE');
    expect(badgeQuality(undefined)).toBe('INCOMPLETE');
  });
});

describe('costAdminError', () => {
  it('prefers the API envelope, then the JS message, then the fallback', () => {
    expect(
      costAdminError({ response: { data: { error: { message: 'Rule #4 already covers…' } } } }),
    ).toBe('Rule #4 already covers…');
    expect(costAdminError(new Error('Network Error'))).toBe('Network Error');
    expect(costAdminError({}, 'Could not save.')).toBe('Could not save.');
  });
});
