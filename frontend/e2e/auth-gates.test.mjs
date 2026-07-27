/**
 * Signed-out gates: /wishlist shows its sign-in prompt in place, /orders
 * bounces to /login with the attempted path in ?next=.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  launchBrowser,
  newPage,
  goto,
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

test('/wishlist prompts guests to sign in', async () => {
  const { context, page, errors } = await newPage(browser);
  try {
    await goto(page, '/wishlist');
    await page
      .getByText('My Wishlist')
      .first()
      .waitFor({ state: 'visible', timeout: 10000 });
    await page
      .getByText('Sign in to view your wishlist')
      .first()
      .waitFor({ state: 'visible', timeout: 10000 });
    // The prompt's CTA carries the return path.
    const signIn = page.locator('a[href="/login?next=/wishlist"]');
    assert.ok((await signIn.count()) > 0, 'Sign In link targets /login?next=/wishlist');
    expectNoPageErrors(errors, '/wishlist');
  } finally {
    await context.close();
  }
});

test('/orders redirects guests to /login?next=%2Forders', async () => {
  const { context, page, errors } = await newPage(browser);
  try {
    await page.goto(BASE_URL + '/orders', { waitUntil: 'domcontentloaded' });
    await page.waitForURL(
      (url) => url.pathname === '/login' && url.search === '?next=%2Forders',
      { timeout: 10000 },
    );
    // Login page actually rendered (not just the URL changing).
    await page
      .locator('input[type="email"]')
      .first()
      .waitFor({ state: 'visible', timeout: 10000 });
    expectNoPageErrors(errors, '/orders redirect');
  } finally {
    await context.close();
  }
});
