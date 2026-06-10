import { useRef, useState } from 'react';
import { ImagePlus, Star, Trash2, Loader2, Images } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils.js';
import { scaleIn, fadeIn, duration, ease } from '@/lib/motion.js';
import {
  useUploadProductImages,
  useDeleteProductImage,
  useSetPrimaryImage,
} from '@/features/admin/hooks.js';

const MAX_IMAGES = 8;
const MAX_SIZE_MB = 15; // mirrors the backend MAX_IMAGE_SIZE_MB

/**
 * Gallery manager for a product's images. Upload (multi-select from local
 * disk), delete, and pick a primary. Each action returns the updated product;
 * the local `images` state is synced from that response.
 *
 * Prop API is unchanged — presentational upgrades only.
 */
export function ProductImageManager({ productId, initialImages = [] }) {
  const [images, setImages] = useState(initialImages);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  const upload = useUploadProductImages();
  const removeImage = useDeleteProductImage();
  const setPrimary = useSetPrimaryImage();
  const busy = upload.isPending || removeImage.isPending || setPrimary.isPending;

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
          {images.map((img) => (
            <motion.div
              key={img.id}
              variants={scaleIn}
              initial="hidden"
              animate="show"
              exit={{ opacity: 0, scale: 0.9, transition: { duration: duration.fast, ease: ease.exit } }}
              className="group relative aspect-square overflow-hidden rounded-sm border border-line-subtle bg-bg-sunken"
            >
              <img
                src={img.url}
                alt=""
                loading="lazy"
                className="size-full object-cover transition-transform duration-300 group-hover:scale-105"
              />

              {/* Primary badge */}
              {img.is_primary && (
                <span className="absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[10px] font-medium text-ink-inverse shadow-glow-sm">
                  <Star className="size-2.5 fill-current" aria-hidden="true" />
                  Primary
                </span>
              )}

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
            select one or several at once.
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}
