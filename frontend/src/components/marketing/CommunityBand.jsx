import { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Mail, Check } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Newsletter band — solid blue full-width strip (bg-accent).
 * White heading + subtitle on the left, email input + Subscribe button on the right.
 * Matches ShopFlow h6-featured.png bottom strip. Form logic unchanged.
 */
export default function CommunityBand() {
  const reduce = useReducedMotion();
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  function onSubmit(e) {
    e.preventDefault();
    const value = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      setError('Please enter a valid email address.');
      return;
    }
    setError('');
    setSubmitted(true);
    setEmail('');
    window.setTimeout(() => setSubmitted(false), 4000);
  }

  return (
    <section className="mx-auto mt-3 max-w-content px-4 sm:px-6">
      <motion.div
        initial={reduce ? false : { opacity: 0, y: 16 }}
        whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.3 }}
        transition={{ duration: 0.4 }}
        className="overflow-hidden rounded-sm bg-bg-elevated shadow-sm"
      >
        <div className="flex flex-col items-center gap-4 px-6 py-8 text-center sm:flex-row sm:justify-between sm:text-left sm:px-8">
          {/* Left — icon + copy */}
          <div className="flex items-center gap-3">
            <span className="grid size-11 shrink-0 place-items-center rounded-sm bg-accent/10 text-accent">
              <Mail className="size-[22px]" aria-hidden="true" />
            </span>
            <div>
              <h3 className="text-base font-bold text-ink-primary">
                Get exclusive deals in your inbox
              </h3>
              <p className="mt-0.5 text-sm text-ink-tertiary">
                No spam — unsubscribe anytime.
              </p>
            </div>
          </div>

          {/* Right — form */}
          <form
            onSubmit={onSubmit}
            noValidate
            aria-label="Subscribe to newsletter"
            className="relative flex w-full max-w-md shrink-0 flex-col gap-1.5 sm:flex-row sm:items-start"
          >
            <label htmlFor="community-email" className="sr-only">
              Email address
            </label>
            <input
              id="community-email"
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (error) setError('');
              }}
              placeholder="you@example.com"
              autoComplete="email"
              aria-invalid={error ? 'true' : undefined}
              aria-describedby="community-email-msg"
              className={cn(
                'h-11 flex-1 rounded-sm border bg-bg-sunken px-4 text-sm text-ink-primary outline-none',
                'placeholder:text-ink-tertiary',
                'transition-colors duration-150',
                error
                  ? 'border-danger/60'
                  : 'border-line-strong focus:border-accent',
              )}
            />

            <button
              type="submit"
              disabled={submitted}
              className={cn(
                'inline-flex h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-sm',
                'bg-accent px-6 text-sm font-semibold text-white',
                'transition-colors duration-150 hover:bg-accent-hover',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              )}
            >
              {submitted ? (
                <>
                  <Check className="size-4" aria-hidden="true" />
                  Subscribed
                </>
              ) : (
                'Subscribe'
              )}
            </button>

            <p
              id="community-email-msg"
              role="status"
              aria-live="polite"
              className={cn(
                'absolute -bottom-5 left-0 min-h-[1rem] text-xs sm:bottom-auto sm:top-full sm:mt-1',
                error ? 'text-danger' : submitted ? 'text-rating' : 'sr-only',
              )}
            >
              {error || (submitted ? "You're in! Check your inbox soon." : '')}
            </p>
          </form>
        </div>
      </motion.div>
    </section>
  );
}
