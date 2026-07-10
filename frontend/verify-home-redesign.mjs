/**
 * Verify the Home / Wishlist / Contact / Footer-newsletter redesign.
 * Run: node verify-home-redesign.mjs   (dev server on :5175, real API proxied)
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = process.env.BASE || 'http://localhost:5175';
const SHOTS = 'verify-shots';
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
let failures = 0;

function check(label, ok, extra = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? `  (${extra})` : ''}`);
  if (!ok) failures++;
}

async function settle(page) {
  await page.evaluate(async () => {
    await new Promise((r) => {
      let y = 0;
      const step = () => {
        window.scrollTo(0, y);
        y += window.innerHeight;
        if (y < document.body.scrollHeight) setTimeout(step, 60);
        else setTimeout(r, 200);
      };
      step();
    });
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
}

try {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: 'reduce',
  });
  // The first-visit pincode modal (Layout-owned) opens 1.2s after load and
  // blocks pointer events — pre-seed its dismissal flag instead.
  await ctx.addInitScript(() => {
    try {
      sessionStorage.setItem('wellvia.pincode_modal_dismissed', '1');
    } catch {
      /* best-effort */
    }
  });

  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  async function dismissPincode() {} // no-op: dismissal flag pre-seeded above

  // ── 1. Home ────────────────────────────────────────────────────────────
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  await dismissPincode();
  await settle(page);

  const body = await page.textContent('body');
  check('home: hero headline', body.includes('Where daily wellness'));
  check('home: trust chip', body.includes('FSSAI'));
  check('home: why-we-exist section', body.includes('It started with one belief.'));
  check('home: product rail heading', body.includes('Your body works hard.'));
  check('home: rail has Add to Cart', body.includes('Add to Cart'));
  check('home: new launches', body.includes('New Launches') && body.includes('The Wellness Gummy'));
  check('home: bundle section hidden (no combos)', !body.includes('Build Your Daily Routine'));
  check('home: blog section', body.includes('WITHOUT') && body.includes('Why Sleep Is Your Superpower'));
  check('home: reviews section', body.includes('The Reviews Behind the Routine') && body.includes('Priya S.'));
  check('home: philosophy', body.includes('the piece that brings') && body.includes('together with what?'));
  check('home: footer columns', body.includes('Customer Care') && body.includes('Subscribe to Our Newsletter'));
  check('home: copyright', /© \d{4} Wellvia\. All rights reserved\./.test(body));

  // "Why Wellvia" anchor scroll
  await page.click('text=Why Wellvia');
  await page.waitForTimeout(700);
  const scrolled = await page.evaluate(() => window.scrollY > 100);
  check('home: Why Wellvia scrolls to why-we-exist', scrolled);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);

  await page.screenshot({ path: `${SHOTS}/home.png`, fullPage: true });

  // ── 2. Footer newsletter subscribe ─────────────────────────────────────
  const nlInput = page.locator('footer input[type="email"]');
  await nlInput.scrollIntoViewIfNeeded();
  await nlInput.fill(`verify+${Date.now()}@example.com`);
  await page.locator('footer button', { hasText: 'Subscribe' }).click();
  await page.waitForTimeout(1500);
  const nlStatus = await page.locator('footer [role="status"]').textContent();
  check('footer: newsletter success', (nlStatus || '').includes('Subscribed!'), nlStatus?.trim());
  await page.screenshot({ path: `${SHOTS}/footer-newsletter.png` });

  // ── 3. Wishlist — signed out ───────────────────────────────────────────
  await page.goto(BASE + '/wishlist', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  await dismissPincode();
  const wlBody = await page.textContent('body');
  check('wishlist: heading', wlBody.includes('My Wishlist'));
  check('wishlist: signed-out prompt', wlBody.includes('Sign in to view your wishlist'));
  await page.screenshot({ path: `${SHOTS}/wishlist.png`, fullPage: true });

  // ── 4. Contact — form submits to real endpoint ─────────────────────────
  await page.goto(BASE + '/contact', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  await dismissPincode();
  const cBody = await page.textContent('body');
  check('contact: hero', cBody.includes('Contact Us') && cBody.includes("We're here to help!"));
  check('contact: info card', cBody.includes('Feel free to reach out to us at any time.') && cBody.includes('Mail id'));
  check('contact: support hours', cBody.includes('Monday – Saturday (9:00 AM – 6:00 PM IST)'));

  await page.fill('form input[autocomplete="name"]', 'Verify Bot');
  await page.fill('form input[type="email"]', `verify+${Date.now()}@example.com`);
  await page.fill('form textarea', 'This is an automated verification message for the contact form.');
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/contact') && r.request().method() === 'POST', { timeout: 10000 }).catch(() => null),
    page.click('form button[type="submit"]'),
  ]);
  await page.waitForTimeout(800);
  check('contact: POST /contact fired', !!resp, resp ? `status ${resp.status()}` : 'no request');
  if (resp) check('contact: POST returned 201', resp.status() === 201, `status ${resp.status()}`);
  const successVisible = await page.locator('form [role="status"]').count();
  check('contact: success message shown', successVisible > 0);
  await page.screenshot({ path: `${SHOTS}/contact.png`, fullPage: true });

  check('no page errors across pages', errors.length === 0, errors.slice(0, 2).join(' | '));

  await ctx.close();
} finally {
  await browser.close();
}

console.log(failures === 0 ? '\nALL CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
