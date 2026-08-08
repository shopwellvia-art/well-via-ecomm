/**
 * The non-catalogue half of global search: storefront destinations.
 *
 * Shoppers type intents, not just product names — "track my order", "return
 * policy", "delivery charges", "contact". None of those are rows in a table, so
 * the backend cannot answer them; they are React routes. The registry therefore
 * lives here, next to the router that owns those paths, and is matched
 * client-side. A hardcoded list of frontend paths in the API would go stale the
 * first time a route was renamed and would start serving dead links.
 *
 * `keywords` is the whole point of the registry: they are the words people
 * actually search for, which are rarely the words in the page title. Nobody
 * types "Refund Policy" — they type "return", "exchange", "money back". Every
 * `to` here must exist in `frontend/src/app/App.jsx`.
 */

export const SEARCH_PAGES = [
  // ── Shopping destinations ───────────────────────────────────────────────────
  {
    to: '/products',
    label: 'Shop all products',
    blurb: 'Browse the full range',
    keywords: ['shop', 'all', 'catalogue', 'catalog', 'store', 'products', 'buy'],
  },
  {
    to: '/bestsellers',
    label: 'Bestsellers',
    blurb: 'What everyone is buying',
    keywords: ['best', 'sellers', 'popular', 'top', 'trending'],
  },
  {
    to: '/new-arrivals',
    label: 'New arrivals',
    blurb: 'Just launched',
    keywords: ['new', 'latest', 'arrivals', 'recent', 'launch'],
  },
  {
    to: '/categories',
    label: 'Shop by goal',
    blurb: 'Sleep, immunity, beauty and more',
    keywords: ['categories', 'goals', 'collections', 'range', 'concern'],
  },
  {
    to: '/cart',
    label: 'Your cart',
    blurb: 'Review before checkout',
    keywords: ['cart', 'bag', 'basket', 'checkout'],
  },

  // ── Account (only offered when signed in — otherwise the click lands on a
  //    login wall, which reads as a broken search result) ─────────────────────
  {
    to: '/orders',
    label: 'My orders',
    blurb: 'Track a delivery, download an invoice',
    keywords: ['order', 'orders', 'track', 'tracking', 'delivery', 'status', 'invoice', 'bill', 'gst', 'awb'],
    requiresAuth: true,
  },
  {
    to: '/wishlist',
    label: 'Wishlist',
    blurb: 'Saved for later',
    keywords: ['wishlist', 'saved', 'favourites', 'favorites', 'liked'],
    requiresAuth: true,
  },
  {
    to: '/rewards',
    label: 'Rewards',
    blurb: 'Points, tiers and referrals',
    keywords: ['rewards', 'points', 'loyalty', 'referral', 'refer', 'tier', 'cashback', 'coupon', 'offer'],
    requiresAuth: true,
  },
  {
    to: '/account/addresses',
    label: 'Saved addresses',
    blurb: 'Manage delivery addresses',
    keywords: ['address', 'addresses', 'pincode', 'delivery address', 'location'],
    requiresAuth: true,
  },
  {
    to: '/account/security',
    label: 'Security & 2FA',
    blurb: 'Password and two-factor',
    keywords: ['security', 'password', '2fa', 'two factor', 'otp', 'login', 'authenticator'],
    requiresAuth: true,
  },

  // ── Help & policy ───────────────────────────────────────────────────────────
  {
    to: '/shipping',
    label: 'Shipping & delivery',
    blurb: 'Charges, timelines and coverage',
    keywords: ['shipping', 'delivery', 'courier', 'charges', 'free shipping', 'dispatch', 'how long'],
  },
  {
    to: '/refund',
    label: 'Returns & refunds',
    blurb: 'How to return an item',
    keywords: ['return', 'returns', 'refund', 'exchange', 'replace', 'cancel', 'money back'],
  },
  {
    to: '/contact',
    label: 'Contact us',
    blurb: 'Reach the support team',
    keywords: ['contact', 'support', 'help', 'email', 'phone', 'call', 'whatsapp', 'complaint'],
  },
  {
    to: '/terms',
    label: 'Terms & conditions',
    blurb: 'The fine print',
    keywords: ['terms', 'conditions', 'policy', 'legal', 'tnc'],
  },
  {
    to: '/privacy',
    label: 'Privacy policy',
    blurb: 'How we handle your data',
    keywords: ['privacy', 'data', 'cookies', 'gdpr', 'personal information'],
  },

  // ── Company ─────────────────────────────────────────────────────────────────
  {
    to: '/about',
    label: 'About Wellvia',
    blurb: 'Our story',
    keywords: ['about', 'story', 'brand', 'who', 'mission', 'founder'],
  },
  {
    to: '/stories',
    label: 'Wellvia stories',
    blurb: 'Journal and guides',
    keywords: ['stories', 'blog', 'journal', 'articles', 'guides', 'wellness tips'],
  },
  {
    to: '/careers',
    label: 'Careers',
    blurb: 'Work with us',
    keywords: ['careers', 'jobs', 'hiring', 'vacancy', 'work', 'internship'],
  },
  {
    to: '/press',
    label: 'Press',
    blurb: 'Media and coverage',
    keywords: ['press', 'media', 'news', 'coverage', 'pr'],
  },
  {
    to: '/corporate',
    label: 'Corporate information',
    blurb: 'Company details',
    keywords: ['corporate', 'company', 'registered', 'cin', 'gstin', 'bulk', 'b2b'],
  },
];

// Ranking buckets, best first. A label match beats a keyword match because the
// shopper who types "press" wants the Press page, not every page that mentions
// the press.
const RANK = { LABEL_EXACT: 0, LABEL_PREFIX: 1, LABEL_WORD: 2, KEYWORD_PREFIX: 3, KEYWORD: 4 };

function scorePage(page, term) {
  const label = page.label.toLowerCase();
  if (label === term) return RANK.LABEL_EXACT;
  if (label.startsWith(term)) return RANK.LABEL_PREFIX;
  // Word-boundary rather than plain `includes`: "art" should not surface
  // "Wellvia stories" just because the letters appear mid-word.
  if (label.split(/\s+/).some((w) => w.startsWith(term))) return RANK.LABEL_WORD;
  const keywords = page.keywords || [];
  if (keywords.some((k) => k === term || k.startsWith(term))) return RANK.KEYWORD_PREFIX;
  // Multi-word keywords ("free shipping", "money back") are matched loosely so
  // a partial phrase still lands.
  if (keywords.some((k) => k.includes(term))) return RANK.KEYWORD;
  return null;
}

/**
 * Storefront pages matching `query`, best first.
 *
 * Pure — no hooks, no fetch — so the ranking is unit-testable and the header can
 * render page hits with zero latency while the product request is still in
 * flight.
 *
 * @param query      raw text from the search box
 * @param isSignedIn gates `requiresAuth` entries; a signed-out shopper is not
 *                   offered "My orders", because that click lands on /login
 * @param limit      max results
 */
export function matchPages(query, { isSignedIn = false, limit = 4 } = {}) {
  const term = (query || '').trim().toLowerCase();
  if (term.length < 2) return [];
  return SEARCH_PAGES.filter((p) => !p.requiresAuth || isSignedIn)
    .map((page) => ({ page, score: scorePage(page, term) }))
    .filter(({ score }) => score !== null)
    .sort((a, b) => a.score - b.score || a.page.label.localeCompare(b.page.label))
    .slice(0, limit)
    .map(({ page }) => page);
}
