/**
 * Command-menu matching over all 73 analytics views. Pure, no React.
 *
 * 73 views is past the point where a person can find one by reading a sidebar,
 * so typing has to be the primary way in. The ranking is deliberately boring
 * and explainable — exact beats prefix beats substring beats keyword — because
 * a command menu that reorders itself on fuzzy-match scores trains people to
 * stop trusting the first result.
 *
 * Everything searched comes from the generated contract (`name`, `slug`,
 * `keywords`, `summary`, and the owning module's name). Nothing is restated
 * here, so a view renamed in Python becomes findable by its new name as soon
 * as the contract is regenerated.
 */
import { allViews } from './registry.js';

/** Score floors, highest first. Exported so the ranking is assertable. */
export const SCORE = Object.freeze({
  NAME_EXACT: 100,
  SLUG_EXACT: 95,
  NAME_PREFIX: 85,
  NAME_WORD_PREFIX: 75,
  SLUG_PREFIX: 72,
  KEYWORD_EXACT: 70,
  KEYWORD_PREFIX: 65,
  NAME_SUBSTRING: 55,
  MODULE_EXACT: 50,
  SLUG_SUBSTRING: 45,
  MODULE_PREFIX: 40,
  MODULE_SUBSTRING: 35,
  SUMMARY_SUBSTRING: 20,
});

const normalise = (text) => String(text ?? '').toLowerCase().trim();

/** Split a label into words so "cohort" matches "…and Cohort Retention". */
const words = (text) => normalise(text).split(/[^a-z0-9]+/).filter(Boolean);

function wordPrefix(text, token) {
  return words(text).some((w) => w.startsWith(token));
}

/**
 * Best score for one token against one view, plus which field earned it.
 *
 * Returns `null` when the token does not appear anywhere — callers use that to
 * require every token to match, so "cohort revenue" does not surface a view
 * that only knows about revenue.
 */
function scoreToken(view, token) {
  const name = normalise(view.name);
  const slug = normalise(view.slug);
  const moduleName = normalise(view.moduleName);
  const summary = normalise(view.summary);
  const keywords = (view.keywords ?? []).map(normalise);

  if (name === token) return { score: SCORE.NAME_EXACT, field: 'name' };
  if (slug === token) return { score: SCORE.SLUG_EXACT, field: 'slug' };
  if (name.startsWith(token)) return { score: SCORE.NAME_PREFIX, field: 'name' };
  if (wordPrefix(name, token)) return { score: SCORE.NAME_WORD_PREFIX, field: 'name' };
  if (slug.startsWith(token)) return { score: SCORE.SLUG_PREFIX, field: 'slug' };
  if (keywords.includes(token)) return { score: SCORE.KEYWORD_EXACT, field: 'keyword' };
  if (keywords.some((k) => k.startsWith(token))) return { score: SCORE.KEYWORD_PREFIX, field: 'keyword' };
  if (name.includes(token)) return { score: SCORE.NAME_SUBSTRING, field: 'name' };
  if (moduleName === token) return { score: SCORE.MODULE_EXACT, field: 'module' };
  if (slug.includes(token)) return { score: SCORE.SLUG_SUBSTRING, field: 'slug' };
  if (moduleName.startsWith(token)) return { score: SCORE.MODULE_PREFIX, field: 'module' };
  if (wordPrefix(moduleName, token)) return { score: SCORE.MODULE_SUBSTRING, field: 'module' };
  if (summary.includes(token)) return { score: SCORE.SUMMARY_SUBSTRING, field: 'summary' };
  return null;
}

/**
 * Rank the views against a query.
 *
 * @param  query   free text; whitespace-separated tokens are ANDed.
 * @param  options `{ limit = 10, views = allViews() }`
 * @return `[{ view, score, matchedOn }]`, best first. `[]` when nothing
 *         matches — an empty command menu is a correct answer, and better than
 *         a list of near-misses the operator has to read to reject.
 */
export function searchViews(query, { limit = 10, views = allViews() } = {}) {
  const tokens = normalise(query).split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];

  const results = [];
  for (const view of views) {
    const hits = tokens.map((token) => scoreToken(view, token));
    if (hits.some((hit) => hit === null)) continue; // every token must land
    const total = hits.reduce((sum, hit) => sum + hit.score, 0);
    results.push({
      view,
      score: Math.round(total / hits.length),
      matchedOn: [...new Set(hits.map((hit) => hit.field))],
    });
  }

  // Registry order breaks ties, so equal-scoring results stay in the order the
  // backend numbers them rather than shuffling between keystrokes.
  results.sort((a, b) => b.score - a.score || a.view.number - b.view.number);
  return results.slice(0, limit);
}
