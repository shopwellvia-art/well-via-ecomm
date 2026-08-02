import { useEffect } from 'react';

/**
 * JsonLd — injects a <script type="application/ld+json"> for the mounting page
 * and removes it on unmount, so schema never leaks onto the next route.
 *
 * `id` keeps one slot per logical block (product, breadcrumb, organisation) so
 * a re-render replaces rather than appends.
 */
export default function JsonLd({ id, data }) {
  const json = data ? JSON.stringify(data) : null;

  useEffect(() => {
    if (!json) return undefined;
    const elementId = `ld-${id}`;
    let el = document.getElementById(elementId);
    let created = false;
    if (!el) {
      el = document.createElement('script');
      el.type = 'application/ld+json';
      el.id = elementId;
      document.head.appendChild(el);
      created = true;
    }
    el.textContent = json;
    return () => {
      if (created) el.remove();
    };
  }, [id, json]);

  return null;
}
