import { useEffect, useRef, useState } from 'react';
import {
  ImagePlus,
  Star,
  Trash2,
  Loader2,
  Images,
  GripVertical,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { scaleIn, fadeIn, duration, ease } from '@/lib/motion.js';
import {
  useUploadProductImages,
  useDeleteProductImage,
  useSetPrimaryImage,
  useReorderProductImages,
} from '@/features/admin/hooks.js';

const MAX_IMAGES = 8;
const MAX_SIZE_MB = 15; // mirrors the backend MAX_IMAGE_SIZE_MB

/** Move `id` to `toIndex`, returning a new array (input untouched). */
export function moveTo(list, id, toIndex) {
  const from = list.findIndex((img) => img.id === id);
  if (from === -1 || toIndex < 0 || toIndex >= list.length || from === toIndex) {
    return list;
  }
  const next = list.slice();
  const [moved] = next.splice(from, 1);
  next.splice(toIndex, 0, moved);
  return next;
}

export const sameOrder = (a, b) =>
  a.length === b.length && a.every((id, i) => id === b[i]);

/** Re-sort `list` into `order` (a list of ids). Ids no longer present are
 *  dropped, and anything `order` doesn't mention keeps its relative place at
 *  the end — so a rollback can never lose a tile. */
export function restoreOrder(list, order) {
  const known = new Set(order);
  return [
    ...order.map((id) => list.find((img) => img.id === id)).filter(Boolean),
    ...list.filter((img) => !known.has(img.id)),
  ];
}

/**
 * Gallery manager for a product's images. Upload (multi-select from local
 * disk), delete, pick a primary, and drag tiles to set the gallery order.
 * Each action returns the updated product; the local `images` state is synced
 * from that response.
 *
 * Ordering is optimistic: the grid reorders live under the cursor and only
 * then persists, reverting to the pre-drag order if the server refuses.
 *
 * Prop API is unchanged — presentational upgrades only.
 */
export function ProductImageManager({ productId, initialImages = [] }) {
  const [images, setImages] = useState(initialImages);
  const [error, setError] = useState(null);
  const [dragId, setDragId] = useState(null);
  const fileRef = useRef(null);
  // Order captured at drag start, so a rejected reorder can be undone.
  const preDragOrder = useRef(null);
  // Mirrors `images` for handlers that run after the last render (dragend).
  const imagesRef = useRef(images);
  useEffect(() => {
    imagesRef.current = images;
  }, [images]);

  const upload = useUploadProductImages();
  const removeImage = useDeleteProductImage();
  const setPrimary = useSetPrimaryImage();
  const reorder = useReorderProductImages();
  const busy =
    upload.isPending ||
    removeImage.isPending ||
    setPrimary.isPending ||
    reorder.isPending;

  async function handleFiles(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (!files.length) return;
    setError(null);
    if (images.length + files.length > MAX_IMAGES) {
      setError(`A product can have at most ${MAX_IMAGES} images.`);
      return;
    }
    const tooBig = files.find((f) => f.size > MAX_SIZE_MB * 1024 * 1024);
    if (tooBig) {
      setError(
        `"${tooBig.name}" is too large — each image must be under ${MAX_SIZE_MB} MB.`,
      );
      return;
    }
    try {
      const product = await upload.mutateAsync({ id: productId, files });
      setImages(product.images);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Upload failed. Try again.');
    }
  }

  async function handleDelete(imageId) {
    setError(null);
    try {
      const product = await removeImage.mutateAsync({ id: productId, imageId });
      setImages(product.images);
    } catch (err) {
      setError(
        err.response?.data?.error?.message || 'Could not delete the image.',
      );
    }
  }

  async function handlePrimary(imageId) {
    setError(null);
    try {
      const product = await setPrimary.mutateAsync({ id: productId, imageId });
      setImages(product.images);
    } catch (err) {
      setError(
        err.response?.data?.error?.message ||
          'Could not set the primary image.',
      );
    }
  }

  /** Persist a new order; `before` is the order to fall back to on failure. */
  async function persistOrder(before, after) {
    if (sameOrder(before, after)) return;
    setError(null);
    try {
      const product = await reorder.mutateAsync({
        id: productId,
        imageIds: after,
      });
      setImages(product.images);
    } catch (err) {
      setImages((prev) => restoreOrder(prev, before));
      setError(
        err.response?.data?.error?.message ||
          'Could not save the new image order.',
      );
    }
  }

  function handleDragStart(e, id) {
    if (busy) {
      e.preventDefault();
      return;
    }
    preDragOrder.current = images.map((img) => img.id);
    setDragId(id);
    e.dataTransfer.effectAllowed = 'move';
    // Firefox refuses to start a drag unless some data is attached.
    e.dataTransfer.setData('text/plain', String(id));
  }

  function handleDragOver(e, overId) {
    if (dragId === null) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (overId === dragId) return;
    setImages((prev) =>
      moveTo(
        prev,
        dragId,
        prev.findIndex((img) => img.id === overId),
      ),
    );
  }

  function handleDragEnd(e) {
    const before = preDragOrder.current;
    preDragOrder.current = null;
    setDragId(null);
    if (!before) return;
    // Esc, or a release outside any tile, ends the drag with no drop effect —
    // that is a cancel, so the live preview has to be rolled back, not saved.
    if (e.dataTransfer?.dropEffect === 'none') {
      setImages((prev) => restoreOrder(prev, before));
      return;
    }
    persistOrder(
      before,
      imagesRef.current.map((img) => img.id),
    );
  }

  /** Keyboard equivalent of a drag: Alt/Ctrl/Cmd + ←/→ on a focused tile. */
  function handleTileKeyDown(e, id) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (!(e.altKey || e.ctrlKey || e.metaKey) || busy) return;
    e.preventDefault();
    const before = images.map((img) => img.id);
    const next = moveTo(
      images,
      id,
      before.indexOf(id) + (e.key === 'ArrowLeft' ? -1 : 1),
    );
    if (next === images) return;
    setImages(next);
    persistOrder(
      before,
      next.map((img) => img.id),
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Section label */}
      <div className="flex items-center gap-1.5">
        <Images className="size-3.5 text-ink-tertiary" aria-hidden="true" />
        <span className="text-sm font-medium text-ink-secondary">
          Images
          <span className="ml-1.5 nums text-xs text-ink-tertiary">
            {images.length}/{MAX_IMAGES}
          </span>
        </span>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
        <AnimatePresence initial={false}>
          {images.map((img, index) => (
            <motion.div
              key={img.id}
              layout
              variants={scaleIn}
              initial="hidden"
              animate="show"
              exit={{ opacity: 0, scale: 0.9, transition: { duration: duration.fast, ease: ease.exit } }}
              draggable={!busy}
              onDragStart={(e) => handleDragStart(e, img.id)}
              onDragOver={(e) => handleDragOver(e, img.id)}
              onDrop={(e) => e.preventDefault()}
              onDragEnd={handleDragEnd}
              onKeyDown={(e) => handleTileKeyDown(e, img.id)}
              tabIndex={0}
              // `group`, not `button` — the tile holds its own buttons, and a
              // button must not contain interactive children.
              role="group"
              aria-roledescription="Draggable image"
              aria-label={`Image ${index + 1} of ${images.length}. Hold Alt and press the left or right arrow key to move it.`}
              title="Drag to reorder"
              className={cn(
                'group relative aspect-square overflow-hidden rounded-sm border bg-bg-sunken',
                'cursor-grab focus-visible:focus-ring active:cursor-grabbing',
                dragId === img.id
                  ? 'border-accent opacity-40'
                  : 'border-line-subtle',
                busy && 'cursor-default',
              )}
            >
              <img
                src={img.url}
                alt=""
                loading="lazy"
                draggable={false}
                className="pointer-events-none size-full object-cover transition-transform duration-300 group-hover:scale-105"
              />

              {/* Order + primary badges */}
              <div className="pointer-events-none absolute left-1.5 top-1.5 flex items-center gap-1">
                <span className="nums grid size-5 place-items-center rounded-full bg-black/60 text-[10px] font-medium text-white">
                  {index + 1}
                </span>
                {img.is_primary && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[10px] font-medium text-ink-inverse shadow-glow-sm">
                    <Star className="size-2.5 fill-current" aria-hidden="true" />
                    Primary
                  </span>
                )}
              </div>

              {/* Drag affordance */}
              <GripVertical
                className={cn(
                  'pointer-events-none absolute right-1 top-1.5 size-4 text-white drop-shadow',
                  'opacity-0 transition-opacity duration-200',
                  'group-hover:opacity-80 group-focus-within:opacity-80',
                )}
                aria-hidden="true"
              />

              {/* Hover action bar */}
              <div
                className={cn(
                  'absolute inset-x-0 bottom-0 flex justify-between gap-1',
                  'bg-gradient-to-t from-black/75 to-transparent p-1.5',
                  'opacity-0 transition-opacity duration-200',
                  'group-hover:opacity-100 group-focus-within:opacity-100',
                )}
              >
                {!img.is_primary && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handlePrimary(img.id)}
                    className={cn(
                      'rounded-sm bg-white/15 px-1.5 py-1 text-[11px] font-medium text-white',
                      'transition-colors hover:bg-white/28 focus-visible:focus-ring',
                      'disabled:opacity-50',
                    )}
                  >
                    Set primary
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => handleDelete(img.id)}
                  aria-label="Delete image"
                  className={cn(
                    'ml-auto grid size-7 place-items-center rounded-sm',
                    'bg-white/15 text-white',
                    'transition-colors hover:bg-danger focus-visible:focus-ring',
                    'disabled:opacity-50',
                  )}
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                </button>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* Upload slot */}
        {images.length < MAX_IMAGES && (
          <motion.button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            whileHover={{ borderColor: 'var(--accent)', scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            transition={{ duration: duration.fast, ease: ease.standard }}
            className={cn(
              'flex aspect-square flex-col items-center justify-center gap-1.5 rounded-sm',
              'border border-dashed border-line-strong',
              'text-ink-tertiary transition-colors',
              'hover:text-accent focus-visible:focus-ring',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
          >
            {upload.isPending ? (
              <Loader2
                className="size-5 animate-spin text-accent"
                aria-hidden="true"
              />
            ) : (
              <ImagePlus className="size-5" aria-hidden="true" />
            )}
            <span className="text-[11px] font-medium">
              {upload.isPending ? 'Uploading…' : 'Add images'}
            </span>
          </motion.button>
        )}
      </div>

      {/* Hidden file input */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        multiple
        onChange={handleFiles}
        className="hidden"
      />

      {/* Status / error line */}
      <AnimatePresence mode="wait">
        {error ? (
          <motion.p
            key="error"
            variants={fadeIn}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="text-xs text-danger"
            role="alert"
          >
            {error}
          </motion.p>
        ) : (
          <motion.p
            key="hint"
            variants={fadeIn}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="text-xs text-ink-tertiary"
          >
            {images.length}/{MAX_IMAGES} images · up to {MAX_SIZE_MB} MB each ·
            select one or several at once
            {images.length > 1 && ' · drag a tile to set the gallery order'}
            {reorder.isPending && ' · saving order…'}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}
