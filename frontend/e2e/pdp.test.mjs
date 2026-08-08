/**
 * Product detail page: name and price match what GET /api/v1/products
 * reports for that product (live data, never hardcoded).
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  launchBrowser,
  newPage,
  goto,
  fetchProducts,
  formatINR,
  normalizeMoney,
  expectNoPageErrors,
} from './helpers.mjs';

let browser;
before(async () => {
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close();
});

test('PDP name and price match the API', async () => {
  const { items } = await fetchProducts('page=1&page_size=12');
  const product = items.find((p) => p.stock > 0) ?? items[0];
  assert.ok(product, 'API returned a product to open');

  const { context, page, errors } = await newPage(browser);
  try {
    const resp = await goto(page, `/products/${product.id}`);
    assert.equal(resp.status(), 200, `GET /products/${product.id} returns 200`);

    // Name from the API appears on the page.
    await page
      .getByText(product.name, { exact: false })
      .first()
      .waitFor({ state: 'visible', timeout: 10000 });

    // Price from the API appears, formatted the way the storefront formats it
    // (en-IN INR); whitespace normalized so ICU variants can't flake it.
    const expectedPrice = normalizeMoney(formatINR(product.price));
    const bodyText = normalizeMoney(await page.textContent('body'));
    assert.ok(
      bodyText.includes(expectedPrice),
      `PDP should show ${formatINR(product.price)} for "${product.name}"`,
    );

    expectNoPageErrors(errors, `/products/${product.id}`);
  } finally {
    await context.close();
  }
});
