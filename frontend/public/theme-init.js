// Storefront is light-only (Flipkart look). Force light before paint so a
// stale 'dark' preference can never flash the old theme.
// Externalized from an inline <script> so it complies with the production CSP
// (`script-src 'self'`) — see frontend/nginx.conf. Loaded render-blocking in
// <head>, so it still runs before first paint.
(function () {
  try {
    var root = document.documentElement;
    root.classList.remove('dark', 'light');
    root.classList.add('light');
    root.style.colorScheme = 'light';
  } catch (e) {}
})();
