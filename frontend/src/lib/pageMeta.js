/**
 * Per-page document head management.
 *
 * The app has no head library. `SiteMeta` sets one global document.title from
 * the storefront config, and because it sits above the pages in the tree its
 * effect runs AFTER a page's effect — so a page that simply assigned
 * document.title would be silently overwritten on every config render.
 *
 * The claim counter below is what stops that: a page mounting `usePageMeta`
 * claims the title, `SiteMeta` defers while any claim is live, and the last
 * page to unmount releases it so the site default comes back.
 *
 * Tags are created on demand and removed on unmount, so a page never leaks its
 * description or canonical onto the next route.
 */

let titleClaims = 0;

/** True while some page is managing the title; SiteMeta must stand down. */
export function isPageTitleClaimed() {
  return titleClaims > 0;
}

function claimTitle() {
  titleClaims += 1;
}

function releaseTitle() {
  titleClaims = Math.max(0, titleClaims - 1);
}

/**
 * Upsert a <meta> tag. `selector` identifies it, `attrs` creates it when absent.
 * Returns a cleanup that removes the tag only if this call created it — a tag
 * that shipped in index.html is left alone.
 */
function upsertMeta(selector, attrs, content) {
  if (!content) return () => {};
  let el = document.head.querySelector(selector);
  let created = false;
  if (!el) {
    el = document.createElement('meta');
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    document.head.appendChild(el);
    created = true;
  }
  const previous = el.getAttribute('content');
  el.setAttribute('content', content);
  return () => {
    if (created) {
      el.remove();
    } else if (previous != null) {
      el.setAttribute('content', previous);
    }
  };
}

function upsertLink(rel, href) {
  if (!href) return () => {};
  let el = document.head.querySelector(`link[rel="${rel}"]`);
  let created = false;
  if (!el) {
    el = document.createElement('link');
    el.setAttribute('rel', rel);
    document.head.appendChild(el);
    created = true;
  }
  const previous = el.getAttribute('href');
  el.setAttribute('href', href);
  return () => {
    if (created) {
      el.remove();
    } else if (previous != null) {
      el.setAttribute('href', previous);
    }
  };
}

/**
 * Absolute URL for the current route. Canonical and og:url must be absolute;
 * a relative canonical is ignored by crawlers.
 */
export function absoluteUrl(path) {
  if (typeof window === 'undefined') return path;
  try {
    return new URL(path || window.location.pathname, window.location.origin).href;
  } catch {
    return window.location.href;
  }
}

/**
 * Apply page metadata. Returns a cleanup function; `usePageMeta` wires it to
 * the effect teardown.
 */
export function applyPageMeta({
  title,
  description,
  canonical,
  image,
  type = 'website',
  siteName = 'Wellvia',
  noindex = false,
}) {
  const cleanups = [];
  const previousTitle = document.title;

  if (title) {
    claimTitle();
    document.title = title;
    cleanups.push(() => {
      releaseTitle();
      document.title = previousTitle;
    });
  }

  cleanups.push(upsertMeta('meta[name="description"]', { name: 'description' }, description));
  cleanups.push(upsertLink('canonical', canonical));

  cleanups.push(upsertMeta('meta[property="og:title"]', { property: 'og:title' }, title));
  cleanups.push(
    upsertMeta('meta[property="og:description"]', { property: 'og:description' }, description),
  );
  cleanups.push(upsertMeta('meta[property="og:type"]', { property: 'og:type' }, type));
  cleanups.push(upsertMeta('meta[property="og:url"]', { property: 'og:url' }, canonical));
  cleanups.push(upsertMeta('meta[property="og:site_name"]', { property: 'og:site_name' }, siteName));
  cleanups.push(upsertMeta('meta[property="og:image"]', { property: 'og:image' }, image));

  cleanups.push(
    upsertMeta(
      'meta[name="twitter:card"]',
      { name: 'twitter:card' },
      image ? 'summary_large_image' : 'summary',
    ),
  );
  cleanups.push(upsertMeta('meta[name="twitter:title"]', { name: 'twitter:title' }, title));
  cleanups.push(
    upsertMeta('meta[name="twitter:description"]', { name: 'twitter:description' }, description),
  );
  cleanups.push(upsertMeta('meta[name="twitter:image"]', { name: 'twitter:image' }, image));

  if (noindex) {
    cleanups.push(upsertMeta('meta[name="robots"]', { name: 'robots' }, 'noindex,follow'));
  }

  return () => cleanups.forEach((fn) => fn());
}

/**
 * Clamp a description to the ~160 characters search engines display, cutting on
 * a word boundary so the snippet never ends mid-word.
 */
export function clampDescription(text, max = 160) {
  if (!text) return '';
  const flat = String(text).replace(/\s+/g, ' ').trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max - 1);
  const lastSpace = cut.lastIndexOf(' ');
  return `${(lastSpace > 40 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}
