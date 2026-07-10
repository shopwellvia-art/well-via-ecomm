import { Candy, CalendarDays, Cherry, Leaf, Sparkles } from 'lucide-react';
import { Reveal } from '../luxury/luxe.jsx';

/**
 * Pack pill strip — "30 Gummies | 15 Servings | Berry Flavour".
 *
 * Renders `product.highlights` (JSON array of strings) as chips on a single
 * lavender pill. Each chip gets a small keyword-matched icon and splits its
 * first word onto a bold top line (mirrors the mockup's "30 / Gummies"
 * stacking). Renders null when there are no highlights.
 */

// Mockup purple accents — no Tailwind token exists for the PDP purple.
const PDP_PURPLE = '#7c4a8c';
const PDP_LAVENDER = '#ede4f3';

const ICON_RULES = [
  [/gumm|tablet|capsule|chew|candy/i, Candy],
  [/serving|dose|supply|day/i, CalendarDays],
  [/flavour|flavor|berry|mango|orange|lemon|fruit|currant/i, Cherry],
  [/vegan|natural|sugar|gelatin|plant/i, Leaf],
];

function iconFor(label) {
  const hit = ICON_RULES.find(([re]) => re.test(String(label)));
  return hit ? hit[1] : Sparkles;
}

/** Coerce a JSON column that may arrive as an array or a JSON string. */
function toArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

export function PackChips({ highlights }) {
  const items = toArray(highlights)
    .map((h) => String(h ?? '').trim())
    .filter(Boolean);
  if (items.length === 0) return null;

  return (
    <Reveal as="section" className="mt-8 lg:mt-10">
      <ul
        aria-label="Pack highlights"
        className="mx-auto flex max-w-[760px] flex-wrap items-center justify-center gap-x-8 gap-y-3 rounded-xl3 px-8 py-4 sm:rounded-full"
        style={{ backgroundColor: PDP_LAVENDER }}
      >
        {items.map((label, i) => {
          const Icon = iconFor(label);
          const [first, ...rest] = label.split(/\s+/);
          return (
            <li key={`${label}-${i}`} className="flex items-center gap-4">
              {i > 0 && (
                <span
                  aria-hidden="true"
                  className="hidden h-9 w-px sm:block"
                  style={{ backgroundColor: 'rgba(124, 74, 140, 0.25)' }}
                />
              )}
              <span className="flex items-center gap-2.5">
                <Icon
                  className="size-7 shrink-0"
                  strokeWidth={1.6}
                  style={{ color: PDP_PURPLE }}
                  aria-hidden="true"
                />
                {rest.length > 0 ? (
                  <span className="flex flex-col leading-tight">
                    <span className="text-[15px] font-semibold text-wink">{first}</span>
                    <span className="text-[13px] text-wink/75">{rest.join(' ')}</span>
                  </span>
                ) : (
                  <span className="text-[15px] font-semibold text-wink">{first}</span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </Reveal>
  );
}
