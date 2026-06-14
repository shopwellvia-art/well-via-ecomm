import { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Mail, ArrowRight, Check } from 'lucide-react';
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
        className="overflow-hidden rounded-sm bg-accent"
      >
        <div className="flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8 sm:py-6">
          {/* Left — copy */}
          <div className="flex items-center gap-4">
            <Mail className="size-8 shrink-0 text-white/80" aria-hidden="true" />
            <div>
              <h2 className="text-base font-bold text-white">
                Get exclusive deals in your inbox
              </h2>
              <p className="mt-0.5 text-sm text-white/75">
                Subscribe and never miss a sale. No spam — unsubscribe anytime.
              </p>
            </div>
          </div>

          {/* Right — form */}
          <form
            onSubmit={onSubmit}
            noValidate
            aria-label="Subscribe to newsletter"
            className="relative flex shrink-0 flex-col gap-1.5 sm:flex-row sm:items-start"
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
                'h-10 w-full rounded-xs border bg-white/10 px-3 text-sm text-white',
                'placeholder:text-white/50',
                'transition-colors duration-150',
                'focus:outline-none focus-visible:bg-white/15',
                error
                  ? 'border-danger/60'
                  : 'border-white/30 focus-visible:border-white/60',
                'sm:w-60',
              )}
            />

            <button
              type="submit"
              disabled={submitted}
              className={cn(
                'inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-xs',
                'bg-white px-5 text-sm font-semibold text-accent',
                'transition-colors duration-150 hover:bg-white/90',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white',
              )}
            >
              {submitted ? (
                <>
                  <Check className="size-4" aria-hidden="true" />
                  Subscribed
                </>
              ) : (
                <>
                  Subscribe
                  <ArrowRight className="size-4" aria-hidden="true" />
                </>
              )}
            </button>

            <p
              id="community-email-msg"
              role="status"
              aria-live="polite"
              className={cn(
                'absolute -bottom-5 left-0 min-h-[1rem] text-xs sm:bottom-auto sm:top-full sm:mt-1',
                error ? 'text-white/90' : submitted ? 'text-white/80' : 'sr-only',
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
