import { Stars } from '@/components/storefront/Icons';
import { FLAVOURS, GOALS } from '@/lib/catalogOptions.js';
import { cn } from '@/lib/utils.js';

export const PRICE_MIN = 100;
export const PRICE_MAX = 2000;

/** Rating filter rows — mockup shows 5★ / 4★+ / 3★+. */
const RATING_OPTIONS = [
  { value: '5', label: '' },
  { value: '4', label: '& above' },
  { value: '3', label: '& above' },
];

const OFFER_OPTIONS = [
  { key: 'combo', label: 'Combo Packs' },
  { key: 'discounted', label: 'Discounted Products' },
  { key: 'best_value', label: 'Best Value' },
];

function Section({ title, action, children }) {
  return (
    <div className="border-b border-wline/70 pb-5 mb-5 last:border-0 last:pb-0 last:mb-0">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-wserif text-[17px] font-semibold text-wink m-0">{title}</h3>
        {action}
      </div>
      {children}
    </div>
  );
}

function CheckRow({ checked, onChange, children }) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5 py-[5px] text-[13.5px] text-wink">
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="size-4 shrink-0 rounded-sm border-wline accent-[#08112C]"
      />
      <span className="min-w-0 truncate">{children}</span>
    </label>
  );
}

export default function FilterSidebar({ filters, onChange }) {
  const toggleIn = (list, value) =>
    list.includes(value) ? list.filter((x) => x !== value) : [...list, value];

  const lo = filters.minPrice ?? PRICE_MIN;
  const hi = filters.maxPrice ?? PRICE_MAX;
  const pct = (v) => ((v - PRICE_MIN) / (PRICE_MAX - PRICE_MIN)) * 100;
  const priceActive = filters.minPrice != null || filters.maxPrice != null;

  return (
    <div className="bg-wcard border border-wline rounded-xl2 p-5">
      {/* ── Goal (categories) ── */}
{/* GOALS are {label, slug} objects (see lib/catalogOptions.js), so the
    slug drives the filter and the label is what renders. */}
<Section title="Goal">
  {GOALS.map((goal) => (
    <CheckRow
      key={goal.slug}
      checked={filters.goals?.includes(goal.slug) ?? false}
      onChange={() =>
        onChange({
          goals: toggleIn(filters.goals || [], goal.slug),
        })
      }
    >
      {goal.label}
    </CheckRow>
  ))}
</Section>

      {/* ── Flavour ── */}
      <Section title="Flavour">
        {FLAVOURS.map((f) => (
          <CheckRow
            key={f}
            checked={filters.flavours.includes(f)}
            onChange={() => onChange({ flavours: toggleIn(filters.flavours, f) })}
          >
            {f}
          </CheckRow>
        ))}
      </Section>

      {/* ── Price ── */}
      <Section
        title="Price"
        action={
          priceActive && (
            <button
              type="button"
              onClick={() => onChange({ minPrice: null, maxPrice: null })}
              className="bg-transparent border-0 p-0 cursor-pointer text-[12px] text-wmuted underline hover:text-wink"
            >
              Clear
            </button>
          )
        }
      >
        {/* Dual-thumb range — two stacked native sliders on one visual track */}
        <div className="relative h-6 mt-1">
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-[3px] rounded-full bg-wline" />
          <div
            className="absolute top-1/2 -translate-y-1/2 h-[3px] rounded-full bg-[#08112C]"
            style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }}
          />
          <input
            type="range"
            min={PRICE_MIN}
            max={PRICE_MAX}
            step={50}
            value={lo}
            aria-label="Minimum price"
            onChange={(e) =>
              onChange({ minPrice: Math.min(Number(e.target.value), hi - 50) })
            }
            className="range-thumb absolute inset-0 w-full appearance-none bg-transparent pointer-events-none"
          />
          <input
            type="range"
            min={PRICE_MIN}
            max={PRICE_MAX}
            step={50}
            value={hi}
            aria-label="Maximum price"
            onChange={(e) =>
              onChange({ maxPrice: Math.max(Number(e.target.value), lo + 50) })
            }
            className="range-thumb absolute inset-0 w-full appearance-none bg-transparent pointer-events-none"
          />
        </div>
        <div className="flex justify-between text-[11.5px] text-wmuted mt-1.5">
          <span>₹{lo.toLocaleString('en-IN')}</span>
          <span>₹{hi.toLocaleString('en-IN')}</span>
        </div>
      </Section>

      {/* ── Offers ── */}
      <Section title="Offers">
        {OFFER_OPTIONS.map((o) => (
          <CheckRow
            key={o.key}
            checked={filters.offers.includes(o.key)}
            onChange={() => onChange({ offers: toggleIn(filters.offers, o.key) })}
          >
            {o.label}
          </CheckRow>
        ))}
      </Section>

      {/* ── Rating ── */}
      <Section title="Rating">
        {RATING_OPTIONS.map((r) => {
          const active = filters.minRating === r.value;
          return (
            <label
              key={r.value}
              className="flex cursor-pointer items-center gap-2.5 py-[5px] text-[13px] text-wink"
            >
              <input
  type="checkbox"
  checked={active}
  onChange={() => onChange({ minRating: active ? null : r.value })}
  className="size-4 shrink-0 rounded-sm border-wline"
  style={{ accentColor: "#08112C" }}
/>
              <span className={cn('inline-flex items-center gap-1 text-wgold')}>
                <Stars count={Number(r.value)} />
              </span>
              {r.label && <span className="text-wmuted text-[12.5px]">{r.label}</span>}
            </label>
          );
        })}
      </Section>

      {/* ── Availability ── */}
      <Section title="Availability">
        <CheckRow
          checked={filters.inStock}
          onChange={() => onChange({ inStock: !filters.inStock })}
        >
          In Stock
        </CheckRow>
      </Section>
    </div>
  );
}
