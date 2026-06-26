import { forwardRef } from 'react';
import { cva } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Wellness-twin of @/components/ui/Button — identical public API, wellness palette.
 *
 * Variants:
 *   primary     — filled wgreen pill; general primary actions
 *   cta         — filled wgold pill; "Buy Now" / "Place Order" (distinct from cart)
 *   cart        — filled wgreen pill; "Add to Cart" action
 *   secondary   — wcard surface with wline border; lower-emphasis actions
 *   outline     — wgreen-bordered pill; ghost-like with visible edge
 *   ghost       — text-only wmuted; tertiary / inline links
 *   destructive — red-filled; destructive confirmations
 *
 * Props mirror @/components/ui/Button exactly:
 *   loading, block, iconOnly, variant, size, plus all native button props.
 */
const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap select-none',
    'font-medium',
    'transition-[background-color,border-color,box-shadow,opacity,filter] duration-150',
    // wellness: no blue focus-ring — use the .wellvia-root scoped outline style
    'focus-visible:outline-none',
    'disabled:opacity-50 disabled:pointer-events-none disabled:cursor-not-allowed',
    'relative overflow-hidden',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-wgreen text-white rounded-full shadow-sm',
          'hover:bg-wgreen-dark active:brightness-95',
        ],
        cta: [
          'bg-wgold text-wink rounded-full shadow-sm font-semibold uppercase tracking-wide',
          'hover:bg-wgold/90 active:brightness-95',
        ],
        cart: [
          'bg-wgreen text-white rounded-full shadow-sm font-semibold',
          'hover:bg-wgreen-dark active:brightness-95',
        ],
        secondary: [
          'bg-wcard text-wink border border-wline rounded-xl shadow-sm',
          'hover:bg-wpaper hover:border-wgreen/30',
        ],
        outline: [
          'border border-wgreen text-wgreen bg-transparent rounded-full',
          'hover:bg-wgreen/10 active:bg-wgreen/15',
        ],
        ghost: [
          'text-wmuted bg-transparent border-transparent rounded-xl',
          'hover:text-wink hover:bg-wgreen/5',
          'active:bg-wgreen/10',
        ],
        destructive: [
          'bg-red-600 text-white border-transparent rounded-xl shadow-sm',
          'hover:brightness-110 active:brightness-100',
        ],
      },
      size: {
        sm: 'h-9 px-4 text-xs',
        md: 'h-11 px-5 text-sm',
        lg: 'h-[52px] px-7 text-sm',
      },
      block: {
        true: 'w-full',
      },
      iconOnly: {
        true: 'px-0',
      },
    },
    compoundVariants: [
      { size: 'sm', iconOnly: true, class: 'w-9' },
      { size: 'md', iconOnly: true, class: 'w-11' },
      { size: 'lg', iconOnly: true, class: 'w-[52px]' },
    ],
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export const Button = forwardRef(function Button(
  { className, variant, size, block, iconOnly, loading = false, disabled, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size, block, iconOnly }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <>
          {/*
           * Spinner in normal flow so layout width is preserved.
           * Children hidden via opacity-0 while loading.
           */}
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          <span className="opacity-0 select-none" aria-hidden="true">
            {children}
          </span>
        </>
      ) : (
        children
      )}
    </button>
  );
});

export { buttonVariants };
