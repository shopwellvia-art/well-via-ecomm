// Runtime smoke test: load every storefront route, capture uncaught exceptions
// and console errors. Catches React render crashes that `npm run build` cannot.
const { chromium } = require('playwright');

const BASE = process.env.SMOKE_BASE || 'http://localhost:5174';
const PID = process.env.SMOKE_PID || '701';

const ROUTES = [
  ['home', '/'],
  ['plp', '/products'],
  ['plp-search', '/products?q=shirt'],
  ['pdp', `/products/${PID}`],
  ['cart', '/cart'],
  ['checkout', '/checkout'],
  ['orders', '/orders'],
  ['wishlist', '/wishlist'],
  ['rewards', '/rewards'],
  ['addresses', '/account/addresses'],
  ['security', '/account/security'],
  ['login', '/login'],
  ['forgot', '/forgot-password'],
  ['about', '/about'],
  ['contact', '/contact'],
  ['careers', '/careers'],
  ['stories', '/stories'],
  ['press', '/press'],
  ['corporate', '/corporate'],
  ['notfound', '/this-route-does-not-exist'],
  ['pay-return', '/payments/return?mtid=SMOKE123'],
  ['pay-mock', '/payments/mock/SMOKE123'],
  ['pay-mock-noid', '/payments/mock/'],
];

// Console noise we don't care about (network failures when anon / no data).
const IGNORE = [
  /Failed to load resource/i,
  /the server responded with a status of (401|403|404|500|400)/i,
  /\[vite\]/i,
  /Download the React DevTools/i,
  /401|403|404/,
  /net::ERR/i,
  /AxiosError/i,
  /Request failed with status/i,
];

(async () => {
  const browser = await chromium.launch();
  const results = [];
  for (const [name, path] of ROUTES) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e.message || e)));
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      const t = m.text();
      if (IGNORE.some((re) => re.test(t))) return;
      consoleErrors.push(t);
    });
    let nav = 'ok';
    try {
      await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 25000 });
      await page.waitForTimeout(900); // let lazy chunk + effects settle
    } catch (e) {
      nav = 'NAV_FAIL: ' + String(e.message || e).split('\n')[0];
    }
    // crude crash detector: empty body or react error overlay
    const bodyLen = await page.evaluate(() => (document.body && document.body.innerText || '').trim().length).catch(() => 0);
    const finalUrl = page.url().replace(BASE, '');
    try { await page.screenshot({ path: `verify-shots/smoke-${name}.png`, fullPage: false }); } catch {}
    results.push({ name, path, nav, finalUrl, bodyLen, pageErrors, consoleErrors });
    await ctx.close();
  }
  await browser.close();

  let bad = 0;
  console.log('\n================ STOREFRONT SMOKE RESULTS ================');
  for (const r of results) {
    const crash = r.pageErrors.length > 0;
    const cerr = r.consoleErrors.length > 0;
    const navfail = r.nav !== 'ok';
    const blank = r.bodyLen < 20;
    const ok = !crash && !navfail && !blank;
    if (!ok) bad++;
    const flag = ok ? 'PASS' : 'FAIL';
    let line = `[${flag}] ${r.name.padEnd(14)} ${r.path}`;
    if (navfail) line += `  ${r.nav}`;
    if (blank) line += `  BLANK(body=${r.bodyLen})`;
    if (r.finalUrl !== r.path) line += `  ->${r.finalUrl}`;
    console.log(line);
    if (crash) r.pageErrors.forEach((e) => console.log('        ⛔ pageerror: ' + e.split('\n')[0]));
    if (cerr) r.consoleErrors.slice(0, 4).forEach((e) => console.log('        ⚠ console: ' + e.slice(0, 200)));
  }
  console.log('=========================================================');
  console.log(`${results.length} routes, ${bad} with problems.`);
  process.exit(bad > 0 ? 1 : 0);
})();
