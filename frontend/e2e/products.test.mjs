/**
 * /products listing: the grid shows the products returned by
 * GET /api/v1/products — asserted against live API data, never hardcoded.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  launchBrowser,
  newPage,
  goto,
  fetchProducts,
  expectNoPageErrors,
  BASE_URL,
} from './helpers.mjs';

let browser;
before(async () => {
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close();
});

test('/products shows the products returned by GET /api/v1/products', async () => {
  // Live data first — the suite is meaningless against an empty catalog.
  const seed = await fetchProducts('page=1&page_size=12');
  assert.ok(seed.total > 0, 'API reports at least one product');

  const { context, page, errors } = await newPage(browser);
  try {
    // Capture the listing page's own products request so the DOM is compared
    // against exactly the dataset the page rendered from.
    const respPromise = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === '/api/v1/products' &&
        r.request().method() === 'GET',
      { timeout: 15000 },
    );
    await page.goto(BASE_URL + '/products', { waitUntil: 'domcontentloaded' });
    const listResp = await respPromise;
    assert.equal(listResp.status(), 200, 'products API responds 200');
    const listData = await listResp.json();
    assert.ok(
      Array.isArray(listData.items) && listData.items.length > 0,
      'listing request returned items',
    );

    // Every product from the page's API response is visible in the grid.
    for (const product of listData.items) {
      await page
        .getByText(product.name, { exact: false })
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
    }

    // And the page's dataset is consistent with a direct API fetch.
    const apiNames = new Set(seed.items.map((p) => p.name));
    const overlap = listData.items.filter((p) => apiNames.has(p.name));
    assert.ok(
      overlap.length > 0,
      'listing dataset overlaps the directly fetched API data',
    );

    expectNoPageErrors(errors, '/products');
  } finally {
    await context.close();
  }
});
