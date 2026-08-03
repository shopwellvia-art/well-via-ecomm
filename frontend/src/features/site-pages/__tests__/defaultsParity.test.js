/**
 * Frontend half of the site-page defaults parity guard.
 *
 * SITE_PAGES_DEFAULTS here and DEFAULT_SITE_PAGES in
 * backend/app/schemas/site_pages.py carry the same customer-visible default
 * copy, and the API value wins this side's merge — drift means the live page
 * shows stale or blanked content with no error anywhere. The shared keys are
 * pinned in backend/tests/fixtures/site_pages_shared_defaults.json;
 * test_site_pages_defaults_parity.py holds the backend to the same file.
 * Editing a default means changing all three copies in one commit.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, it, expect } from 'vitest';
import { SITE_PAGES_DEFAULTS } from '../defaults.js';

const FIXTURE = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../../backend/tests/fixtures/site_pages_shared_defaults.json',
);

function sharedSubset() {
  const data = JSON.parse(readFileSync(FIXTURE, 'utf8'));
  delete data._comment;
  return data;
}

describe('site-page defaults parity with backend', () => {
  it('matches every shared key pinned in the backend fixture', () => {
    const shared = sharedSubset();
    for (const [pageKey, expected] of Object.entries(shared)) {
      for (const [field, value] of Object.entries(expected)) {
        expect(
          SITE_PAGES_DEFAULTS[pageKey][field],
          `SITE_PAGES_DEFAULTS.${pageKey}.${field} drifted from ` +
            'backend/tests/fixtures/site_pages_shared_defaults.json — ' +
            'update backend, frontend and fixture in one commit',
        ).toEqual(value);
      }
    }
  });
});
