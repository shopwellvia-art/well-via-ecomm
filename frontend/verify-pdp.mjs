/**
 * PDP redesign smoke check — run with: node verify-pdp.mjs [productId] [--rich]
 *
 * Opens /products/:id on the local dev server, asserts the title renders,
 * the Add to cart button is visible, the Description accordion toggles,
 * and saves a full-page screenshot to verify-shots/pdp.png.
 *
 * --rich intercepts the product + settings responses client-side and injects
 * the PDP content fields (flavour, offer, coupon, benefits, highlights,
 * ingredients, usage_steps, faqs, rating) so the full mockup layout can be
 * verified without touching the shared backend. Screenshot:
 * verify-shots/pdp-rich.png.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = 'http://localhost:5175';
const args = process.argv.slice(2);
const rich = args.includes('--rich');
const productId = args.find((a) => !a.startsWith('--')) || '13';

const RICH_FIELDS = {
  flavour: 'Black Currant',
  offer_text: 'Buy 1 Get 1 free!',
  coupon_code: 'WELLVIA',
  coupon_hint: 'Get it for ₹800',
  highlights: ['30 Gummies', '15 Servings', 'Berry Flavour'],
  benefits: [
    { icon: 'sleep', title: 'Fall Asleep Faster', text: 'Helps you relax and drift off naturally.' },
    { icon: 'clock', title: 'Stay Asleep Longer', text: 'Supports deep, uninterrupted sleep.' },
    { icon: 'calm', title: 'Feel Calm & Relaxed', text: 'Reduces stress and quiets your mind.' },
    { icon: 'refresh', title: 'Wake Up Refreshed', text: 'No grogginess, just natural energy.' },
  ],
  ingredients: [
    'Melatonin — Regulates sleep cycle and improves quality',
    'L-Theanine — Promotes relaxation without drowsiness',
    'Vitamin B6 — Supports mood balance and recovery',
  ].join('\n'),
  usage_steps: [
    { label: '30 Mins Before Bed', text: 'Take 2 Sleep Gummies' },
    { label: 'Relax & Unwind', text: 'Feel calm, relaxed & stress-free' },
    { label: 'Fall Asleep Faster', text: 'Drift off naturally and comfortably' },
    { label: 'Wake Refreshed', text: 'Feel rejuvenated and ready for the day' },
  ],
  faqs: [
    { q: 'Is it habit forming?', a: 'No — the formula is non-habit forming.' },
    { q: 'When will I see results?', a: 'Most customers feel the difference within a week.' },
  ],
  rating_avg: '4.60',
  rating_count: 100,
  rating_distribution: { 1: 2, 2: 3, 3: 10, 4: 25, 5: 60 },
};

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
}

mkdirSync('verify-shots', { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
const page = await context.newPage();

if (rich) {
  await page.route(`**/api/v1/products/${productId}`, async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    Object.assign(json, RICH_FIELDS);
    await route.fulfill({ response: res, json });
  });
  await page.route('**/api/v1/settings/public', async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    json['shipping.free_threshold'] = '500';
    await route.fulfill({ response: res, json });
  });
}

/** Scroll through the page so whileInView reveals fire, then back to top. */
async function settleReveals() {
  await page.evaluate(async () => {
    const step = 700;
    for (let y = 0; y < document.body.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 120));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(700);
}

try {
  await page.goto(`${BASE}/products/${productId}`, { waitUntil: 'networkidle' });

  // 1. Title renders
  const title = page.locator('h1').first();
  await title.waitFor({ state: 'visible', timeout: 15000 });
  const titleText = (await title.textContent())?.trim();
  check('title renders', Boolean(titleText), `"${titleText}"`);

  // 2. Add to cart button visible
  const addBtn = page.getByRole('button', { name: /add to cart/i }).first();
  check('Add to cart visible', await addBtn.isVisible());

  // 3. Description accordion toggles (description is set on the seed product)
  const acc = page.getByRole('button', { name: 'Description' });
  if ((await acc.count()) === 0) {
    check('Description accordion exists', false);
  } else {
    check('Description accordion exists', true);
    const before = await acc.getAttribute('aria-expanded');
    await acc.click();
    const afterOpen = await acc.getAttribute('aria-expanded');
    const panelId = await acc.getAttribute('aria-controls');
    const panelVisible = await page.locator(`[id="${panelId}"]`).isVisible();
    await acc.click();
    const afterClose = await acc.getAttribute('aria-expanded');
    check(
      'Description accordion toggles',
      before === 'false' && afterOpen === 'true' && panelVisible && afterClose === 'false',
      `expanded ${before} -> ${afterOpen} -> ${afterClose}, panel visible while open: ${panelVisible}`,
    );
    await acc.click(); // leave open for the screenshot
  }

  if (rich) {
    // 4. Content sections render from the injected fields
    check('flavour chip', await page.getByText('Black Currant').first().isVisible());
    check('offer chip', await page.getByText('Buy 1 Get 1 free!').first().isVisible());
    check('rating anchor', await page.locator('a[href="#reviews"]').first().isVisible());
    check('benefits section', await page.getByRole('heading', { name: /Why You.ll Love It/ }).isVisible());
    check('pack chips', await page.locator('[aria-label="Pack highlights"]').isVisible());
    check('ingredients section', await page.getByRole('heading', { name: 'Clean & Effective Ingredients' }).isVisible());
    check('expect timeline', await page.getByRole('heading', { name: 'What to expect' }).isVisible());
    check('How to use accordion', (await page.getByRole('button', { name: 'How to use' }).count()) === 1);
    check('FAQ accordion', (await page.getByRole('button', { name: 'FAQ' }).count()) === 1);
    check('free shipping row', await page.getByText(/Free Shipping on orders above/).isVisible());

    // 5. Coupon copy → clipboard + "Copied!" feedback
    const copyBtn = page.getByRole('button', { name: /Copy coupon code/ });
    await copyBtn.click();
    let copiedShown = true;
    try {
      // The click handler is async (clipboard write) — wait for the state flip.
      await page.getByText('Copied!').waitFor({ state: 'visible', timeout: 2000 });
    } catch {
      copiedShown = false;
    }
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    check('coupon copy works', copiedShown && clip === 'WELLVIA', `clipboard="${clip}"`);
  }

  // 6. Full-page screenshot (after scrolling so reveal animations fire)
  await settleReveals();
  const shot = rich ? 'verify-shots/pdp-rich.png' : 'verify-shots/pdp.png';
  await page.screenshot({ path: shot, fullPage: true });
  check('screenshot saved', true, shot);
} catch (err) {
  check('script completed', false, err.message);
} finally {
  await browser.close();
}

if (results.some((r) => !r.ok)) process.exit(1);
