import { Truck, RotateCcw, ShieldCheck, BadgeCheck } from 'lucide-react';

/**
 * Trust badge strip — wellness palette.
 * wcard surface, wgreen-tinted icons, wline borders. No dark gradient.
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
      <ul className="grid grid-cols-2 divide-x divide-y divide-wline overflow-hidden rounded-sm border border-wline bg-wcard md:grid-cols-4 md:divide-y-0">
        {BADGES.map(({ icon: Icon, title, sub }) => (
          <li key={title} className="flex items-center gap-3 px-4 py-4">
            <span
              className="grid size-10 shrink-0 place-items-center rounded-sm bg-wgreen/10 text-wgreen"
              aria-hidden="true"
            >
              <Icon className="size-5" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-wink">{title}</p>
              <p className="text-xs text-wmuted">{sub}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
