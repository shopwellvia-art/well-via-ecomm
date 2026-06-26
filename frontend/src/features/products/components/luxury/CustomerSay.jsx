import { CheckCircle2, Star } from 'lucide-react';
import { useProductReviews } from '@/features/reviews/hooks.js';
import { StarRating } from '@/features/reviews/StarRating.jsx';
import { RatingHistogram } from '@/features/reviews/RatingHistogram.jsx';
import { Skeleton } from '@/components/storefront/ui/Skeleton.jsx';

/**
 * "What our customers say" — flat Flipkart/Amazon style.
 * Left: headline rating + histogram. Right: top review cards.
 * Renders nothing when the product has no ratings yet.
 * All data logic unchanged.
 */
export function CustomerSay({ product }) {
  const ratingAvg = Number(product.rating_avg) || 0;
  const ratingCount = Number(product.rating_count) || 0;

  const { data, isLoading } = useProductReviews(product.id, {
    page: 1,
    page_size: 6,
    sort: 'top',
  });

  if (ratingCount === 0) return null;

  const distribution =
    product.rating_distribution || { '1': 0, '2': 0, '3': 0, '4': 0, '5': 0 };
  const cards = (data?.items || []).filter((r) => r.body).slice(0, 3);

  return (
    <section aria-labelledby="customer-say" className="mt-10">
      <h2
        id="customer-say"
        className="mb-4 text-base font-semibold text-wink"
      >
        Customer reviews
      </h2>

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        {/* Rating summary */}
        <div className="flex flex-col justify-center rounded-sm border border-wline bg-wcard p-5 text-center">
          <p className="nums text-5xl font-semibold leading-none text-wink tabular-nums">
            {ratingAvg.toFixed(1)}
          </p>
          <div className="mt-2.5 flex justify-center">
            <StarRating value={ratingAvg} size="lg" />
          </div>
          <p className="mt-1.5 text-xs text-wmuted">
            Based on {ratingCount.toLocaleString()} review{ratingCount === 1 ? '' : 's'}
          </p>
          <div className="mt-4 text-left">
            <RatingHistogram distribution={distribution} total={ratingCount} />
          </div>
        </div>

        {/* Review cards */}
        {isLoading && cards.length === 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-32 rounded-sm" />
            ))}
          </div>
        ) : cards.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {cards.map((r) => (
              <div
                key={r.id}
                className="flex flex-col rounded-sm border border-wline bg-wcard p-4"
              >
                <div className="flex items-center gap-2.5">
                  <span className="grid size-9 shrink-0 place-items-center rounded-full bg-wgreen/10 text-sm font-semibold text-wgreen">
                    {(r.author_display || '?').charAt(0).toUpperCase()}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-wink">
                      {r.author_display}
                    </p>
                    {r.is_verified_purchase && (
                      <span className="inline-flex items-center gap-1 text-xs text-wgreen">
                        <CheckCircle2 className="size-3" aria-hidden="true" />
                        Verified purchase
                      </span>
                    )}
                  </div>
                </div>

                <div className="mt-2.5">
                  <StarRating value={r.rating} size="sm" />
                </div>

                {r.title && (
                  <h3 className="mt-2 text-sm font-semibold text-wink">{r.title}</h3>
                )}
                <p className="mt-1 line-clamp-4 text-xs leading-relaxed text-wmuted">
                  {r.body}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
