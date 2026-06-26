import { useEffect, useMemo, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Play, ZoomIn } from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { ProductMedia } from '../ProductMedia.jsx';
import { WishlistButton } from '@/features/wishlist/WishlistButton.jsx';

/**
 * Product gallery — a vertical thumbnail rail beside a large main image with
 * an Amazon-style hover magnifier. Thumbnails swap the main image on
 * hover/click; on mobile the rail drops below and the stage is swipeable.
 *
 * A thumbnail/main slot becomes a <video> when its URL is a video file, so
 * uploading a video "just works" without any extra setup.
 */

const VIDEO_RE = /\.(mp4|webm|ogg|mov|m4v)(\?|#|$)/i;
const isVideo = (url) => typeof url === 'string' && VIDEO_RE.test(url);

// Amazon-style hover zoom magnification factor.
const ZOOM = 2.4;
const clamp = (v, min, max) => Math.min(Math.max(v, min), max);

export function LuxuryGallery({ product }) {
  const images = useMemo(
    () =>
      [...(product.images || [])].sort(
        (a, b) =>
          Number(b.is_primary) - Number(a.is_primary) || a.position - b.position,
      ),
    [product.images],
  );

  const [active, setActive] = useState(0);
  const [lens, setLens] = useState(null);
  const stageRef = useRef(null);
  const touchX = useRef(null);

  // Tracks whether the main stage image failed to load (404 / CDN error).
  const [mainImgFailed, setMainImgFailed] = useState(false);
  // Tracks per-thumbnail image load failures.
  const [thumbFailed, setThumbFailed] = useState({});

  const current = images[Math.min(active, images.length - 1)];

  // Reset the main-image error flag whenever the active slide changes.
  useEffect(() => {
    setMainImgFailed(false);
  }, [current?.url]);

  // No images at all — graceful gradient placeholder.
  if (images.length === 0) {
    return (
      <div className="flex gap-3">
        <div className="aspect-square w-full overflow-hidden rounded-lg border border-wline bg-wcard p-4">
          <ProductMedia product={product} className="object-contain" eager />
        </div>
      </div>
    );
  }

  const currentIsVideo = isVideo(current.url);

  function onMouseMove(e) {
    if (currentIsVideo) return;
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    const { width: w, height: h } = rect;
    const px = clamp(((e.clientX - rect.left) / w) * 100, 0, 100);
    const py = clamp(((e.clientY - rect.top) / h) * 100, 0, 100);
    const lensW = w / ZOOM;
    const lensH = h / ZOOM;
    const lx = clamp(e.clientX - rect.left - lensW / 2, 0, w - lensW);
    const ly = clamp(e.clientY - rect.top - lensH / 2, 0, h - lensH);
    setLens({ px, py, x: lx, y: ly, w: lensW, h: lensH });
  }

  function step(dir) {
    setActive((i) => (i + dir + images.length) % images.length);
  }
  function onTouchStart(e) {
    touchX.current = e.touches[0].clientX;
  }
  function onTouchEnd(e) {
    if (touchX.current == null) return;
    const dx = e.changedTouches[0].clientX - touchX.current;
    if (Math.abs(dx) > 40) step(dx < 0 ? 1 : -1);
    touchX.current = null;
  }
  // Keyboard arrow navigation on the main stage.
  function onKeyDown(e) {
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  }

  return (
    <div className="flex gap-3">
      {/* Thumbnail rail — vertical column of ~size-14 chips */}
      <div
        role="tablist"
        aria-label="Product images"
        className="flex shrink-0 flex-col gap-2.5 overflow-y-auto"
      >
        {images.map((img, i) => {
          const vid = isVideo(img.url);
          return (
            <button
              key={img.id}
              type="button"
              role="tab"
              onMouseEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              onClick={() => setActive(i)}
              aria-label={`View ${vid ? 'video' : 'image'} ${i + 1}`}
              aria-selected={i === active}
              className={cn(
                'relative size-14 shrink-0 overflow-hidden rounded-lg border bg-wcard transition-all duration-200',
                i === active
                  ? 'border-wgreen ring-1 ring-wgreen/40'
                  : 'border-wline opacity-90 hover:opacity-100 hover:border-wline',
              )}
            >
              {vid ? (
                <>
                  <video src={img.url} muted playsInline className="size-full object-cover" />
                  <span className="absolute inset-0 grid place-items-center bg-black/30">
                    <Play className="size-4 text-white" aria-hidden="true" />
                  </span>
                </>
              ) : thumbFailed[i] ? (
                // Fallback for broken thumbnail URLs.
                <ProductMedia product={product} className="size-full object-contain p-1" />
              ) : (
                <img
                  src={img.url}
                  alt=""
                  loading={i < 6 ? 'eager' : 'lazy'}
                  className="size-full object-contain p-1"
                  onError={() => setThumbFailed((prev) => ({ ...prev, [i]: true }))}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Stage */}
      <div className="relative min-w-0 flex-1">
        <div
          ref={stageRef}
          onMouseMove={onMouseMove}
          onMouseLeave={() => setLens(null)}
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
          onKeyDown={onKeyDown}
          tabIndex={0}
          aria-label={`Product image ${active + 1} of ${images.length}`}
          className="group relative aspect-square w-full select-none overflow-hidden rounded-lg border border-wline bg-wcard p-4"
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={current.id}
              initial={{ opacity: 0, scale: 1.02 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              className="size-full"
            >
              {currentIsVideo ? (
                <video
                  src={current.url}
                  controls
                  playsInline
                  className="size-full bg-black object-contain"
                />
              ) : mainImgFailed ? (
                // Fallback when the main stage image URL is broken.
                <ProductMedia product={product} className="size-full object-contain" />
              ) : (
                <img
                  src={current.url}
                  alt={product.name}
                  fetchpriority="high"
                  draggable={false}
                  className={cn('size-full object-contain', !lens && 'cursor-zoom-in')}
                  onError={() => setMainImgFailed(true)}
                />
              )}
            </motion.div>
          </AnimatePresence>

          {/* Lens — tracks the cursor over the source image */}
          {lens && !currentIsVideo && (
            <div
              className="pointer-events-none absolute z-[2] rounded-sm border border-wline bg-white/10 shadow-[0_0_0_2000px_rgba(0,0,0,0.18)]"
              /* intentional vignette — no token equivalent */
              style={{ left: lens.x, top: lens.y, width: lens.w, height: lens.h }}
            />
          )}

          {/* Wishlist */}
          <div className="absolute right-4 top-4 z-10">
            <WishlistButton productId={product.id} />
          </div>

          {/* Zoom hint */}
          {!currentIsVideo && (
            <div className="pointer-events-none absolute bottom-3 left-3 z-10 inline-flex items-center gap-1.5 rounded-xs bg-wink/75 px-2.5 py-1 text-xs text-white opacity-0 transition-opacity duration-200 group-hover:opacity-100">
              <ZoomIn className="size-3.5" aria-hidden="true" /> Hover to zoom
            </div>
          )}
        </div>

        {/* Magnifier panel — only shown at xl+ where there is sufficient horizontal space */}
        {lens && !currentIsVideo && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute left-[calc(100%+1.25rem)] top-0 z-40 hidden aspect-square overflow-hidden rounded-sm border border-wline bg-wcard shadow-lg xl:block xl:w-[360px] 2xl:w-[460px]"
            style={{
              backgroundImage: `url(${current.url})`,
              backgroundRepeat: 'no-repeat',
              backgroundSize: `${ZOOM * 100}%`,
              backgroundPosition: `${lens.px}% ${lens.py}%`,
            }}
          />
        )}

        {/* Dots (mobile) — interactive tab buttons for screen readers */}
        <div role="tablist" aria-label="Image navigation" className="mt-3 flex justify-center gap-1.5">
          {images.map((img, i) => (
            <button
              key={img.id}
              type="button"
              role="tab"
              aria-selected={i === active}
              aria-label={`Go to image ${i + 1}`}
              onClick={() => setActive(i)}
              className={cn(
                'h-1.5 rounded-full transition-all',
                i === active ? 'w-5 bg-wgreen' : 'w-1.5 bg-wline',
              )}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
