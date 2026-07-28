/**
 * The banner's decision logic — every rule about *whether to ask* and *what a
 * click means*, with no React and no DOM in sight.
 *
 * It lives apart from the JSX for one reason: these are the rules a regulator
 * would read, and rules that can only be checked by rendering a component are
 * rules nobody checks. Everything here is pure and covered by the node suite in
 * `features/tracking/__tests__/consentBanner.test.js`.
 *
 * Storage, the policy version and the timestamp are `features/tracking/consent.js`'s
 * job. Nothing in this file writes anything; it only decides.
 */
import { isAdminRoute } from '@/features/tracking/config.js';
import { normalizeConsent } from '@/features/tracking/consent.js';

/**
 * The four categories as the customer sees them.
 *
 * The descriptions are deliberately plain: "helps us improve the site" is not a
 * description, it is a euphemism. Each one says what is collected and who ends
 * up with it, because consent to a thing you were not told about is not consent.
 */
export const CONSENT_UI_CATEGORIES = Object.freeze([
  Object.freeze({
    id: 'necessary',
    label: 'Strictly necessary',
    locked: true,
    description:
      'Keeps you signed in, remembers your cart and protects checkout against fraud. ' +
      'The site cannot work without these, so they cannot be switched off — and they ' +
      'are never used to build a profile of you.',
  }),
  Object.freeze({
    id: 'analytics',
    label: 'Analytics',
    locked: false,
    description:
      'Google Analytics and Microsoft Clarity: which pages you visited, what you ' +
      'clicked and how far you scrolled, and — where session replay is switched ' +
      'on — a recording of your visit. Shared with Google and Microsoft, who ' +
      'process it on their own servers.',
  }),
  Object.freeze({
    id: 'marketing',
    label: 'Marketing',
    locked: false,
    description:
      'Lets advertising platforms know that a visit or a purchase came from an ad ' +
      'they showed you, and lets them show you ads elsewhere based on what you ' +
      'looked at here.',
  }),
  Object.freeze({
    id: 'personalization',
    label: 'Personalization',
    locked: false,
    description:
      'Lets recommendations and “recently viewed” be built from your own browsing ' +
      'rather than from what is popular overall. With it off, the tags on the page ' +
      'are told not to build a profile from your visits.',
  }),
]);

/** The three that a customer can actually decide about. */
export const OPTIONAL_CATEGORY_IDS = Object.freeze(
  CONSENT_UI_CATEGORIES.filter((category) => !category.locked).map((category) => category.id)
);

/** True for the one category that has no switch, because it has no choice. */
export function isCategoryLocked(id) {
  return CONSENT_UI_CATEGORIES.some((category) => category.id === id && category.locked);
}

/**
 * Should the banner be on screen?
 *
 * Four rules, in order of how badly getting them wrong would hurt:
 *
 * 1. **Never on `/admin`.** Staff are not the data subject here — they are the
 *    people who set the tracking up. Asking them for the customer's consent
 *    records the wrong person's decision against the customer's browser.
 * 2. **`reopened` wins over everything else** (except admin). This is the
 *    footer link: a decision that cannot be revisited is not revocable, and
 *    consent that cannot be withdrawn is not consent.
 * 3. **A recorded decision is final.** Re-asking someone who already said no is
 *    not a second chance to inform them, it is pestering them until they give
 *    in, and the answer it eventually extracts is not freely given.
 * 4. **Dismissed for this session** hides it without recording anything. The
 *    customer walked away from the question, so nothing is granted — and
 *    because no decision was recorded, rule 3 does not apply next visit.
 */
export function shouldShowBanner({
  pathname,
  consent,
  reopened = false,
  dismissedForSession = false,
} = {}) {
  if (isAdminRoute(pathname)) return false;
  if (reopened) return true;
  if (normalizeConsent(consent).decided) return false;
  return !dismissedForSession;
}

/** Grant everything. The exact counterpart of `rejectAllPatch`. */
export function acceptAllPatch() {
  return Object.fromEntries(OPTIONAL_CATEGORY_IDS.map((id) => [id, true]));
}

/**
 * Deny everything optional. Also the withdrawal path: applied after a grant it
 * revokes it, and because it is a decision it persists rather than dropping the
 * customer back into "undecided" and re-prompting them.
 */
export function rejectAllPatch() {
  return Object.fromEntries(OPTIONAL_CATEGORY_IDS.map((id) => [id, false]));
}

/**
 * The switch positions for a consent record.
 *
 * Nothing is pre-ticked beyond what the customer already agreed to: an opt-out
 * checkbox that starts ticked collects a decision the customer never made.
 */
export function draftFromConsent(consent) {
  const { categories } = normalizeConsent(consent);
  const draft = { necessary: true };
  for (const id of OPTIONAL_CATEGORY_IDS) draft[id] = categories[id] === true;
  return draft;
}

/**
 * Flip one switch. Pure — returns a new draft.
 *
 * `necessary` is pinned on: the UI never renders a switch for it, and if some
 * future refactor does, it still cannot turn it off here.
 */
export function setDraftCategory(draft, id, value) {
  const next = { ...draft, necessary: true };
  if (id === 'necessary') return next;
  if (!OPTIONAL_CATEGORY_IDS.includes(id)) {
    throw new Error(
      `unknown consent category "${id}" — the switchable categories are ` +
        `${OPTIONAL_CATEGORY_IDS.join(', ')}`
    );
  }
  next[id] = value === true;
  return next;
}

/** The patch for `setConsent`, carrying only the three switchable categories. */
export function patchFromDraft(draft) {
  return Object.fromEntries(OPTIONAL_CATEGORY_IDS.map((id) => [id, draft?.[id] === true]));
}
