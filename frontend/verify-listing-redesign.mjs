import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:5175';
const SHOTS = 'verify-shots';
mkdirSync(SHOTS, { recursive: true });

const results = [];
function log(step, ok, detail = '') {
  results.push({ ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${step}  ::  ${detail}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

// ── /products: hero + sidebar + grid ──
await page.goto(`${BASE}/products`, { waitUntil: 'networkidle' });
log('products hero', await page.getByText('Wellness, Your Way.').isVisible());
log('sidebar goal section', await page.getByRole('heading', { name: 'Goal' }).isVisible());
log('sidebar flavour section', await page.getByRole('heading', { name: 'Flavour' }).isVisible());
log('showing count', await page.getByText(/Showing \d+ of \d+/).isVisible());
await page.screenshot({ path: `${SHOTS}/listing-products.png`, fullPage: true });

// ── filter via URL: flavours param + in_stock ──
await page.goto(`${BASE}/products?flavours=Grape&in_stock=1&sort_by=price_asc`, {
  waitUntil: 'networkidle',
});
log('flavour chip visible', await page.getByRole('button', { name: /Grape/ }).isVisible());
log('in-stock chip visible', await page.getByRole('button', { name: /In Stock/ }).isVisible());
const sortVal = await page.locator('select').inputValue();
log('sort from URL', sortVal === 'price_asc', `sort=${sortVal}`);

// ── clicking a flavour checkbox updates URL ──
await page.goto(`${BASE}/products`, { waitUntil: 'networkidle' });
await page.getByLabel('Orange', { exact: true }).check();
await page.waitForTimeout(400);
log('checkbox → URL', page.url().includes('flavours=Orange'), page.url());

// ── /bestsellers ──
await page.goto(`${BASE}/bestsellers`, { waitUntil: 'networkidle' });
log('bestsellers hero', await page.getByText('Customer Favorites, For a Reason.').isVisible());
const bsBadges = await page.getByText('Bestseller', { exact: true }).count();
log('bestseller badges', bsBadges > 0, `${bsBadges} badges`);
await page.screenshot({ path: `${SHOTS}/listing-bestsellers.png`, fullPage: true });

// ── /new-arrivals ──
await page.goto(`${BASE}/new-arrivals`, { waitUntil: 'networkidle' });
log('new arrivals hero', await page.getByText('Fresh Drops, Feel Good Finds.').isVisible());
await page.screenshot({ path: `${SHOTS}/listing-new-arrivals.png`, fullPage: true });

// ── /categories ──
await page.goto(`${BASE}/categories`, { waitUntil: 'networkidle' });
log('categories hero', await page.getByText('Your Perfect Wellness Bundle.').isVisible());
log('combo tab', await page.getByRole('tab', { name: 'Combo Packs' }).isVisible());
await page.getByRole('tab', { name: 'Combo Packs' }).click();
await page.waitForTimeout(600);
await page.screenshot({ path: `${SHOTS}/categories-combos.png`, fullPage: true });

// ── header nav + mega menu ──
await page.goto(BASE, { waitUntil: 'networkidle' });
log('nav Categories', await page.getByRole('link', { name: 'Categories' }).first().isVisible());
log('nav Track Order', await page.getByRole('link', { name: 'Track Order' }).first().isVisible());
await page.getByRole('link', { name: 'Shop' }).first().hover();
await page.waitForTimeout(400);
const megaVisible = await page.getByText('View all products →').isVisible().catch(() => false);
log('mega menu opens on hover', megaVisible);
await page.screenshot({ path: `${SHOTS}/header-megamenu.png` });

await browser.close();
const fails = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - fails}/${results.length} passed`);
process.exit(fails ? 1 : 0);
