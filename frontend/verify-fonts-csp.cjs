// Serve the freshly-built dist/ with the EXACT production CSP header from
// nginx.conf, then Playwright-load it and assert: (a) no CSP violations for
// fonts/scripts, (b) Roboto is actually loaded from self (not a fallback).
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const DIST = path.join(__dirname, 'dist');
const PORT = 4321;
const CSP =
  "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'";
const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.ico': 'image/x-icon',
};

const server = http.createServer((req, res) => {
  let urlPath = decodeURIComponent(req.url.split('?')[0]);
  let file = path.join(DIST, urlPath);
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html'); // SPA fallback
  const ext = path.extname(file).toLowerCase();
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('Content-Type', TYPES[ext] || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});

server.listen(PORT, async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const violations = [];
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (/Content Security Policy|violates the following/i.test(t)) violations.push(t.slice(0, 160));
  });
  await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle', timeout: 20000 });
  const font = await page.evaluate(async () => {
    await document.fonts.ready;
    const roboto = [...document.fonts].filter((f) => /roboto/i.test(f.family));
    return {
      bodyFamily: getComputedStyle(document.body).fontFamily,
      robotoCheck: document.fonts.check('500 16px Roboto'),
      robotoFaces: roboto.length,
      robotoLoaded: roboto.filter((f) => f.status === 'loaded').length,
    };
  });
  await browser.close();
  server.close();

  const fontViol = violations.filter((v) => /googleapis|gstatic|font|inline script|script-src/i.test(v));
  console.log('\n===== FONT / SCRIPT CSP VERIFICATION (prod CSP enforced) =====');
  console.log('body font-family   :', font.bodyFamily);
  console.log('Roboto faces loaded:', font.robotoLoaded + '/' + font.robotoFaces, '| check(Roboto):', font.robotoCheck);
  console.log('font/script CSP violations:', fontViol.length);
  fontViol.forEach((v) => console.log('   ⛔', v));
  console.log('other CSP violations      :', violations.length - fontViol.length);
  violations.filter((v) => !fontViol.includes(v)).slice(0, 5).forEach((v) => console.log('   ·', v));
  const ok = fontViol.length === 0 && font.robotoLoaded > 0 && /roboto/i.test(font.bodyFamily);
  console.log(ok ? '\n✅ PASS: Roboto self-hosted & loading under prod CSP; no font/script CSP violations.' : '\n❌ FAIL');
  console.log('=============================================================');
  process.exit(ok ? 0 : 1);
});
