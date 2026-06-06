/**
 * Return `url` only when its scheme is safe for use in an anchor href.
 *
 * Allowed:
 *   - Site-relative paths starting with "/" or "#"
 *   - http: and https: absolute URLs
 *   - mailto: and tel: links
 *
 * Rejected (returns `fallback`):
 *   - javascript:, data:, vbscript:, and any other unknown scheme
 *   - null / undefined / non-string values
 *
 * @param {unknown} url
 * @param {string}  fallback  Returned when `url` is unsafe. Defaults to '#'.
 * @returns {string}
 */
export function safeUrl(url, fallback = '#') {
  if (typeof url !== 'string' || url.trim() === '') return fallback;

  // Protocol-relative ("//host") points off-site — reject (check before "/").
  if (url.startsWith('//')) return fallback;

  // Site-relative paths are always safe.
  if (url.startsWith('/') || url.startsWith('#')) return url;

  // Extract the scheme (everything before the first colon).
  // toLowerCase guards against `JavaScript:` casing tricks.
  const scheme = url.split(':')[0].toLowerCase().trim();
  const ALLOWED_SCHEMES = new Set(['http', 'https', 'mailto', 'tel']);

  return ALLOWED_SCHEMES.has(scheme) ? url : fallback;
}
