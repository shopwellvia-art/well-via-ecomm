import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { absoluteUrl, applyPageMeta, clampDescription } from '@/lib/pageMeta.js';

/**
 * PageMeta — renders nothing; sets title, description, canonical and social
 * tags for the page that mounts it, and restores the previous values on
 * unmount so nothing leaks across routes.
 *
 * Canonical defaults to the current path made absolute. Pass `canonicalPath`
 * to point a variant page at its canonical form (e.g. a filtered listing).
 *
 * Usage:
 *   <PageMeta title="Sleep Gummies" description={product.short_description} />
 */
export default function PageMeta({
  title,
  description,
  canonicalPath,
  image,
  type = 'website',
  noindex = false,
  titleSuffix = 'Wellvia',
}) {
  const location = useLocation();
  const path = canonicalPath ?? location.pathname;

  // Titles read "<page> | Wellvia". Product names already carry the brand
  // ("Wellvia Sleep Gummies"), and "Wellvia Sleep Gummies | Wellvia" both reads
  // badly and burns characters out of the ~60 Google renders — so the suffix is
  // only appended when the brand isn't in the title already.
  const fullTitle = !title
    ? undefined
    : titleSuffix && !title.toLowerCase().includes(titleSuffix.toLowerCase())
      ? `${title} | ${titleSuffix}`
      : title;

  const desc = clampDescription(description);
  const canonical = absoluteUrl(path);
  const absImage = image ? absoluteUrl(image) : undefined;

  useEffect(
    () =>
      applyPageMeta({
        title: fullTitle,
        description: desc,
        canonical,
        image: absImage,
        type,
        noindex,
      }),
    [fullTitle, desc, canonical, absImage, type, noindex],
  );

  return null;
}
