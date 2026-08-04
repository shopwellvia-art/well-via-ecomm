// Regression: ISSUE-003 — homepage daily-routine images 404'd in production.
// Found by /qa on 2026-08-04
// Report: .gstack/qa-reports/qa-report-localhost-5199-2026-08-04.md
//
// Kavya's redesign (7c9c5168) moved public/home/routine-*.jpg to
// public/routine-*.png but HomePage.jsx kept the old src, so both bundle
// cards rendered broken-image icons. This test extracts every static
// /public asset path referenced by HomePage.jsx (including the
// `/routine-${r.key}.png` template, expanded with the ROUTINES keys) and
// asserts the file actually exists, so a future asset move or rename fails
// CI instead of shipping a broken homepage.
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, '../../..');
const publicDir = resolve(frontendRoot, 'public');
const source = readFileSync(resolve(here, '../HomePage.jsx'), 'utf8');

function referencedAssetPaths() {
  const paths = new Set();

  // Plain literals: src="/foo.png" or src={'/foo.png'} — root-relative only.
  for (const m of source.matchAll(/src=\{?["'`](\/[^"'`${}]+)["'`]\}?/g)) {
    paths.add(m[1]);
  }

  // Template literals with ${r.key}: expand against the ROUTINES keys
  // declared in the same file, since that's the only interpolation used.
  const keys = [...source.matchAll(/key:\s*['"](\w+)['"]/g)].map((m) => m[1]);
  for (const m of source.matchAll(/src=\{`(\/[^`]*\$\{r\.key\}[^`]*)`\}/g)) {
    for (const key of keys) {
      paths.add(m[1].replace('${r.key}', key));
    }
  }

  return [...paths];
}

describe('HomePage static asset references', () => {
  const paths = referencedAssetPaths();

  it('extracts the routine bundle paths (guard that the regex keeps working)', () => {
    expect(paths).toContain('/routine-morning.png');
    expect(paths).toContain('/routine-night.png');
  });

  it.each(paths)('%s exists in frontend/public', (assetPath) => {
    expect(existsSync(resolve(publicDir, `.${assetPath}`))).toBe(true);
  });
});
