// Canonical factory defaults for the storefront config document. Must stay in
// sync with DEFAULT_STOREFRONT in backend/app/schemas/storefront.py — the
// storefront renders these when the API is unreachable or a field is unset.
export const STOREFRONT_DEFAULTS = {
  site_title: 'Wellvia — Wellness Redefined',
  brand_name: 'WELLVIA',
  tagline: 'Wellness Redefined',
  logo_url: '',
  favicon_url: '',
  nav_items: [
    { label: 'Home', to: '/', end: true, visible: true },
    { label: 'Shop', to: '/products', megaMenu: true, visible: true },
    { label: 'Categories', to: '/categories', visible: true },
    { label: 'Best Sellers', to: '/bestsellers', visible: true },
    { label: 'New Arrivals', to: '/new-arrivals', visible: true },
    { label: 'Contact', to: '/contact', visible: true },
    { label: 'About', to: '/about', visible: true },
  ],
  homepage_sections: [
    { key: 'hero', label: 'Hero banner', visible: true, title: '' },
    { key: 'why-we-exist', label: 'Why We Exist', visible: true, title: '' },
    { key: 'bestsellers-rail', label: 'Bestsellers rail', visible: true, title: '' },
    { key: 'new-launches', label: 'New Launches', visible: true, title: '' },
    { key: 'daily-routine', label: 'Build Your Daily Routine', visible: true, title: '' },
    { key: 'blog', label: 'Blog cards', visible: true, title: '' },
    { key: 'reviews', label: 'Reviews carousel', visible: true, title: '' },
    { key: 'brand-philosophy', label: 'Brand philosophy (puzzle)', visible: true, title: '' },
  ],
};
