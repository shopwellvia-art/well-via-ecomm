import {
  Sparkles,
  Moon,
  AlarmClock,
  Flower2,
  Sunrise,
  Zap,
  Sun,
  ShieldCheck,
  Apple,
  Heart,
  Brain,
  Droplets,
  Leaf,
} from 'lucide-react';
import { Reveal } from '../luxury/luxe.jsx';

/**
 * "Why You'll Love It" — 4-up benefit grid from `product.benefits`
 * (JSON array of { icon, title, text }).
 *
 * Icon keywords (sleep, calm, energy, glow, immunity, gut, refresh…) map to
 * lucide line icons with Sparkles as the default. Renders null when the
 * product has no usable benefits, so content-less products stay clean.
 */

// Mockup purple accent — no Tailwind token exists for the PDP purple.
const PDP_PURPLE = '#7c4a8c';

const ICON_RULES = [
  [/sleep|moon|night|dream/i, Moon],
  [/calm|relax|zen|stress|anxi/i, Flower2],
  [/energy|boost|power|vital/i, Zap],
  [/glow|skin|radian|beauty/i, Sun],
  [/immun|defen|shield|protect/i, ShieldCheck],
  [/gut|digest|tummy|stomach/i, Apple],
  [/refresh|wake|morning|sunrise/i, Sunrise],
  [/clock|alarm|longer|deep/i, AlarmClock],
  [/heart|love/i, Heart],
  [/focus|brain|mind|memory/i, Brain],
  [/hydrat|water|drop/i, Droplets],
  [/natural|plant|leaf|herb|clean/i, Leaf],
];

function iconFor(benefit) {
  const key = `${benefit.icon || ''} ${benefit.title || ''}`;
  const hit = ICON_RULES.find(([re]) => re.test(key));
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

export function BenefitsGrid({ benefits }) {
  const items = toArray(benefits).filter((b) => b && (b.title || b.text));
  if (items.length === 0) return null;

  return (
    <Reveal as="section" className="mt-10 lg:mt-14">
      <div
        aria-labelledby="pdp-benefits-heading"
        className="rounded-xl3 border border-wline bg-wcard px-6 py-8 lg:px-12 lg:py-12"
      >
        <h2
          id="pdp-benefits-heading"
          className="text-center font-wserif text-[clamp(24px,3vw,34px)] font-medium text-wink"
        >
          Why You&rsquo;ll Love It
        </h2>

        <div className="mt-8 grid grid-cols-2 gap-x-6 gap-y-8 lg:grid-cols-4 lg:gap-x-0 lg:divide-x lg:divide-wline">
          {items.map((benefit, i) => {
            const Icon = iconFor(benefit);
            return (
              <div
                key={`${benefit.title || benefit.text}-${i}`}
                className="flex flex-col items-center text-center lg:px-6"
              >
                <Icon
                  className="size-10"
                  strokeWidth={1.4}
                  style={{ color: PDP_PURPLE }}
                  aria-hidden="true"
                />
                {benefit.title && (
                  <h3 className="mt-3.5 text-[15px] font-semibold leading-snug text-wink">
                    {benefit.title}
                  </h3>
                )}
                {benefit.text && (
                  <p className="mt-1.5 text-[12.5px] leading-[1.6] text-wmuted">
                    {benefit.text}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </Reveal>
  );
}
