import { useState } from 'react';
import { motion } from 'framer-motion';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { scaleIn } from '@/lib/motion.js';
import { StarRating } from './StarRating.jsx';
import { useCreateReview } from './hooks.js';

export function WriteReviewForm({ productId, onCancel, onSubmitted }) {
  const [rating, setRating] = useState(0);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [error, setError] = useState(null);
  const create = useCreateReview();

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (rating < 1) {
      setError('Pick a star rating before submitting.');
      return;
    }
    try {
      await create.mutateAsync({
        productId,
        data: { rating, title: title.trim() || null, body: body.trim() || null },
      });
      onSubmitted?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not submit your review.');
    }
  }

  return (
    <motion.form
      onSubmit={handleSubmit}
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className="mb-6 rounded-lg border border-line-subtle bg-bg-elevated p-5 shadow-md"
    >
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-h3 tracking-tight text-ink-primary">Write your review</h3>
        <button
          type="button"
          aria-label="Cancel review"
          onClick={onCancel}
          className="grid size-8 place-items-center rounded-sm text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      {/* Star rating */}
      <div>
        <p className="mb-2 text-sm font-medium text-ink-secondary" id="rating-label">
          Overall rating
          <span className="ml-1 text-danger" aria-hidden="true">*</span>
        </p>
        <StarRating
          value={rating}
          onChange={setRating}
          size="lg"
          ariaLabel="Select your star rating"
        />
      </div>

      {/* Headline */}
      <div className="mt-4">
        <Input
          label="Headline"
          placeholder="Sums up your experience in one line"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={160}
        />
      </div>

      {/* Body */}
      <div className="mt-3">
        <Textarea
          label="Your review"
          placeholder="What did you like or dislike? Was it as described?"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={4}
          maxRows={8}
        />
      </div>

      {error && (
        <p role="alert" className="mt-2.5 text-xs text-danger shadow-glow-danger/0">
          {error}
        </p>
      )}

      <div className="mt-5 flex items-center justify-end gap-2.5 border-t border-line-subtle pt-4">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={create.isPending}>
          Cancel
        </Button>
        <Button type="submit" loading={create.isPending}>
          Submit review
        </Button>
      </div>
    </motion.form>
  );
}
