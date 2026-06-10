import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  Plus,
  Pencil,
  Trash2,
  Star,
  Search,
  X,
  CheckCircle2,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
  Filter,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, scaleIn, listStagger } from '@/lib/motion.js';
import { StarRating } from '@/features/reviews/StarRating.jsx';
import {
  useAdminReviews,
  useAdminCreateReview,
  useAdminUpdateReview,
  useAdminDeleteReview,
} from '@/features/reviews/hooks.js';
import { useProducts } from '@/features/products/hooks.js';

const PAGE_SIZE = 25;

const EMPTY_FORM = {
  product_id: null,
  productName: '',
  rating: 5,
  author_name: '',
  title: '',
  body: '',
  is_verified_purchase: false,
  is_approved: true,
};

function useDebounced(value, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// ---- Product picker ----

function ProductPicker({ value, valueName, onChange, disabled, error }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const debouncedQ = useDebounced(q, 200);
  const { data, isLoading } = useProducts({ q: debouncedQ || undefined, page: 1, page_size: 8 });
  const items = data?.items || [];

  if (value) {
    return (
      <div className="flex flex-col gap-1.5">
        <label className="text-sm font-medium text-ink-secondary">Product</label>
        <div className="flex items-center gap-2 rounded-lg border border-line-subtle bg-bg-sunken px-3 py-2.5">
          <span className="flex-1 truncate text-sm text-ink-primary">{valueName}</span>
          <span className="font-mono text-xs text-ink-tertiary">#{value}</span>
          <button
            type="button"
            aria-label="Clear product"
            disabled={disabled}
            onClick={() => onChange(null, '')}
            className="grid size-7 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring disabled:opacity-50"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <p className="min-h-[1.25rem] text-xs text-ink-tertiary">
          Click × to pick a different product.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-ink-secondary">Product</label>
      <div className="relative">
        <Input
          icon={Search}
          placeholder="Search by name…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          error={error}
        />
        {open && q.trim().length > 0 && (
          <motion.ul
            variants={scaleIn}
            initial="hidden"
            animate="show"
            className="absolute z-10 mt-1 max-h-72 w-full overflow-y-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-lg"
          >
            {isLoading ? (
              <li className="px-3 py-2 text-xs text-ink-tertiary">Searching…</li>
            ) : items.length === 0 ? (
              <li className="px-3 py-2 text-xs text-ink-tertiary">No matches.</li>
            ) : (
              items.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onChange(p.id, p.name);
                      setOpen(false);
                      setQ('');
                    }}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm transition-colors hover:bg-fill focus-visible:focus-ring"
                  >
                    <span className="truncate text-ink-primary">{p.name}</span>
                    <span className="font-mono text-xs text-ink-tertiary">{p.sku}</span>
                  </button>
                </li>
              ))
            )}
          </motion.ul>
        )}
      </div>
    </div>
  );
}

// ---- Create / edit form ----

