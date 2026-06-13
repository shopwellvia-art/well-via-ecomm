import { Truck, RotateCcw, ShieldCheck, BadgeCheck } from 'lucide-react';

/**
 * Trust badge strip — flat Flipkart/Amazon marketplace style.
 * White card, accent-tinted icons, subtle border dividers. No dark gradient.
 * Data is unchanged.
 */
const BADGES = [
  { icon: Truck, title: 'Free Delivery', sub: 'On every order' },
  { icon: RotateCcw, title: '7-Day Returns', sub: 'Hassle-free' },
  { icon: ShieldCheck, title: 'Secure Payments', sub: 'Encrypted checkout' },
  { icon: BadgeCheck, title: '100% Authentic', sub: 'Sold by ShopWell' },
];

export function TrustRow() {
  return (
    <div className="mt-10">
      <ul className="grid grid-cols-2 divide-x divide-y divide-line-subtle overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated md:grid-cols-4 md:divide-y-0">
        {BADGES.map(({ icon: Icon, title, sub }) => (
          <li key={title} className="flex items-center gap-3 px-4 py-4">
            <span
              className="grid size-10 shrink-0 place-items-center rounded-sm bg-accent/10 text-accent"
              aria-hidden="true"
            >
              <Icon className="size-5" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-ink-primary">{title}</p>
              <p className="text-xs text-ink-secondary">{sub}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
