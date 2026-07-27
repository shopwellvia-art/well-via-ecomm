/**
 * Guest cart flow, end to end in one browser context (read-only against the
 * backend — the guest cart lives in localStorage, no order is placed):
 *
 *   add-to-cart on /products → header badge updates + `guest-cart`
 *   localStorage line → /cart shows the guest line → Proceed to Checkout
 *   lands on /checkout showing the guest login panel.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  launchBrowser,
  newPage,
  goto,
  fetchProducts,
  expectNoPageErrors,
} from './helpers.mjs';

let browser;
before(async () => {
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close();
});

test('guest add-to-cart → cart page → checkout login panel', async (t) => {
  const { items } = await fetchProducts('page=1&page_size=12');
  const product = items.find((p) => p.stock > 0);
  assert.ok(product, 'API returned an in-stock product to add');

  const { context, page, errors } = await newPage(browser);
  try {
    await goto(page, '/products');

    await t.test('add to cart updates the badge and localStorage', async () => {
      const card = page
        .locator('article')
        .filter({ hasText: product.name })
        .first();
      await card.waitFor({ state: 'visible', timeout: 10000 });
      await card.getByRole('button', { name: 'Add to Cart' }).click();

      // Header badge — desktop cart button announces its count.
      await page
        .locator('button[aria-label="Open cart (1 items)"]')
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });

      // Guest cart persisted to localStorage (zustand persist envelope).
      const stored = await page.evaluate(() =>
        localStorage.getItem('guest-cart'),
      );
      assert.ok(stored, 'guest-cart key exists in localStorage');
      const lines = JSON.parse(stored)?.state?.items;
      assert.deepEqual(
        lines,
        [{ product_id: product.id, quantity: 1 }],
        'guest-cart holds exactly the added line',
      );
    });

    await t.test('cart page shows the guest line', async () => {
      await goto(page, '/cart');
      await page
        .getByRole('heading', { name: /Your Cart/i })
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
      await page
        .getByText(product.name, { exact: false })
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
    });

    await t.test('proceed to checkout lands on the guest login panel', async () => {
      const proceed = page.getByRole('button', {
        name: 'Proceed to Checkout',
      });
      await proceed.waitFor({ state: 'visible', timeout: 10000 });
      assert.ok(await proceed.isEnabled(), 'Proceed to Checkout is enabled');
      await proceed.click();

      await page.waitForURL((url) => url.pathname === '/checkout', {
        timeout: 10000,
      });
      await page
        .getByText('Login to continue')
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
      // Embedded LoginPanel — email + password fields for the guest.
      await page
        .locator('input[type="email"]')
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
      await page
        .locator('input[type="password"]')
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
    });

    expectNoPageErrors(errors, 'guest cart flow');
  } finally {
    await context.close();
  }
});
