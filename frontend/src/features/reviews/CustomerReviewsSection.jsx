import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { MessageSquare, PencilLine, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { listStagger, fadeUp, fadeIn } from '@/lib/motion.js';
import { useProductReviews } from './hooks.js';
import { StarRating } from './StarRating.jsx';
import { RatingHistogram } from './RatingHistogram.jsx';
import { ReviewCard } from './ReviewCard.jsx';
import { WriteReviewForm } from './WriteReviewForm.jsx';

const PAGE_SIZE = 10;

/**
 * Full customer reviews section. Two-column layout on >=lg:
 *   - Left rail: aggregate stars + count + animated histogram + write button
 *   - Right rail: sort control + staggered paginated reviews
 *
 * Stacks vertically on smaller screens.
 */
export function CustomerReviewsSection({ product }) {
  const user = useAuthStore((s) => s.user);
  const [sort, setSort] = useState('top');
  const [page, setPage] = useState(1);
  const [writing, setWriting] = useState(false);

  const { data, isLoading, isError, refetch } = useProductReviews(product.id, {
    page,
    page_size: PAGE_SIZE,
    sort,
  });

  const items = data?.items || [];
  const total = data?.total || 0;
  const ratingAvg = Number(product.rating_avg) || 0;
  const ratingCount = Number(product.rating_count) || 0;
  const distribution =
    product.rating_distribution || { '1': 0, '2': 0, '3': 0, '4': 0, '5': 0 };

  const userReviewed = !!user && items.some((r) => r.user_id === user.id);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <motion.section
      id="reviews"
      aria-labelledby="reviews-heading"
      variants={fadeIn}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount: 0.1 }}
      className="mt-16 border-t border-line-subtle pt-12"
    >
      <h2 id="reviews-heading" className="text-h2 tracking-tight text-ink-primary">
        Customer reviews
      </h2>

      <div className="mt-8 grid gap-10 lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* ── Left rail — aggregate ── */}
        <aside aria-label="Rating summary" className="lg:sticky lg:top-24 lg:self-start">
          {ratingCount === 0 ? (
            <p className="text-sm text-ink-secondary">
              No reviews yet. Be the first to share your thoughts.
            </p>
          ) : (
            <>
              <div className="flex items-end gap-3">
                <p className="nums text-5xl font-semibold leading-none tracking-tight text-ink-primary">
                  {ratingAvg.toFixed(1)}
                </p>
                <div className="pb-1">
                  <StarRating value={ratingAvg} size="md" />
                  <p className="mt-1 text-xs text-ink-tertiary">
                    {ratingCount.toLocaleString()} rating{ratingCount === 1 ? '' : 's'}
                  </p>
                </div>
              </div>
              <div className="mt-5">
                <RatingHistogram distribution={distribution} total={ratingCount} />
              </div>
            </>
          )}

          {/* Write review CTA */}
          <div className="mt-6 rounded-sm border border-line-subtle bg-bg-elevated p-4">
            <p className="text-sm font-semibold text-ink-primary">Review this product</p>
            <p className="mt-1 text-xs text-ink-tertiary">
              Share your thoughts with other customers.
            </p>
            {!user ? (
              <Link to={`/login?next=/products/${product.id}`} className="mt-3 block">
                <Button size="sm" variant="secondary" className="w-full">
                  Sign in to write a review
                </Button>
              </Link>
            ) : userReviewed ? (
              <p className="mt-3 text-xs text-ink-tertiary">
                You&apos;ve already reviewed this product.
              </p>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                className="mt-3 w-full"
                onClick={() => setWriting(true)}
                disabled={writing}
              >
                <PencilLine className="size-4" aria-hidden="true" />
                Write a customer review
              </Button>
            )}
          </div>
        </aside>

        {/* ── Right rail — review list ── */}
        <div className="min-w-0">
          {writing && (
            <WriteReviewForm
              productId={product.id}
              onCancel={() => setWriting(false)}
              onSubmitted={() => {
                setWriting(false);
                setSort('newest');
                setPage(1);
              }}
            />
          )}

          {/* Sort + count row */}
          <div className="flex items-center justify-between gap-3 border-b border-line-subtle pb-4">
            <p className="text-sm text-ink-secondary">
              {isLoading ? (
                <Skeleton className="inline-block h-3.5 w-32" />
              ) : ratingCount > 0 ? (
                <>
                  Showing{' '}
                  <span className="nums font-medium text-ink-primary">{items.length}</span>
                  {' '}of{' '}
                  <span className="nums font-medium text-ink-primary">{total}</span>
                  {' '}review{total === 1 ? '' : 's'}
                </>
              ) : (
                'No reviews yet'
              )}
            </p>
            <SortMenu value={sort} onChange={(v) => { setSort(v); setPage(1); }} />
          </div>

          {/* Review list */}
          <div className="mt-4">
            {isLoading ? (
              <div className="flex flex-col gap-5">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="flex flex-col gap-2.5 border-t border-line-subtle pt-5 first:border-t-0 first:pt-0">
                    <div className="flex items-center gap-2.5">
                      <Skeleton variant="circle" className="size-8 rounded-full" />
                      <div className="flex flex-col gap-1.5">
                        <Skeleton className="h-3 w-24" />
                        <Skeleton className="h-2.5 w-32" />
                      </div>
                    </div>
                    <Skeleton className="h-3 w-20" />
                    <Skeleton variant="text" lines={2} className="w-full" />
                  </div>
                ))}
              </div>
            ) : isError ? (
              <div className="py-8 text-center text-sm text-danger">
                Couldn&apos;t load reviews.{' '}
                <button onClick={() => refetch()} className="underline">
                  Retry
                </button>
              </div>
            ) : items.length === 0 ? (
              <div className="flex flex-col items-center gap-3 rounded-sm border border-dashed border-line-subtle py-14 text-center">
                <span className="grid size-10 place-items-center rounded-full bg-fill">
                  <MessageSquare className="size-5 text-ink-tertiary" aria-hidden="true" />
                </span>
                <div>
                  <p className="text-sm font-medium text-ink-secondary">No reviews yet</p>
                  <p className="mt-0.5 text-xs text-ink-tertiary">Be the first to leave one.</p>
                </div>
              </div>
            ) : (
              <motion.ul
                variants={listStagger(0.06)}
                initial="hidden"
                animate="show"
                className="divide-y divide-line-subtle"
              >
                {items.map((r) => (
                  <li key={r.id}>
                    <ReviewCard review={r} />
                  </li>
                ))}
              </motion.ul>
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-8 flex items-center justify-center gap-3 border-t border-line-subtle pt-6">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                <ChevronLeft className="size-4" aria-hidden="true" />
                Previous
              </Button>
              <span className="nums text-xs text-ink-tertiary">
                Page {page} of {totalPages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
              >
                Next
                <ChevronRight className="size-4" aria-hidden="true" />
              </Button>
            </div>
          )}
        </div>
      </div>
    </motion.section>
  );
}

function SortMenu({ value, onChange }) {
  return (
    <label className="inline-flex items-center gap-2 text-xs text-ink-secondary">
      Sort by
      <span className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            'h-8 appearance-none rounded-sm border border-line-subtle bg-bg-sunken pl-3 pr-7 text-xs text-ink-primary',
            'transition-colors hover:border-line-strong focus-visible:border-accent focus-visible:outline-none',
          )}
        >
          <option value="top">Top reviews</option>
          <option value="newest">Most recent</option>
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-ink-tertiary"
          aria-hidden="true"
        />
      </span>
    </label>
  );
}
