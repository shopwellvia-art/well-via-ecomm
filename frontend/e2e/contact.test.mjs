/**
 * /contact renders the enquiry form fields. Render-only — the form is never
 * submitted (read-only suite).
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

test('/contact renders the form fields', async () => {
  const { context, page, errors } = await newPage(browser);
  try {
    const resp = await goto(page, '/contact');
    assert.equal(resp.status(), 200, 'GET /contact returns 200');

    const form = page.locator('form').first();
    await form.waitFor({ state: 'visible', timeout: 10000 });

    for (const selector of [
      'form input[autocomplete="name"]',
      'form input[type="email"]',
      'form input[autocomplete="tel"]',
      'form textarea',
      'form button[type="submit"]',
    ]) {
      await page
        .locator(selector)
        .first()
        .waitFor({ state: 'visible', timeout: 10000 });
    }

    expectNoPageErrors(errors, '/contact');
  } finally {
    await context.close();
  }
});
