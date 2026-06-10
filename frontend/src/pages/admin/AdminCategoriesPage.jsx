import { useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Tags, Plus, Trash2, Upload, X, ImageOff } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import {
  useCategories,
  useCreateCategory,
  useDeleteCategory,
  useUploadCategoryImage,
  useRemoveCategoryImage,
} from '@/features/categories/hooks.js';
import { listStagger, fadeUp } from '@/lib/motion.js';

const MAX_IMAGE_SIZE_MB = 15;

// Deterministic gradient from a slug — mirrors CategoryCircles
const GRADIENTS = [
  'from-rose-300 to-amber-200',
  'from-indigo-300 to-sky-200',
  'from-emerald-300 to-lime-200',
  'from-fuchsia-300 to-pink-200',
  'from-amber-300 to-yellow-200',
  'from-teal-300 to-cyan-200',
  'from-violet-300 to-indigo-200',
  'from-orange-300 to-rose-200',
];

function gradientFor(slug) {
  let h = 0;
  for (let i = 0; i < slug.length; i += 1) {
    h = (h * 31 + slug.charCodeAt(i)) >>> 0;
  }
  return GRADIENTS[h % GRADIENTS.length];
}

function CategoryRow({ category }) {
  const fileRef = useRef(null);
  const [confirming, setConfirming] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [removeError, setRemoveError] = useState(null);

  const del = useDeleteCategory();
  const uploadImage = useUploadCategoryImage();
  const removeImage = useRemoveCategoryImage();

  const gradient = gradientFor(category.slug);
  const anyPending = del.isPending || uploadImage.isPending || removeImage.isPending;

  async function handleFileChange(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setUploadError(null);

    if (!file.type.startsWith('image/')) {
      setUploadError('Only image files are accepted.');
      return;
    }
    if (file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024) {
      setUploadError(`Image must be under ${MAX_IMAGE_SIZE_MB} MB.`);
      return;
    }

    try {
      await uploadImage.mutateAsync({ id: category.id, file });
    } catch (err) {
      setUploadError(
        err.response?.data?.error?.message || 'Upload failed. Please try again.',
      );
    }
  }

  async function handleRemoveImage() {
    setRemoveError(null);
    try {
      await removeImage.mutateAsync(category.id);
    } catch (err) {
      setRemoveError(
        err.response?.data?.error?.message || 'Could not remove the image.',
      );
    }
  }

  return (
    <motion.li variants={fadeUp} className="flex flex-col gap-1">
      <div className="flex items-center gap-4 px-5 py-3.5">
        {/* Thumbnail */}
        <span
          className={cn(
            'relative grid size-11 shrink-0 place-items-center overflow-hidden rounded-lg',
            'border border-line-subtle bg-gradient-to-br shadow-sm',
            gradient,
          )}
          aria-hidden="true"
        >
          {category.image_url ? (
            <img
              src={category.image_url}
              alt=""
              loading="lazy"
              className="size-full object-cover"
            />
          ) : (
            <span className="text-base font-bold uppercase text-white/95 drop-shadow-sm">
              {category.name.trim().charAt(0)}
            </span>
          )}
        </span>

        {/* Name + slug */}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink-primary">{category.name}</p>
          <p className="text-xs text-ink-tertiary">/{category.slug}</p>
        </div>

        {/* Image actions */}
        <div className="flex items-center gap-1">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="sr-only"
            aria-label={`Upload image for ${category.name}`}
            onChange={handleFileChange}
            disabled={anyPending}
          />

          <button
            type="button"
            aria-label={
              category.image_url
                ? `Replace image for ${category.name}`
                : `Upload image for ${category.name}`
            }
            disabled={anyPending}
            onClick={() => {
              setUploadError(null);
              fileRef.current?.click();
            }}
            className={cn(
              'grid size-8 place-items-center rounded-sm text-ink-tertiary',
              'transition-colors hover:bg-accent/10 hover:text-accent',
              'focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40',
              uploadImage.isPending && 'pointer-events-none opacity-40',
            )}
          >
            {uploadImage.isPending ? (
              <span
                className="size-4 animate-spin rounded-full border-2 border-accent border-t-transparent"
                aria-hidden="true"
              />
            ) : (
              <Upload className="size-4" aria-hidden="true" />
            )}
          </button>

          {category.image_url && (
            <button
              type="button"
              aria-label={`Remove image for ${category.name}`}
              disabled={anyPending}
              onClick={handleRemoveImage}
              className={cn(
                'grid size-8 place-items-center rounded-sm text-ink-tertiary',
                'transition-colors hover:bg-warning/10 hover:text-warning',
                'focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40',
                removeImage.isPending && 'pointer-events-none opacity-40',
              )}
            >
              {removeImage.isPending ? (
                <span
                  className="size-4 animate-spin rounded-full border-2 border-warning border-t-transparent"
                  aria-hidden="true"
                />
              ) : (
                <ImageOff className="size-4" aria-hidden="true" />
              )}
            </button>
          )}
        </div>

        {/* Delete */}
        {confirming ? (
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-secondary">Delete?</span>
            <Button
              variant="destructive"
              size="sm"
              loading={del.isPending}
              onClick={() => del.mutate(category.id)}
            >
              Confirm
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={del.isPending}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <button
            type="button"
            aria-label={`Delete ${category.name}`}
            disabled={anyPending}
            onClick={() => setConfirming(true)}
            className="grid size-8 place-items-center rounded-sm text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40"
          >
            <Trash2 className="size-4" aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Per-row errors */}
      {uploadError && (
        <p className="ml-[4.75rem] flex items-center gap-1.5 pb-1 text-xs text-danger">
          <X className="size-3 shrink-0" aria-hidden="true" />
          {uploadError}
        </p>
      )}
      {removeError && (
        <p className="ml-[4.75rem] flex items-center gap-1.5 pb-1 text-xs text-danger">
          <X className="size-3 shrink-0" aria-hidden="true" />
          {removeError}
        </p>
      )}
    </motion.li>
  );
}

export default function AdminCategoriesPage() {
  const { data: categories = [], isLoading } = useCategories();
  const create = useCreateCategory();
  const [name, setName] = useState('');
  const [error, setError] = useState(null);

  async function handleAdd(e) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError('Enter a category name.');
      return;
    }
    try {
      await create.mutateAsync({ name: name.trim() });
      setName('');
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not create the category.');
    }
  }

  return (
    <AdminPage
      title="Categories"
      description="Group products for browsing and filtering. Each category can have an image shown in the storefront circles."
    >
      {/* Create form */}
      <form onSubmit={handleAdd} className="mb-6 flex items-start gap-3">
        <div className="w-72">
          <Input
            placeholder="New category name — e.g. Accessories"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={error}
          />
        </div>
        <Button type="submit" loading={create.isPending}>
          <Plus className="size-4" aria-hidden="true" />
          Add category
        </Button>
      </form>

      {isLoading ? (
        <div className="overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated shadow-md">
          <div className="border-b border-line-subtle bg-bg-sunken px-5 py-3">
            <Skeleton variant="text" lines={1} className="w-24" />
          </div>
          <div className="flex flex-col divide-y divide-line-subtle">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                <Skeleton variant="circle" className="size-11 shrink-0" />
                <div className="flex-1">
                  <Skeleton variant="text" lines={2} />
                </div>
                <Skeleton className="h-7 w-20" />
              </div>
            ))}
          </div>
        </div>
      ) : categories.length === 0 ? (
        <EmptyState
          icon={Tags}
          title="No categories yet"
          description="Add your first category with the form above."
        />
      ) : (
        <Card className="shadow-md">
          <CardHeader
            title={`${categories.length} categor${categories.length === 1 ? 'y' : 'ies'}`}
          />
          <motion.ul
            variants={listStagger(0.04)}
            initial="hidden"
            animate="show"
            className="divide-y divide-line-subtle"
          >
            {categories.map((c) => (
              <CategoryRow key={c.id} category={c} />
            ))}
          </motion.ul>
        </Card>
      )}

      <p className="mt-4 text-xs text-ink-tertiary">
        Deleting a category leaves its products uncategorized — they remain visible in the catalog.
      </p>
    </AdminPage>
  );
}
