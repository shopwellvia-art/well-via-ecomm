import { motion } from 'framer-motion';
import { CheckCircle2 } from 'lucide-react';
import { fadeUp } from '@/lib/motion.js';
import { StarRating } from './StarRating.jsx';

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

/**
 * Single review card. Used on the product detail page and (read-only) inside
 * the admin moderation list.
 */
export function ReviewCard({ review }) {
  if (!review) return null;

  const initials = (review.author_display || '?')
    .split(' ')
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();

  return (
    <motion.article
      variants={fadeUp}
      className="border-t border-wline py-5 first:border-t-0 first:pt-0"
    >
      {/* Author row */}
      <div className="flex items-center gap-2.5">
        <span
          className="grid size-8 shrink-0 place-items-center rounded-full bg-wgreen/10 text-xs font-semibold text-wgreen"
          aria-hidden="true"
        >
          {initials}
        </span>
        <div>
          <p className="text-sm font-semibold text-wink leading-none">
            {review.author_display}
          </p>
          <p className="mt-0.5 text-xs text-wmuted">
            {formatDate(review.created_at)}
            {review.is_verified_purchase && (
              <>
                <span className="mx-1.5 text-wmuted" aria-hidden="true">·</span>
                <span className="inline-flex items-center gap-1 text-wgreen">
                  <CheckCircle2 className="size-3" aria-hidden="true" />
                  Verified purchase
                </span>
              </>
            )}
          </p>
        </div>
      </div>

      {/* Rating + title row */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <StarRating value={review.rating} size="sm" />
        {review.title && (
          <h3 className="text-sm font-semibold text-wink">{review.title}</h3>
        )}
      </div>

      {review.body && (
        <p className="mt-2.5 whitespace-pre-line text-sm leading-relaxed text-wmuted">
          {review.body}
        </p>
      )}
    </motion.article>
  );
}
