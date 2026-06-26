import { CheckCircle } from '@/components/storefront/Icons';

/**
 * TrustBadges — a horizontal list of trust/credential badges.
 *
 * Props:
 *   items     — array of label strings (defaults to the Wellvia brand claims)
 *   className — extra Tailwind classes to add to the wrapper
 */
const DEFAULT_ITEMS = [
  'Clinically Reviewed',
  'Vegan & Clean',
  'No Added Sugar',
  'FSSAI Compliant',
];

export default function TrustBadges({ items = DEFAULT_ITEMS, className = '' }) {
  return (
    <div className={`flex flex-wrap gap-x-6 gap-y-4 items-center ${className}`}>
      {items.map((label, i) => (
        <div key={i} className="flex items-center gap-2 text-[12.5px] text-wmuted">
          <CheckCircle size={15} stroke="#183A2E" />
          {label}
        </div>
      ))}
    </div>
  );
}
