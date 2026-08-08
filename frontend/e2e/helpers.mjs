/**
 * Shared helpers for the e2e regression suite.
 *
 * Zero new dependencies: node's built-in test runner (`node --test e2e/`)
 * drives specs, and the already-installed `playwright` library provides the
 * browser. Specs assert against live API data fetched at test start — never
 * hardcoded product names.
 *
 * BASE_URL env selects the target stack (default: the running nginx build on
 * http://localhost:8090). Read-only usage only — specs must never place
 * orders or register users.
 */
import { chromium } from 'playwright';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

export const BASE_URL = process.env.BASE_URL || 'http://localhost:8090';

/**
 * Console noise that is expected for signed-out visitors and must not fail a
 * spec: the browser's own network-layer log lines for 4xx responses (e.g. a
 * stale-session 401). Real page errors (uncaught exceptions) always fail.
 */
const BENIGN_CONSOLE = [/Failed to load resource/i, /net::ERR_/i];

/**
 * Launch headless chromium. If the exact browser revision pinned by the
 * installed playwright package hasn't been downloaded, fall back to the
 * newest chromium already in the local ms-playwright cache instead of
 * demanding a fresh download (zero-new-downloads policy).
 */
export async function launchBrowser() {
  try {
    return await chromium.launch();
  } catch (err) {
    if (!/Executable doesn't exist/i.test(String(err?.message))) throw err;
    const executablePath = findCachedChromium();
    if (!executablePath) throw err;
    return chromium.launch({ executablePath });
  }
}

/** Newest cached chromium executable (headless shell preferred), or null. */
function findCachedChromium() {
  const roots = [
    path.join(homedir(), 'Library/Caches/ms-playwright'), // macOS
    path.join(homedir(), '.cache/ms-playwright'), // Linux
  ];
  const relCandidates = [
    'chrome-headless-shell-mac-x64/chrome-headless-shell',
    'chrome-headless-shell-mac-arm64/chrome-headless-shell',
    'chrome-headless-shell-linux64/chrome-headless-shell',
    'chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
    'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
    'chrome-mac/Chromium.app/Contents/MacOS/Chromium',
    'chrome-linux/chrome',
  ];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    const entries = readdirSync(root)
      .map((name) => /^chromium(_headless_shell)?-(\d+)$/.exec(name))
      .filter(Boolean)
      // Newest revision first; headless shell before full chromium at a tie.
      .sort(
        (a, b) =>
          Number(b[2]) - Number(a[2]) || (a[1] ? -1 : 1),
      );
    for (const m of entries) {
      for (const rel of relCandidates) {
        const candidate = path.join(root, m[0], rel);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return null;
}

/**
 * New context + page with:
 *  - desktop viewport / reduced motion,
 *  - the first-visit pincode modal pre-dismissed (it opens ~1.2s after first
 *    mount and intercepts clicks; pre-seeding its sessionStorage flag before
 *    any page script runs keeps it closed),
 *  - a console-error collector: `errors` accumulates uncaught page errors and
 *    unexpected console.error output; assert with expectNoPageErrors().
 */
export async function newPage(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: 'reduce',
  });
  await context.addInitScript(() => {
    try {
      sessionStorage.setItem('wellvia.pincode_modal_dismissed', '1');
    } catch {
      /* best-effort */
    }
  });
  const page = await context.newPage();
  const errors = collectPageErrors(page);
  return { context, page, errors };
}

/** Attach the collector to a page; returns the (mutating) error list. */
export function collectPageErrors(page) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e?.message ?? e}`));
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    if (BENIGN_CONSOLE.some((re) => re.test(text))) return;
    errors.push(`console.error: ${text}`);
  });
  return errors;
}

/** Fail the current test if any unexpected page error was collected. */
export function expectNoPageErrors(errors, label = '') {
  if (errors.length > 0) {
    throw new Error(
      `Unexpected page errors${label ? ` (${label})` : ''}:\n  ${errors.join('\n  ')}`,
    );
  }
}

/** Navigate and settle (networkidle also lets React Query requests finish). */
export async function goto(page, path = '/') {
  const resp = await page.goto(BASE_URL + path, { waitUntil: 'networkidle' });
  return resp;
}

/** GET a backend JSON endpoint directly (live data source for assertions). */
export async function fetchJson(path) {
  const res = await fetch(BASE_URL + path, {
    headers: { accept: 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`GET ${path} -> HTTP ${res.status}`);
  }
  return res.json();
}

/** Live product list — `{ items, total, page, page_size }`. */
export function fetchProducts(query = 'page=1&page_size=12') {
  return fetchJson(`/api/v1/products?${query}`);
}

/**
 * Mirror of the storefront's formatPrice (src/lib/utils.js): en-IN INR with
 * two decimals. Compare via normalizeMoney() — ICU builds differ in the
 * whitespace they emit around the currency symbol.
 */
export function formatINR(value) {
  const n = Number(value);
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
}

/**
 * Strip every kind of whitespace (\s covers NBSP / narrow-NBSP) so price
 * strings compare reliably across ICU builds.
 */
export function normalizeMoney(s) {
  return String(s).replace(/\s+/g, '');
}
