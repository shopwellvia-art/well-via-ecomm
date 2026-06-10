import { Truck, RefreshCw, ShieldCheck } from 'lucide-react';

const PERKS = [
  {
    icon: Truck,
    title: 'Free delivery',
    body: 'On all orders — no minimum value, all serviceable pincodes.',
  },
  {
    icon: RefreshCw,
    title: '7-day returns',
    body: 'Changed your mind? Returns are free within 7 days of delivery.',
  },
  {
    icon: ShieldCheck,
    title: '1-year warranty',
    body: 'All products backed by ShopWell's quality guarantee.',
  },
];

/**
 * Platform-wide perks strip — three trust pillars displayed as a compact
 * horizontal row (stacks to single column on mobile).
 */
export function OfferStrip() {
  return (
    <section className="mt-6" aria-label="Purchase guarantees">
      <h2 className="sr-only">Delivery and returns</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {PERKS.map(({ icon: Icon, title, body }) => (
          <div
            key={title}
            className="flex items-start gap-3 rounded-md border border-line-subtle bg-bg-elevated p-3 transition-colors hover:border-line-strong"
          >
            <span
              className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-sm bg-accent-soft text-accent"
              aria-hidden="true"
            >
              <Icon className="size-4" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-ink-primary">{title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-secondary">{body}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
