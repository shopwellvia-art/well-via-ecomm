import { useEffect, useState } from 'react';
import { cn, mediaUrl } from '@/lib/utils';

/**
 * WImage — wellness image with graceful fallback.
 *
 * Renders `<img src={mediaUrl(src)}>`. When src is null/empty, or the image
 * fails to load (404, broken URL), shows a warm gradient placeholder with the
 * first letter of `alt` as an initial. Mirrors the pattern from ProductMedia.jsx.
 *
 * Props:
 *   src       — raw image URL (may be absolute /media/…, relative, or null)
 *   alt       — image alt text; first letter used as initial in fallback
 *   className — additional Tailwind classes (sizing, etc.)
 *   shape     — 'rounded' (rounded-xl2) | 'circle' (rounded-full) | 'pill' | undefined
 */
export default function WImage({ src, alt = '', className, shape }) {
  const initial = (alt || '?').trim().charAt(0).toUpperCase();
  const [failed, setFailed] = useState(false);

  // Reset the error flag when the image source changes (e.g. list reuse).
  useEffect(() => {
    setFailed(false);
  }, [src]);

  const normalized = mediaUrl(src);
  const showImage = normalized && !failed;

  const shapeClass =
    shape === 'circle' ? 'rounded-full' :
    shape === 'pill'   ? 'rounded-full' :
    shape === 'rounded'? 'rounded-xl2'  :
    '';

  if (showImage) {
    return (
      <img
        src={normalized}
        alt={alt}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
        className={cn('object-cover', shapeClass, className)}
      />
    );
  }

  // Gradient + initial fallback — consistent with wellness colour language.
  return (
    <div
      aria-hidden="true"
      className={cn(
        'grid place-items-center bg-gradient-to-br from-wgold/25 via-wpaper to-wcanvas',
        shapeClass,
        className,
      )}
    >
      <span className="select-none text-4xl font-semibold text-wink/30">
        {initial}
      </span>
    </div>
  );
}
