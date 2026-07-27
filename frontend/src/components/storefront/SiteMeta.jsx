import { useEffect } from 'react';
import { mediaUrl } from '@/lib/utils';
import { useStorefrontConfigWithDefaults } from '@/features/storefront-config/hooks.js';

/**
 * SiteMeta — renders nothing; syncs the browser tab with the storefront
 * config. document.title follows site_title and the favicon follows
 * favicon_url (uploaded favicons are raster; the bundled default is svg).
 */
export default function SiteMeta() {
  const { config } = useStorefrontConfigWithDefaults();
  const siteTitle = config.site_title;
  const faviconUrl = config.favicon_url ? mediaUrl(config.favicon_url) : '';

  useEffect(() => {
    if (siteTitle && document.title !== siteTitle) {
      document.title = siteTitle;
    }
  }, [siteTitle]);

  useEffect(() => {
    const href = faviconUrl || '/favicon.svg';
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    if (link.getAttribute('href') !== href) {
      link.setAttribute('href', href);
    }
    if (faviconUrl) {
      // Uploaded favicons are raster — drop the type so the browser sniffs it.
      if (link.hasAttribute('type')) link.removeAttribute('type');
    } else if (link.getAttribute('type') !== 'image/svg+xml') {
      link.setAttribute('type', 'image/svg+xml');
    }
  }, [faviconUrl]);

  return null;
}
