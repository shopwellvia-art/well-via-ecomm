import { createPortal } from 'react-dom';
import { create } from 'zustand';
import { CheckCircle2, AlertTriangle, Info, X } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Minimal global toast/announcer — no extra npm deps, just a tiny zustand store
 * plus a portal-mounted, screen-reader-friendly live region. Mount <Toaster />
 * once near the app root; fire toasts imperatively from anywhere via `toast`:
 *
 *   toast.error(err?.response?.data?.error?.message || 'Something went wrong.');
 *   toast.success('Added to cart');
 *
 * Errors are announced assertively, everything else politely.
 */

const DEFAULT_DURATION = 5000;
let nextId = 1;

export const useToastStore = create((set, get) => ({
  toasts: [], // [{ id, message, variant }]

  push: ({ message, variant = 'info', duration = DEFAULT_DURATION }) => {
    if (!message) return null;
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, message, variant }] }));
    if (duration > 0) {
      // Auto-dismiss. Timers are cleared implicitly when dismiss() removes the
      // toast; a late-firing timer for an already-removed id is a no-op.
      setTimeout(() => get().dismiss(id), duration);
    }
    return id;
  },

  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Imperative helper — callable from event handlers / mutation onError (no hook). */
export const toast = {
  show: (message, opts) => useToastStore.getState().push({ message, ...opts }),
  success: (message, opts) =>
    useToastStore.getState().push({ message, variant: 'success', ...opts }),
  error: (message, opts) =>
    useToastStore.getState().push({ message, variant: 'error', ...opts }),
  info: (message, opts) =>
    useToastStore.getState().push({ message, variant: 'info', ...opts }),
};

const VARIANTS = {
  success: { Icon: CheckCircle2, accent: 'text-wgreen', ring: 'border-wgreen/30' },
  error: { Icon: AlertTriangle, accent: 'text-red-500', ring: 'border-red-200' },
  info: { Icon: Info, accent: 'text-wgold', ring: 'border-wline' },
};

function ToastItem({ toast: t, onDismiss }) {
  const { Icon, accent, ring } = VARIANTS[t.variant] || VARIANTS.info;
  return (
    <div
      className={cn(
        'pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl2 border bg-wcard px-4 py-3',
        'shadow-[0_18px_40px_-20px_rgba(20,29,24,0.5)] animate-rise', // animate-rise is disabled under prefers-reduced-motion (see global.css)
        ring,
      )}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', accent)} aria-hidden="true" />
      <p className="min-w-0 flex-1 text-sm leading-snug text-wink">{t.message}</p>
      <button
        type="button"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(t.id)}
        className="-mr-1 -mt-0.5 shrink-0 rounded-full p-1 text-wmuted transition-colors hover:text-wink"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}

export default function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (typeof document === 'undefined') return null;

  // Errors go in an assertive region (interrupts the screen reader); the rest
  // in a polite region. Two regions, one visual stack in the corner.
  const assertive = toasts.filter((t) => t.variant === 'error');
  const polite = toasts.filter((t) => t.variant !== 'error');

  return createPortal(
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:items-end sm:pr-6">
      <div
        aria-live="assertive"
        aria-atomic="false"
        className="flex w-full flex-col items-center gap-2 sm:items-end"
      >
        {assertive.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
      <div
        aria-live="polite"
        aria-atomic="false"
        className="flex w-full flex-col items-center gap-2 sm:items-end"
      >
        {polite.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </div>,
    document.body,
  );
}
