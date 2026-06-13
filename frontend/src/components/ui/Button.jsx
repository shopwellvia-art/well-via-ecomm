import { forwardRef } from 'react';
import { cva } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Primary interactive button — Flipkart/marketplace flat style.
 *
 * Variants:
 *   primary     — filled Flipkart blue, general primary actions
 *   cta         — filled orange, the "Buy Now" / "Place Order" action
 *   cart        — filled amber/yellow, the "Add to Cart" action
 *   secondary   — white with a hairline border + subtle shadow
 *   outline     — blue-bordered, lower emphasis
 *   ghost       — text-only, tertiary actions and inline links
 *   destructive — danger-filled, destructive confirmations
 *
 * Props:
 *   loading    — shows spinner and sets aria-busy; disables the button
 *   block      — full-width (w-full)
 *   iconOnly   — square aspect for icon-only buttons (no horizontal padding)
 */
const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap select-none',
    'font-medium rounded-xs',
    'transition-[background-color,border-color,box-shadow,opacity,filter] duration-150',
    'focus-visible:focus-ring',
    'disabled:opacity-50 disabled:pointer-events-none disabled:cursor-not-allowed',
    // Prevents text-flicker during loading state change
    'relative overflow-hidden',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-accent text-ink-inverse shadow-sm',
          'hover:bg-accent-hover active:bg-accent-press',
        ],
        cta: [
          'bg-cta text-white shadow-sm font-semibold uppercase tracking-wide',
          'hover:bg-cta-hover active:bg-cta-press',
        ],
        cart: [
          'bg-accent text-white shadow-sm font-semibold',
          'hover:bg-accent-hover active:bg-accent-press',
        ],
        secondary: [
          'bg-bg-elevated text-ink-primary border border-line-subtle shadow-sm',
          'hover:bg-bg-sunken hover:border-line-strong',
        ],
        outline: [
          'border border-accent text-accent bg-bg-elevated',
          'hover:bg-accent/8 active:bg-accent/12',
        ],
        ghost: [
          'text-ink-secondary bg-transparent border-transparent',
          'hover:text-ink-primary hover:bg-fill',
          'active:bg-fill-strong',
        ],
        destructive: [
          'bg-danger text-white border-transparent shadow-sm',
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
           * Spinner sits in normal flow so it doesn't shift layout.
           * Children are hidden (opacity-0) while loading, preserving button width.
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
