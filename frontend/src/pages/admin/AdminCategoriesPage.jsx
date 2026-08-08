import { useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Tags,
  Plus,
  Trash2,
  Upload,
  X,
  ImageOff,
  FolderTree,
  CornerDownRight,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import {
  useCategories,
  useCreateCategory,
  useDeleteCategory,
  useUpdateCategory,
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

function CategoryRow({ category, isChild, childCount, parentOptions }) {
  const fileRef = useRef(null);
  const [confirming, setConfirming] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [removeError, setRemoveError] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [editingParent, setEditingParent] = useState(false);
  const [parentValue, setParentValue] = useState('');
  const [parentError, setParentError] = useState(null);

  const del = useDeleteCategory();
  const update = useUpdateCategory();
  const uploadImage = useUploadCategoryImage();
  const removeImage = useRemoveCategoryImage();

  const gradient = gradientFor(category.slug);
  const hasChildren = childCount > 0;
  const anyPending =
    del.isPending || update.isPending || uploadImage.isPending || removeImage.isPending;
  // Left inset that aligns row footers (errors, parent editor) with the text column
  const insetClass = isChild ? 'ml-[8rem]' : 'ml-[4.75rem]';

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

  async function handleDelete() {
    setDeleteError(null);
    try {
      await del.mutateAsync(category.id);
    } catch (err) {
      setDeleteError(
        err.response?.data?.error?.message || 'Could not delete the category.',
      );
      setConfirming(false);
    }
  }

  async function handleSaveParent() {
    setParentError(null);
    try {
      await update.mutateAsync({
        id: category.id,
        data: { parent_id: parentValue === '' ? null : Number(parentValue) },
      });
      setEditingParent(false);
    } catch (err) {
      setParentError(
        err.response?.data?.error?.message || 'Could not move the category.',
      );
    }
  }

  return (
    <motion.li variants={fadeUp} className="flex flex-col gap-1">
      <div className={cn('flex items-center gap-4 px-5 py-3.5', isChild && 'pl-9')}>
        {/* Connector for subcategories */}
        {isChild && (
          <CornerDownRight
            className="size-4 shrink-0 text-ink-tertiary/70"
            aria-hidden="true"
          />
        )}

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
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-ink-primary">{category.name}</p>
            {hasChildren && (
              <span className="inline-flex shrink-0 items-center rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-accent">
                {childCount} subcategor{childCount === 1 ? 'y' : 'ies'}
              </span>
            )}
            {isChild && (
              <span className="inline-flex shrink-0 items-center rounded-full border border-line-subtle bg-bg-sunken px-2 py-0.5 text-[11px] font-medium text-ink-tertiary">
                Subcategory
              </span>
            )}
          </div>
          <p className="text-xs text-ink-tertiary">/{category.slug}</p>
        </div>

        {/* Parent + image actions */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label={`Change parent for ${category.name}`}
            aria-expanded={editingParent}
            disabled={anyPending}
            onClick={() => {
              setParentError(null);
              setParentValue(
                category.parent_id != null ? String(category.parent_id) : '',
              );
              setEditingParent((v) => !v);
            }}
            className={cn(
              'grid size-8 place-items-center rounded-sm text-ink-tertiary',
              'transition-colors hover:bg-accent/10 hover:text-accent',
              'focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40',
              editingParent && 'bg-accent/10 text-accent',
            )}
          >
            <FolderTree className="size-4" aria-hidden="true" />
          </button>
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
              onClick={handleDelete}
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

      {/* Inline parent editor */}
      {editingParent && (
        <div className={cn('flex items-start gap-2 pb-3 pr-5', insetClass)}>
          <div className="w-64">
            <Select
              aria-label={`Parent category for ${category.name}`}
              value={parentValue}
              onChange={(e) => setParentValue(e.target.value)}
              disabled={hasChildren || update.isPending}
              error={parentError}
              helper={
                hasChildren
                  ? 'Has subcategories — move them first'
                  : 'Choose a parent, or none for top level.'
              }
            >
              <option value="">None — top level</option>
              {parentOptions.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </div>
          <Button
            size="sm"
            loading={update.isPending}
            disabled={hasChildren}
            onClick={handleSaveParent}
          >
            Save
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={update.isPending}
            onClick={() => setEditingParent(false)}
          >
            Cancel
          </Button>
        </div>
      )}

      {/* Per-row errors */}
      {uploadError && (
        <p className={cn('flex items-center gap-1.5 pb-1 text-xs text-danger', insetClass)}>
          <X className="size-3 shrink-0" aria-hidden="true" />
          {uploadError}
        </p>
      )}
      {removeError && (
        <p className={cn('flex items-center gap-1.5 pb-1 text-xs text-danger', insetClass)}>
          <X className="size-3 shrink-0" aria-hidden="true" />
          {removeError}
        </p>
      )}
      {deleteError && (
        <p className={cn('flex items-center gap-1.5 pb-1 text-xs text-danger', insetClass)}>
          <X className="size-3 shrink-0" aria-hidden="true" />
          {deleteError}
        </p>
      )}
    </motion.li>
  );
}

export default function AdminCategoriesPage() {
  const { data: categories = [], isLoading } = useCategories();
  const create = useCreateCategory();
  const [name, setName] = useState('');
  const [parentId, setParentId] = useState('');
  const [error, setError] = useState(null);

  const topLevel = useMemo(
    () => categories.filter((c) => c.parent_id == null),
    [categories],
  );

  // One-level tree flattened for rendering: each top-level category followed by
  // its children. A child whose parent is missing — or is itself a subcategory
  // (possible via a concurrent-edit race) — renders as top-level so no row can
  // ever disappear from the list.
  const rows = useMemo(() => {
    const rootIds = new Set(
      categories.filter((c) => c.parent_id == null).map((c) => c.id),
    );
    const childrenOf = new Map();
    const roots = [];
    for (const c of categories) {
      if (c.parent_id != null && rootIds.has(c.parent_id)) {
        const siblings = childrenOf.get(c.parent_id) ?? [];
        siblings.push(c);
        childrenOf.set(c.parent_id, siblings);
      } else {
        roots.push(c);
      }
    }
    return roots.flatMap((root) => {
      const children = childrenOf.get(root.id) ?? [];
      return [
        { category: root, isChild: false, childCount: children.length },
        ...children.map((child) => ({ category: child, isChild: true, childCount: 0 })),
      ];
    });
  }, [categories]);

  async function handleAdd(e) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError('Enter a category name.');
      return;
    }
    try {
      await create.mutateAsync({
        name: name.trim(),
        ...(parentId !== '' && { parent_id: Number(parentId) }),
      });
      setName('');
      setParentId('');
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
      <form onSubmit={handleAdd} className="mb-6 flex flex-wrap items-start gap-3">
        <div className="w-72">
          <Input
            placeholder="New category name — e.g. Accessories"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={error}
          />
        </div>
        <div className="w-56">
          <Select
            aria-label="Parent category"
            value={parentId}
            onChange={(e) => setParentId(e.target.value)}
            helper="Parent category"
          >
            <option value="">None — top level</option>
            {topLevel.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
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
            {rows.map(({ category, isChild, childCount }) => (
              <CategoryRow
                key={category.id}
                category={category}
                isChild={isChild}
                childCount={childCount}
                parentOptions={topLevel.filter((p) => p.id !== category.id)}
              />
            ))}
          </motion.ul>
        </Card>
      )}

      <p className="mt-4 text-xs text-ink-tertiary">
        Deleting a category leaves its products uncategorized — they remain visible in the
        catalog. A category with subcategories can&apos;t be deleted until they are moved or
        removed.
      </p>
    </AdminPage>
  );
}
