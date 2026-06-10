import { forwardRef } from 'react';
import { cva } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Primary interactive button.
 *
 * Variants:
 *   primary    — filled accent, main CTAs
 *   secondary  — glass/translucent, secondary actions
 *   outline    — accent-bordered, lower emphasis than secondary
 *   ghost      — text-only, tertiary actions and inline links
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
    'font-semibold rounded-sm',
    // All transitioned properties in one declaration for GPU compositing
    'transition-[transform,background-color,border-color,box-shadow,opacity,filter]',
    'duration-200',
    'focus-visible:focus-ring',
    'disabled:opacity-40 disabled:pointer-events-none disabled:cursor-not-allowed',
    // Prevents text-flicker during loading state change
    'relative overflow-hidden',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-accent text-ink-inverse',
          'hover:bg-accent-hover hover:-translate-y-px hover:shadow-glow-sm',
          'active:bg-accent-press active:translate-y-0 active:shadow-none',
        ],
        secondary: [
          'glass text-ink-primary',
          'hover:border-line-strong hover:-translate-y-px',
          'active:translate-y-0',
        ],
        outline: [
          'border border-accent/50 text-accent bg-transparent',
          'hover:bg-accent/8 hover:border-accent hover:-translate-y-px',
          'active:bg-accent/12 active:translate-y-0',
        ],
        ghost: [
          'text-ink-secondary bg-transparent border-transparent',
          'hover:text-ink-primary hover:bg-fill',
          'active:bg-fill-strong',
        ],
        destructive: [
          'bg-danger text-white border-transparent',
          'hover:brightness-110 hover:-translate-y-px hover:shadow-glow-danger',
          'active:translate-y-0 active:brightness-100 active:shadow-none',
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