function ReviewForm({ initial, mode, onCancel, onSaved }) {
  const [form, setForm] = useState(initial || EMPTY_FORM);
  const [error, setError] = useState(null);
  const create = useAdminCreateReview();
  const update = useAdminUpdateReview();

  useEffect(() => {
    setForm(initial || EMPTY_FORM);
    setError(null);
  }, [initial]);

  const pending = create.isPending || update.isPending;
  const isEdit = mode === 'edit';

  function set(k) {
    return (e) => {
      const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      setForm((f) => ({ ...f, [k]: v }));
    };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (!isEdit && !form.product_id) {
      setError('Pick a product first.');
      return;
    }
    if (!form.rating || form.rating < 1 || form.rating > 5) {
      setError('Rating must be 1–5.');
      return;
    }
    if (!isEdit && !form.author_name.trim() && !form.user_id) {
      setError('Author name is required when no user account is linked.');
      return;
    }

    const payload = {
      rating: Number(form.rating),
      author_name: form.author_name.trim() || null,
      title: form.title.trim() || null,
      body: form.body.trim() || null,
      is_verified_purchase: !!form.is_verified_purchase,
      is_approved: !!form.is_approved,
    };

    try {
      if (isEdit && initial?.id) {
        await update.mutateAsync({ reviewId: initial.id, data: payload });
      } else {
        await create.mutateAsync({ ...payload, product_id: form.product_id });
      }
      onSaved?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not save the review.');
    }
  }

  return (
    <motion.form
      variants={scaleIn}
      initial="hidden"
      animate="show"
      onSubmit={handleSubmit}
      className="mb-6 rounded-xl border border-line-subtle bg-bg-elevated p-6 shadow-md"
    >
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-full bg-warning/12 text-warning">
            <Star className="size-5" aria-hidden="true" />
          </div>
          <h2 className="text-h3 font-semibold tracking-tight text-ink-primary">
            {isEdit ? 'Edit review' : 'New review'}
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={onCancel}
          className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {!isEdit ? (
          <ProductPicker
            value={form.product_id}
            valueName={form.productName}
            onChange={(id, name) =>
              setForm((f) => ({ ...f, product_id: id, productName: name }))
            }
            disabled={pending}
          />
        ) : (
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-ink-secondary">Product</label>
            <div className="flex h-11 items-center rounded-lg border border-line-subtle bg-bg-sunken px-3 text-sm text-ink-primary">
              {form.productName}
              <span className="ml-auto font-mono text-xs text-ink-tertiary">
                #{form.product_id}
              </span>
            </div>
            <p className="min-h-[1.25rem] text-xs text-ink-tertiary">
              Product cannot be changed after creation.
            </p>
          </div>
        )}

        <Input
          label="Author name"
          placeholder="Priya R."
          value={form.author_name}
          onChange={set('author_name')}
          helper="Shown publicly. Used when no user account is linked."
        />

        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-ink-secondary">Rating</label>
          <div className="flex h-11 items-center">
            <StarRating
              value={Number(form.rating) || 0}
              onChange={(n) => setForm((f) => ({ ...f, rating: n }))}
              size="lg"
            />
          </div>
          <p className="min-h-[1.25rem] text-xs text-ink-tertiary">
            Tap a star (1–5).
          </p>
        </div>

        <Input
          label="Headline (optional)"
          placeholder="Sums up the review"
          value={form.title}
          onChange={set('title')}
          maxLength={160}
        />
      </div>

      <div className="mt-1">
        <Textarea
          label="Body (optional)"
          placeholder="Detailed review body"
          value={form.body}
          onChange={set('body')}
          rows={4}
          maxRows={8}
        />
      </div>

      {/* Flags */}
      <div className="mt-4 flex flex-wrap gap-3">
        <label
          className={cn(
            'flex cursor-pointer items-center gap-2.5 rounded-lg border px-4 py-2.5 text-sm transition-all duration-150',
            form.is_verified_purchase
              ? 'border-success/40 bg-success/8 text-success'
              : 'border-line-subtle bg-bg-sunken text-ink-secondary hover:bg-fill',
          )}
        >
          <input
            type="checkbox"
            checked={form.is_verified_purchase}
            onChange={set('is_verified_purchase')}
            className="sr-only"
          />
          <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
          Verified purchase
        </label>
        <label
          className={cn(
            'flex cursor-pointer items-center gap-2.5 rounded-lg border px-4 py-2.5 text-sm transition-all duration-150',
            form.is_approved
              ? 'border-accent/40 bg-accent/8 text-accent'
              : 'border-line-subtle bg-bg-sunken text-ink-secondary hover:bg-fill',
          )}
        >
          <input
            type="checkbox"
            checked={form.is_approved}
            onChange={set('is_approved')}
            className="sr-only"
          />
          <Star className="size-4 shrink-0" aria-hidden="true" />
          Approved — visible publicly
        </label>
      </div>

      {error && (
        <p className="mt-4 rounded-lg border border-danger/30 bg-danger/8 px-3 py-2 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="mt-6 flex justify-end gap-3">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" loading={pending}>
          {isEdit ? 'Save changes' : 'Create review'}
        </Button>
      </div>
    </motion.form>
  );
}

// ---- List row ----

function ReviewRow({ review, onEdit }) {
  const update = useAdminUpdateReview();
  const del = useAdminDeleteReview();
  const [confirming, setConfirming] = useState(false);

  const pending = update.isPending || del.isPending;

  function toggleApproved() {
    update.mutate({
      reviewId: review.id,
      data: { is_approved: !review.is_approved },
    });
  }

  return (
    <motion.tr variants={fadeUp} className="group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/60">
      <td className="px-5 py-3.5">
        <Link
          to={`/products/${review.product_id}`}
          className="font-mono text-xs font-medium text-accent transition-colors hover:text-accent-hover hover:underline focus-visible:focus-ring"
        >
          #{review.product_id}
        </Link>
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2">
          <StarRating value={review.rating} size="sm" />
          <span className="nums text-xs tabular-nums text-ink-tertiary">{review.rating}</span>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <p className="text-sm font-medium text-ink-primary">{review.author_display}</p>
        {review.is_verified_purchase && (
          <span className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-success">
            <CheckCircle2 className="size-3" aria-hidden="true" /> Verified
          </span>
        )}
      </td>
      <td className="max-w-xs px-5 py-3.5">
        {review.title && (
          <p className="line-clamp-1 text-sm font-medium text-ink-primary">
            {review.title}
          </p>
        )}
        {review.body && (
          <p className="line-clamp-2 text-xs text-ink-secondary">{review.body}</p>
        )}
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            role="switch"
            aria-checked={review.is_approved}
            aria-label={review.is_approved ? 'Unapprove' : 'Approve'}
            disabled={pending}
            onClick={toggleApproved}
            className={cn(
              'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200',
              'focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40',
              review.is_approved ? 'bg-accent' : 'bg-fill-strong',
            )}
          >
            <span
              className={cn(
                'pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
                review.is_approved ? 'translate-x-4' : 'translate-x-0',
              )}
            />
          </button>
          <Badge tone={review.is_approved ? 'success' : 'neutral'} size="sm">
            {review.is_approved ? 'Approved' : 'Hidden'}
          </Badge>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <div className="flex items-center justify-end gap-1">
          {confirming ? (
            <>
              <Button
                variant="destructive"
                size="sm"
                loading={del.isPending}
                onClick={() =>
                  del.mutate(
                    { reviewId: review.id, productId: review.product_id },
                    { onSuccess: () => setConfirming(false) },
                  )
                }
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
            </>
          ) : (
            <>
              <button
                type="button"
                aria-label="Edit review"
                disabled={pending}
                onClick={() => onEdit(review)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
              >
                <Pencil className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Delete review"
                disabled={pending}
                onClick={() => setConfirming(true)}
                className="grid size-9 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
              >
                <Trash2 className="size-4" />
              </button>
            </>
          )}
        </div>
      </td>
    </motion.tr>
  );
}

// ---- Page ----

export default function AdminReviewsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebounced(search, 250);
  const [ratingFilter, setRatingFilter] = useState('');
  const [approvedFilter, setApprovedFilter] = useState('');
  const [editing, setEditing] = useState(null);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, ratingFilter, approvedFilter]);

  const queryOpts = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      rating: ratingFilter ? Number(ratingFilter) : undefined,
      approved:
        approvedFilter === '' ? undefined : approvedFilter === 'true',
      page,
      page_size: PAGE_SIZE,
    }),
    [debouncedSearch, ratingFilter, approvedFilter, page],
  );

  const { data, isLoading, isError, refetch } = useAdminReviews(queryOpts);
  const items = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const isFormOpen = editing !== null;
  const formInitial =
    editing && editing !== 'new'
      ? {
          id: editing.id,
          product_id: editing.product_id,
          productName: `Product #${editing.product_id}`,
          rating: editing.rating,
          author_name: editing.author_display || '',
          title: editing.title || '',
          body: editing.body || '',
          is_verified_purchase: editing.is_verified_purchase,
          is_approved: editing.is_approved,
        }
      : null;

  const hasFilters = !!(debouncedSearch || ratingFilter || approvedFilter);

  return (
    <AdminPage
      title="Reviews"
      description={
        isLoading
          ? 'Loading…'
          : `${total} review${total === 1 ? '' : 's'} — entered ratings show up on the product detail page.`
      }
      action={
        !isFormOpen && (
          <Button onClick={() => setEditing('new')}>
            <Plus className="size-4" aria-hidden="true" />
            New review
          </Button>
        )
      }
    >
      {isFormOpen && (
        <ReviewForm
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={formInitial}
          onCancel={() => setEditing(null)}
          onSaved={() => setEditing(null)}
        />
      )}

      {/* Filters bar */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex-1">
          <Input
            icon={Search}
            placeholder="Search title, body, or author…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Filter className="size-4 text-ink-tertiary" aria-hidden="true" />
          <Select
            value={ratingFilter}
            onChange={(e) => setRatingFilter(e.target.value)}
            className="w-[130px]"
          >
            <option value="">All ratings</option>
            <option value="5">★ 5 only</option>
            <option value="4">★ 4 only</option>
            <option value="3">★ 3 only</option>
            <option value="2">★ 2 only</option>
            <option value="1">★ 1 only</option>
          </Select>
          <Select
            value={approvedFilter}
            onChange={(e) => setApprovedFilter(e.target.value)}
            className="w-[150px]"
          >
            <option value="">All statuses</option>
            <option value="true">Approved only</option>
            <option value="false">Unapproved only</option>
          </Select>
          {hasFilters && (
            <button
              type="button"
              onClick={() => {
                setSearch('');
                setRatingFilter('');
                setApprovedFilter('');
              }}
              className="text-xs text-ink-tertiary underline hover:text-ink-secondary focus-visible:focus-ring"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {isError ? (
        <EmptyState
          icon={Star}
          iconTone="danger"
          title="Couldn't load reviews"
          description="Something went wrong. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated shadow-md">
          <div className="border-b border-line-subtle bg-bg-sunken/60 px-5 py-3">
            <Skeleton variant="text" lines={1} className="w-32" />
          </div>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 border-t border-line-subtle px-5 py-4">
              <Skeleton className="h-4 w-12 rounded" />
              <Skeleton className="h-4 w-24 rounded" />
              <div className="flex-1">
                <Skeleton variant="text" lines={2} />
              </div>
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={MessageSquare}
          size={hasFilters ? 'sm' : undefined}
          bordered={hasFilters}
          title={
            hasFilters
              ? 'No reviews match those filters'
              : 'No reviews yet'
          }
          description={
            hasFilters
              ? 'Try clearing a filter.'
              : 'Use "New review" to seed your first one.'
          }
        />
      ) : (
        <>
          <motion.div
            className="overflow-x-auto rounded-xl border border-line-subtle bg-bg-elevated shadow-md"
            variants={listStagger(0.025)}
            initial="hidden"
            animate="show"
          >
            <table className="w-full min-w-[840px]">
              <thead>
                <tr className="border-b border-line-subtle bg-bg-sunken/60 text-left">
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Product
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Rating
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Author
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Review
                  </th>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Status
                  </th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-ink-tertiary">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <ReviewRow key={r.id} review={r} onEdit={setEditing} />
                ))}
              </tbody>
            </table>
          </motion.div>

          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between gap-2">
              <p className="text-xs text-ink-tertiary">
                <span className="nums font-medium text-ink-secondary">{total}</span> review{total === 1 ? '' : 's'} total
              </p>
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink-tertiary">
                  Page <span className="nums">{page}</span> of <span className="nums">{totalPages}</span>
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  aria-label="Previous page"
                  className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:bg-fill hover:border-line-strong focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  aria-label="Next page"
                  className="grid size-9 place-items-center rounded-md border border-line-subtle text-ink-secondary transition-colors hover:bg-fill hover:border-line-strong focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
                >
                  <ChevronRight className="size-4" />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </AdminPage>
  );
}
