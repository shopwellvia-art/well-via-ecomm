import { Truck, RefreshCw, ShieldCheck, Tag } from 'lucide-react';

const OFFERS = [
  {
    icon: Tag,
    label: 'Bank Offer',
    detail: '10% instant discount on HDFC Bank Debit and Credit Cards',
  },
  {
    icon: Truck,
    label: 'Free Delivery',
    detail: 'Free shipping on all orders to all serviceable pincodes',
  },
  {
    icon: RefreshCw,
    label: '7 Day Returns',
    detail: 'Changed your mind? Easy free returns within 7 days',
  },
  {
    icon: ShieldCheck,
    label: '1 Year Warranty',
    detail: 'Backed by ShopWell quality guarantee on all products',
  },
];

/**
 * Amazon-style compact offer strip — labelled rows with icon + bold label +
 * detail text. Flat white card, no shadows or gradients.
 */
export function OfferStrip() {
  return (
    <section className="mt-5" aria-label="Available offers">
      <div className="rounded-sm border border-line-subtle bg-bg-elevated">
        <p className="border-b border-line-subtle px-4 py-2.5 text-sm font-semibold text-ink-primary">
          Available Offers
        </p>
        <ul className="divide-y divide-line-subtle">
          {OFFERS.map(({ icon: Icon, label, detail }) => (
            <li key={label} className="flex items-start gap-3 px-4 py-3">
              <span
                className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-sm bg-success/10 text-success"
                aria-hidden="true"
              >
                <Icon className="size-3.5" />
              </span>
              <p className="text-xs leading-relaxed text-ink-secondary">
                <span className="font-semibold text-ink-primary">{label}: </span>
                {detail}
              </p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
