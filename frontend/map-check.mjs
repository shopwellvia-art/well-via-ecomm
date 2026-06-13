// One-shot diagnostic: can a real user reach the map picker?
// Run from frontend/: node map-check.mjs
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'http://localhost';
const SHOTS = 'D:/ng/simple ecomers/xyz/mapcheck';
fs.mkdirSync(SHOTS, { recursive: true });

const email = `mapcheck${Date.now()}@test.com`;
const password = 'MapCheck#2026x';

// 1. Register a throwaway user via the API.
const reg = await fetch(`${BASE}/api/v1/auth/register`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, password, full_name: 'Map Check' }),
});
console.log('register:', reg.status, reg.status >= 400 ? await reg.text() : 'ok');

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  permissions: ['geolocation'],
  geolocation: { latitude: 12.9716, longitude: 77.5946 },
});
const page = await context.newPage();
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('pageerror', (e) => errors.push(`PAGEERROR: ${e.message}`));

// 2. Log in through the UI.
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', email);
await page.fill('input[type="password"]', password);
await page.click('button[type="submit"]');
await page.waitForTimeout(2500);
console.log('after login url:', page.url());

// 3. Addresses page → open the add-address form.
await page.goto(`${BASE}/account/addresses`, { waitUntil: 'networkidle' });
await page.waitForTimeout(1000);
await page.screenshot({ path: `${SHOTS}/1-addresses-page.png` });

// Click whichever add-address trigger exists.
const addBtn = page
  .getByRole('button', { name: /add.*address|new address/i })
  .first();
if (await addBtn.count()) {
  await addBtn.click();
  await page.waitForTimeout(800);
}
await page.screenshot({ path: `${SHOTS}/2-address-form.png`, fullPage: true });

const pickBtn = page.getByRole('button', { name: /pick on map/i }).first();
const pickVisible = (await pickBtn.count()) && (await pickBtn.isVisible());
console.log('pick-on-map button visible:', pickVisible);

// 4. Open the map picker.
if (pickVisible) {
  await pickBtn.click();
  await page.waitForTimeout(6000); // lazy chunk + tiles + auto-geolocate + reverse geocode
  await page.screenshot({ path: `${SHOTS}/3-map-open.png` });
  const leaflet = await page.locator('.leaflet-container').count();
  console.log('leaflet container rendered:', leaflet > 0);
  const panelText = await page
    .locator('text=/560|Bengaluru|pincode|location/i')
    .first()
    .textContent()
    .catch(() => null);
  console.log('panel sample text:', panelText);
}

console.log('console errors:', errors.length ? errors : 'none');
await browser.close();
