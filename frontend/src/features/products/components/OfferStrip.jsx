import { Tag } from 'lucide-react';

const OFFERS = [
  {
    label: 'Bank Offer',
    detail: '10% instant discount on HDFC Bank Debit and Credit Cards',
  },
  {
    label: 'No-cost EMI',
    detail: 'From ₹999/month on orders above ₹5,000',
  },
  {
    label: 'Free delivery',
    detail: 'On all serviceable pincodes',
  },
  {
    label: '1 Year Warranty',
    detail: 'Backed by ShopWell quality guarantee on all products',
  },
];

/**
 * "Available offers" card — Flipkart-style rows: green tag icon + bold label + detail.
 * Flat white card with a header title and border separators.
 */
export function OfferStrip() {
  return (
    <section className="mt-5" aria-label="Available offers">
      <div className="rounded-lg border border-line-subtle">
        <h2 className="border-b border-line-subtle px-4 py-2.5 text-sm font-bold text-ink-primary">
          Available offers
        </h2>
        <ul className="space-y-2.5 px-4 py-3.5 text-sm">
          {OFFERS.map(({ label, detail }) => (
            <li key={label} className="flex gap-2.5">
              <span className="mt-0.5 shrink-0 text-rating" aria-hidden="true">
                <Tag className="size-4" strokeWidth={1.8} />
              </span>
              <span>
                <b className="font-semibold text-ink-primary">{label} </b>
                {detail}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
