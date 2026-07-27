/**
 * Home page: renders the storefront shell (header/nav, footer, content) with
 * zero unexpected console/page errors.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  launchBrowser,
  newPage,
  goto,
  expectNoPageErrors,
} from './helpers.mjs';

let browser;
before(async () => {
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close();
});

test('home renders with zero console errors', async () => {
  const { context, page, errors } = await newPage(browser);
  try {
    const resp = await goto(page, '/');
    assert.equal(resp.status(), 200, 'GET / should return 200');

    // Shell renders: header + footer landmarks.
    await page.locator('header').first().waitFor({ state: 'visible' });
    await page.locator('footer').first().waitFor({ state: 'attached' });

    // Real content mounted (not a blank/broken SPA shell).
    const title = await page.title();
    assert.ok(title.trim().length > 0, 'document.title should be set');
    const bodyText = await page.textContent('body');
    assert.ok(
      bodyText.trim().length > 500,
      `body text should be substantial (got ${bodyText.trim().length} chars)`,
    );

    // Storefront nav offers a way into the catalog.
    const productLinks = await page.locator('a[href^="/products"]').count();
    assert.ok(productLinks > 0, 'expected at least one link into /products');

    expectNoPageErrors(errors, 'home');
  } finally {
    await context.close();
  }
});
