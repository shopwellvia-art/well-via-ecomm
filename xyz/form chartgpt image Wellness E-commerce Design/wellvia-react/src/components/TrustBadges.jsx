import { CheckCircle } from './Icons';

const DEFAULT_BADGES = [
  'Clinically Reviewed',
  'Vegan & Clean',
  'No Added Sugar',
  'FSSAI Compliant',
];

export default function TrustBadges({ items = DEFAULT_BADGES, className = '' }) {
  return (
    <div className={`flex flex-wrap gap-x-6 gap-y-4 items-center ${className}`}>
      {items.map((label, i) => (
        <div key={i} className="flex items-center gap-2 text-[12.5px] text-muted">
          <CheckCircle size={15} stroke="#183A2E" />
          {label}
        </div>
      ))}
    </div>
  );
}
